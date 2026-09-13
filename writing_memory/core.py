"""Durable task records, immutable document snapshots and conservative paragraph links.

A task is the transaction boundary: its turns, versions and relations are replaced
together. Git is a second, retryable persistence step; a failed commit never rolls
back the already saved record.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import subprocess

from .util import atomic_json, atomic_write, digest, file_lock, new_id, read_json, utc_now, validate_id


MARKER = ".writing-memory.json"
ACCEPTANCE_STATES = {"pending", "accepted", "rejected", "final", "withdrawn"}
ASSET_ROOTS = ("tasks", "raw", "sources", "imports", "extractions", "experiences",
               "releases", "usage", "exports", "prompt_snapshots", "reviews")
LOCAL_IGNORE_PATTERNS = (".lock", "git_sync/", "outbox/", ".sync-lock/", "*.tmp", ".DS_Store", "__pycache__/")


def split_paragraphs(content: str) -> list[dict]:
    """Split at blank lines, preserving fenced code and exact character offsets."""
    paragraphs = []
    offset = 0
    start = None
    fence = None
    for line in content.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        marker = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$", bare)
        if start is None and bare.strip():
            start = offset
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
        elif marker:
            fence = (marker[1][0], len(marker[1]))
        elif not bare.strip() and start is not None:
            end = offset
            while end > start and content[end - 1] in "\r\n":
                end -= 1
            paragraphs.append({"text": content[start:end], "start": start, "end": end})
            start = None
        offset += len(line)
    if start is not None:
        end = len(content)
        while end > start and content[end - 1] in "\r\n":
            end -= 1
        paragraphs.append({"text": content[start:end], "start": start, "end": end})
    return [{**paragraph, "index": i + 1} for i, paragraph in enumerate(paragraphs)]


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        command = ["git", "-C", str(self.root), "-c", "core.hooksPath=/dev/null", *args]
        result = subprocess.run(command, text=True, capture_output=True, timeout=60)
        if check and result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip() or "Git command failed")
        return result

    def _require_init(self):
        marker = self.root / MARKER
        if not marker.is_file() or read_json(marker).get("application") != "writing-memory":
            raise ValueError(f"尚未初始化资产目录：{self.root}")
        top = self._git("rev-parse", "--show-toplevel").stdout.strip()
        if Path(top).resolve() != self.root:
            raise ValueError("资产目录必须有自己的 Git 仓库，不能使用外层仓库")

    def init(self) -> dict:
        if self.root.exists() and not (self.root / MARKER).exists():
            unexpected = [path.name for path in self.root.iterdir() if path.name != ".lock"]
            if unexpected:
                raise ValueError("拒绝初始化已有内容的目录；请选择新的独立资产目录：" + ", ".join(sorted(unexpected)))
        self.root.mkdir(parents=True, exist_ok=True)
        with file_lock(self.root):
            marker_path = self.root / MARKER
            if marker_path.exists():
                self._require_init()
                self._ensure_gitignore()
                return self.status()
            unexpected = [path.name for path in self.root.iterdir() if path.name != ".lock"]
            if unexpected:
                raise ValueError("拒绝初始化已有内容的目录；请选择新的独立资产目录：" + ", ".join(sorted(unexpected)))
            self._git("init", "--initial-branch=main")
            for name in ("tasks", "experiences", "raw", "exports", "git_sync"):
                (self.root / name).mkdir()
            atomic_json(marker_path, {"application": "writing-memory", "schema_version": 1, "created_at": utc_now()})
            self.commit_path(marker_path, "Initialize writing memory assets")
            self._ensure_gitignore()
            return self.status()

    def _ensure_gitignore(self):
        path = self.root / ".gitignore"
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        missing = [pattern for pattern in LOCAL_IGNORE_PATTERNS if pattern not in previous.splitlines()]
        if missing:
            content = previous + ("\n" if previous and not previous.endswith("\n") else "") + "\n".join(missing) + "\n"
            atomic_write(path, content)
            self.commit_path(path, "Ignore local locks and delivery retry state")

    def _task_path(self, task_id: str) -> Path:
        return self.root / "tasks" / (validate_id(task_id) + ".json")

    def commit_path(self, path: Path, message: str) -> dict:
        """Commit exactly one owned asset, preserving unrelated staged changes."""
        path = Path(path)
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        try:
            relative = path.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ValueError("只能提交资产目录内部文件") from exc
        if relative in {".", ""}:
            raise ValueError("不能提交整个资产目录；必须明确指定资产文件或资料包")
        if Path(relative).parts[0] in {".git", "outbox", "git_sync"}:
            raise ValueError("不能提交 Git 内部文件或本地投递状态")
        with file_lock(self.root):
            pending_path = self.root / "git_sync" / (digest(relative)[:24] + ".json")
            try:
                self._require_init()
                self._git("add", "--", relative)
                changes = self._git("diff", "--cached", "--quiet", "--", relative, check=False)
                if changes.returncode == 0:
                    commit = self._git("rev-parse", "HEAD", check=False)
                    result = {"status": "unchanged", "commit": commit.stdout.strip() if commit.returncode == 0 else None, "path": relative}
                else:
                    self._git("-c", "user.name=Writing Memory", "-c", "user.email=writing-memory@localhost", "-c", "commit.gpgsign=false", "commit", "--only", "-m", message, "--", relative)
                    result = {"status": "committed", "commit": self._git("rev-parse", "HEAD").stdout.strip(), "path": relative}
                pending_path.unlink(missing_ok=True)
                return result
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
                result = {"status": "pending", "commit": None, "path": relative, "error": str(exc), "last_attempt": utc_now(), "message": message}
                atomic_json(pending_path, result)
                return result

    def retry_git(self) -> list[dict]:
        with file_lock(self.root):
            self._require_init()
            return [self.commit_path(self.root / record["path"], record.get("message", "Retry saved writing record"))
                    for record in self._pending_commits()]

    def _pending_commits(self) -> list[dict]:
        """Reconcile disk with Git, including a kill between atomic save and commit."""
        records = [read_json(path) for path in sorted((self.root / "git_sync").glob("*.json"))]
        known = {record["path"] for record in records}
        changed = self._git("diff", "--name-only", "-z", "HEAD", "--", *ASSET_ROOTS, check=False)
        untracked = self._git("ls-files", "--others", "--exclude-standard", "-z", "--", *ASSET_ROOTS, check=False)
        for relative in sorted(set(changed.stdout.split("\0") + untracked.stdout.split("\0"))):
            if not relative:
                continue
            parts = Path(relative).parts
            path = self.root / relative
            covered = any(relative == parent or relative.startswith(parent.rstrip("/") + "/") for parent in known)
            # Ignore atomic-write leftovers and links: only named, regular assets
            # inside the fixed owned roots may be recovered automatically.
            if parts[0] not in ASSET_ROOTS or any(part.startswith(".") for part in parts) or path.is_symlink():
                continue
            try:
                path.resolve().relative_to(self.root)
            except ValueError:
                continue
            if not covered and path.is_file():
                records.append({"status": "pending", "commit": None, "path": relative,
                                "error": "资料已保存在本地，但尚未提交到 Git；可执行补提交", "recovered_from_disk": True})
        return records

    def _save(self, task: dict, message: str) -> dict:
        task["updated_at"] = utc_now()
        path = self._task_path(task["id"])
        atomic_json(path, task)
        return self.commit_path(path, message)

    def create_task(self, title, purpose, audience, document_type, document_path=None, topic="") -> dict:
        with file_lock(self.root):
            self._require_init()
            if not str(title).strip():
                raise ValueError("任务标题不能为空")
            task = {"schema_version": 1, "id": new_id("task"), "title": title,
                    "purpose": purpose, "audience": audience, "document_type": document_type,
                    "topic": topic, "document_path": str(Path(document_path).expanduser().resolve()) if document_path else None,
                    "created_at": utc_now(), "sessions": [], "turns": [], "versions": [], "relations": []}
            if document_path:
                document = Path(task["document_path"])
                if document.exists():
                    if not document.is_file():
                        raise ValueError("绑定文稿必须是文件")
                    task["versions"].append(self._make_version(task, document.read_text(encoding="utf-8"), source="initial"))
                else:
                    task["document_sync"] = {"status": "missing", "path": str(document)}
            sync = self._save(task, f"Create task {task['id']}")
            return {**task, "git_sync": sync}

    def get_task(self, task_id) -> dict:
        self._require_init()
        path = self._task_path(task_id)
        if not path.is_file():
            raise ValueError(f"任务不存在：{task_id}")
        return read_json(path)

    def list_tasks(self) -> list[dict]:
        self._require_init()
        return [read_json(path) for path in sorted((self.root / "tasks").glob("*.json"))]

    @staticmethod
    def _version(task, version_id=None):
        if version_id is None:
            return task["versions"][-1] if task["versions"] else None
        for version in task["versions"]:
            if version["id"] == version_id:
                return version
        raise ValueError(f"版本不存在：{version_id}")

    @staticmethod
    def _paragraph(version, reference):
        if version is None:
            raise ValueError("没有可定位的文稿版本")
        for paragraph in version["paragraphs"]:
            if paragraph["id"] == reference:
                return paragraph
        try:
            ordinal = int(reference)
        except (TypeError, ValueError):
            raise ValueError(f"段落不存在：{reference}") from None
        if 1 <= ordinal <= len(version["paragraphs"]):
            return version["paragraphs"][ordinal - 1]
        raise ValueError(f"段落不存在：{reference}")

    def _make_version(self, task, content, target_id=None, source="capture", provenance=None):
        before = self._version(task)
        old = before["paragraphs"] if before else []
        paragraphs = split_paragraphs(content)
        old_counts = Counter(paragraph["text"] for paragraph in old)
        new_counts = Counter(paragraph["text"] for paragraph in paragraphs)
        old_by_text = {paragraph["text"]: paragraph for paragraph in old}
        retained = set()
        for paragraph in paragraphs:
            text = paragraph["text"]
            if old_counts[text] == new_counts[text] == 1:
                paragraph["id"] = old_by_text[text]["id"]
                retained.add(paragraph["id"])
        old_unmatched = [paragraph for paragraph in old if paragraph["id"] not in retained]
        new_unmatched = [paragraph for paragraph in paragraphs if "id" not in paragraph]
        if target_id and len(old_unmatched) == len(new_unmatched) == 1 and old_unmatched[0]["id"] == target_id:
            new_unmatched[0]["id"] = target_id
            new_unmatched[0]["mapping"] = "explicit_target"
        for paragraph in paragraphs:
            paragraph.setdefault("id", new_id("p"))
        version = {"id": f"V{len(task['versions']) + 1}", "content": content, "content_hash": digest(content),
                   "paragraphs": paragraphs, "accepted": "pending", "acceptance_history": [],
                   "created_at": utc_now(), "source": source,
                   "parent_version": before["id"] if before else None}
        if provenance:
            version["restored_from"] = provenance
        return version

    def capture(self, task_id, content: str | None, instruction: str, session_id: str,
                event_id: str, target_version=None, target_paragraph=None, excerpt=None, source="manual") -> dict:
        with file_lock(self.root):
            task = self.get_task(task_id)
            return self._capture(task, content, instruction, session_id, event_id,
                                 target_version, target_paragraph, excerpt, source)

    def _capture(self, task, content, instruction, session_id, event_id,
                 target_version=None, target_paragraph=None, excerpt=None, source="manual", provenance=None,
                 document_intent=None):
        if content is not None and not isinstance(content, str):
            raise ValueError("content 必须为字符串或 None")
        if not isinstance(instruction, str):
            raise ValueError("instruction 必须是原始文本")
        if not session_id or not event_id:
            raise ValueError("session_id 和 event_id 不能为空")
        payload = {"content": content, "instruction": instruction, "session_id": session_id,
                   "event_id": event_id, "target_version": target_version, "target_paragraph": target_paragraph,
                   "excerpt": excerpt, "source": source, "provenance": provenance}
        if document_intent is not None:
            payload["document_intent"] = document_intent
        payload_hash = digest(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        for turn in task["turns"]:
            if turn["event_id"] == event_id:
                if turn["payload_hash"] != payload_hash:
                    raise ValueError(f"事件 {event_id} 已记录，但重试内容不同；原记录保持不变")
                sync = self.commit_path(self._task_path(task["id"]), f"Retry task {task['id']}")
                return {**task, "capture_result": {**turn, "duplicate": True}, "git_sync": sync}
        before = self._version(task)
        target = self._version(task, target_version) if target_version else before
        paragraph = self._paragraph(target, target_paragraph) if target_paragraph is not None else None
        if excerpt is not None and (paragraph is None or excerpt not in paragraph["text"]):
            raise ValueError("摘录与所选版本段落不符；请重新定位，避免批示漂移")
        changed = content is not None and (before is None or content != before["content"])
        if changed:
            after = self._make_version(task, content, paragraph["id"] if paragraph else None, source, provenance)
            task["versions"].append(after)
            previous_sync = task.get("document_sync", {})
            if previous_sync.get("operation") == "restore" and previous_sync.get("status") in {"pending", "conflict", "superseded"}:
                task["document_sync"] = {**previous_sync, "status": "superseded", "superseded_by": after["id"],
                                         "error": "恢复稿尚未写回时又记录了新版本；原恢复版本已保留，请重新选择需要恢复的段落"}
            else:
                task.pop("document_sync", None)
        else:
            after = before
        if document_intent is not None:
            task["document_sync"] = {**document_intent, "version": after["id"], "status": "pending"}
        now = utc_now()
        turn = {"id": new_id("turn"), "event_id": event_id, "payload_hash": payload_hash,
                "instruction": instruction, "session_id": session_id, "source": source,
                "before_version": before["id"] if before else None,
                "after_version": after["id"] if after else None,
                "target_version": target["id"] if paragraph else target_version,
                "target_paragraph": paragraph["id"] if paragraph else None,
                "excerpt": excerpt if excerpt is not None else (paragraph["text"] if paragraph else None),
                "created_at": now, "version_status": "captured" if changed else ("missing" if after is None else ("discussion" if content is None else "unchanged")),
                "missing_version": after is None, "relations": []}
        if provenance:
            turn["restored_from"] = provenance
        if not any(session["id"] == session_id for session in task["sessions"]):
            task["sessions"].append({"id": session_id, "source": source, "started_at": now})
        if paragraph:
            following = next((p for p in after["paragraphs"] if p["id"] == paragraph["id"]), None) if after else None
            status = "modified" if changed and following and following["text"] != paragraph["text"] else "pending"
            self._add_relation(task, turn, target, paragraph, after, following, status,
                               "explicit_target" if following else "ambiguous_mapping")
        if changed and before:
            after_ids = {p["id"] for p in after["paragraphs"]}
            for old in before["paragraphs"]:
                if old["id"] not in after_ids and (not paragraph or old["id"] != paragraph["id"]):
                    self._add_relation(task, turn, before, old, after, None, "pending", "ambiguous_mapping")
        task["turns"].append(turn)
        sync = self._save(task, f"Record {task['id']} event {event_id}")
        return {**task, "capture_result": {**turn, "duplicate": False}, "git_sync": sync}

    @staticmethod
    def _add_relation(task, turn, before, paragraph, after, following, status, reason):
        relation = {"id": new_id("rel"), "turn_id": turn["id"],
                    "before_version": before["id"], "before_paragraph": paragraph["id"],
                    "excerpt": paragraph["text"], "before_excerpt": paragraph["text"],
                    "after_version": after["id"] if after else None,
                    "after_paragraph": following["id"] if following else None,
                    "after_excerpt": following["text"] if following else None,
                    "status": status, "reason": reason, "resolutions": []}
        task["relations"].append(relation)
        turn["relations"].append(relation["id"])

    def set_acceptance(self, task_id, version_id, status, actor):
        if status not in ACCEPTANCE_STATES:
            raise ValueError("不支持的认可状态：" + str(status))
        if not str(actor).strip():
            raise ValueError("认可状态必须由明确的人类审核人设置")
        with file_lock(self.root):
            task = self.get_task(task_id)
            version = self._version(task, version_id)
            version["accepted"] = status
            version["acceptance_history"].append({"status": status, "actor": actor, "at": utc_now()})
            for relation in task["relations"]:
                if relation["after_version"] == version_id and relation["status"] in {"modified", "accepted"}:
                    relation["status"] = "accepted" if status in {"accepted", "final"} else "modified"
            sync = self._save(task, f"Set {task_id} {version_id} acceptance to {status}")
            return {**task, "git_sync": sync}

    def restore_paragraph(self, task_id, from_version, from_paragraph, current_paragraph, instruction, actor):
        if not str(actor).strip():
            raise ValueError("局部恢复必须记录操作者")
        with file_lock(self.root):
            task = self.get_task(task_id)
            old_version = self._version(task, from_version)
            old = self._paragraph(old_version, from_paragraph)
            current = self._version(task)
            target = self._paragraph(current, current_paragraph)
            document = Path(task["document_path"]) if task.get("document_path") else None
            expected_document_hash = None
            if document and document.exists():
                existing = document.read_text(encoding="utf-8")
                if existing != current["content"]:
                    raise ValueError("绑定文稿已有未记录修改，请先 capture 后再局部恢复")
                expected_document_hash = digest(existing)
            content = current["content"][:target["start"]] + old["text"] + current["content"][target["end"]:]
            provenance = {"version_id": old_version["id"], "paragraph_id": old["id"], "excerpt": old["text"], "actor": actor}
            intent = {"operation": "restore", "path": str(document), "expected_document_hash": expected_document_hash,
                      "actor": actor, "requested_at": utc_now()} if document else None
            result = self._capture(task, content, instruction, f"restore:{actor}", new_id("restore"),
                                   current["id"], target["id"], target["text"], "restore", provenance, intent)
            if document:
                result["git_sync"] = self._materialize_document(task)
                result["document_sync"] = task["document_sync"]
            return result

    def _materialize_document(self, task):
        sync = task["document_sync"]
        version = self._version(task, sync["version"])
        document = Path(sync["path"])
        try:
            if self._version(task)["id"] != version["id"]:
                sync.update(status="superseded", error="已有更新版本，不会将旧恢复稿写回文件")
            else:
                current = document.read_text(encoding="utf-8") if document.exists() else None
                actual_hash = digest(current) if current is not None else None
                if current != version["content"] and actual_hash != sync["expected_document_hash"]:
                    sync.update(status="conflict", error="绑定文稿在恢复期间被外部修改或删除；原文件保持不变")
                else:
                    if current != version["content"]:
                        atomic_write(document, version["content"])
                    sync.update(status="synced", completed_at=utc_now())
                    sync.pop("error", None)
        except (OSError, UnicodeError) as exc:
            sync.update(status="pending", error=str(exc))
        return self._save(task, f"Record restored document writeback for {task['id']}")

    def retry_documents(self, task_id=None) -> list[dict]:
        """Finish a recorded restore; never replace an intervening external edit."""
        with file_lock(self.root):
            tasks = [self.get_task(task_id)] if task_id else self.list_tasks()
            results = []
            for task in tasks:
                sync = task.get("document_sync", {})
                if sync.get("operation") == "restore" and sync.get("status") in {"pending", "conflict"}:
                    git = self._materialize_document(task)
                    results.append({"task_id": task["id"], **task["document_sync"], "git_sync": git})
            return results

    def resolve_relation(self, task_id, relation_id, paragraph_id=None, actor=""):
        if not str(actor).strip():
            raise ValueError("确认或取消段落关联必须记录操作者")
        with file_lock(self.root):
            task = self.get_task(task_id)
            relation = next((r for r in task["relations"] if r["id"] == relation_id), None)
            if relation is None:
                raise ValueError(f"段落关联不存在：{relation_id}")
            if paragraph_id is None:
                relation["status"] = "withdrawn"
                relation["after_paragraph"] = None
                relation["after_excerpt"] = None
            else:
                version = self._version(task, relation["after_version"])
                paragraph = self._paragraph(version, paragraph_id)
                relation["after_paragraph"] = paragraph["id"]
                relation["after_excerpt"] = paragraph["text"]
                relation["status"] = "modified"
            relation["resolutions"].append({"actor": actor, "at": utc_now(), "paragraph_id": relation["after_paragraph"], "status": relation["status"]})
            sync = self._save(task, f"Resolve paragraph relation {relation_id}")
            return {**task, "git_sync": sync}

    def status(self):
        self._require_init()
        tasks = self.list_tasks()
        pending = self._pending_commits()
        mismatches = []
        for task in tasks:
            if task.get("document_path") and task["versions"]:
                path = Path(task["document_path"])
                try:
                    if path.read_text(encoding="utf-8") != task["versions"][-1]["content"]:
                        mismatches.append({"task_id": task["id"], "path": str(path), "status": "changed"})
                except (OSError, UnicodeError) as exc:
                    mismatches.append({"task_id": task["id"], "path": str(path), "status": "unavailable", "error": str(exc)})
        return {"root": str(self.root), "initialized": True, "tasks": len(tasks),
                "versions": sum(len(task["versions"]) for task in tasks),
                "turns": sum(len(task["turns"]) for task in tasks),
                "pending_relations": sum(relation["status"] == "pending" for task in tasks for relation in task["relations"]),
                "missing_versions": sum(turn.get("missing_version", False) for task in tasks for turn in task["turns"]),
                "pending_commits": pending, "document_changes": mismatches,
                "document_sync_pending": [{"task_id": task["id"], **task["document_sync"]} for task in tasks
                                          if task.get("document_sync", {}).get("status") in {"pending", "conflict"}],
                "document_sync": [{"task_id": task["id"], **task["document_sync"]} for task in tasks
                                  if task.get("document_sync", {}).get("status") not in {None, "synced"}]}
