"""Explicit-file imports and a durable, opt-in Langfuse delivery outbox.

Historical text is data, never executable instructions. No account discovery,
background upload, hook installation, or implicit document reconstruction occurs.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import socket
import ssl
from urllib import error, parse, request

from .util import atomic_json, atomic_write, digest, file_lock, read_json, utc_now, validate_id


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def trace_id(event_id: str) -> str:
    """Stable OTEL-compatible trace ID for one local immutable event."""
    return digest("writing-memory:trace:" + event_id)[:32]


def _span_id(event_id: str) -> str:
    return digest("writing-memory:span:" + event_id)[:16]


class SourceImporter:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def _store(self):
        from .core import Store
        return Store(self.root)

    def _archive(self, data: bytes, suffix: str) -> dict:
        sha = digest(data)
        target = self.root / "sources" / (sha + suffix)
        if not target.exists():
            atomic_write(target, data)
        self._store().commit_path(target, "Preserve original imported bytes " + sha[:12])
        return {"sha256": sha, "path": str(target.relative_to(self.root)), "bytes": len(data)}

    @staticmethod
    def parse_text(text: str) -> list[dict]:
        """Recognize role-only lines, retaining unmarked text and exact order.

        Accepted examples: ## user, [assistant], 用户：. Inline phrases such as
        '用户：请写材料' remain unknown: they might be quoted prose, not a role.
        """
        aliases = {"user": "user", "human": "user", "用户": "user", "人类": "user",
                   "assistant": "assistant", "ai": "assistant", "助手": "assistant",
                   "system": "system", "系统": "system", "tool": "tool", "工具": "tool"}
        roles = r"user|human|assistant|ai|system|tool|用户|人类|助手|系统|工具"
        marker = re.compile(r"^\s*(?:#{1,6}\s+)?(?:(?P<plain>" + roles + r")|\[(?P<bracket>" + roles + r")\])\s*[:：]?\s*$", re.I)
        messages, content, role, start = [], [], "unknown", 1
        fence = None
        for number, line in enumerate(text.splitlines(keepends=True), 1):
            fenced = re.match(r"^\s*(`{3,}|~{3,})", line)
            if fenced:
                token = fenced.group(1)[0]
                fence = None if fence == token else (fence or token)
            match = marker.match(line.rstrip("\r\n")) if fence is None and not fenced else None
            if match:
                if content or role != "unknown":
                    messages.append({"role": role, "content": "".join(content), "start_line": start})
                content, role, start = [], aliases[(match.group("plain") or match.group("bracket")).lower()], number + 1
            else:
                content.append(line)
        if content or role != "unknown":
            messages.append({"role": role, "content": "".join(content), "start_line": start})
        return messages

    def import_file(self, task_id, path, source="manual", document_path=None) -> dict:
        validate_id(task_id)
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() not in {".md", ".txt"}:
            raise ValueError("文本导入仅支持 .md / .txt；Codex JSONL 请使用 import_codex")
        self._store().get_task(task_id)
        key = digest(_canonical([task_id, source, str(path)]))
        state_path = self.root / "imports" / ("file_" + key + ".json")
        with file_lock(self.root):
            state = read_json(state_path) if state_path.exists() else {"task_id": task_id, "source": source, "original_path": str(path)}
            try:
                raw = path.read_bytes()  # includes close errors; cursor is never advanced on failure
                archive = self._archive(raw, path.suffix.lower())
                state["raw_source"] = archive
                atomic_json(state_path, state)
                text = raw.decode("utf-8-sig")
                messages = self.parse_text(text)
                for index, message in enumerate(messages):
                    message.update(sequence=index, event_id="msg_" + digest(_canonical([task_id, source, archive["sha256"], index])))
                document = self._document(document_path)
                import_id = "import_" + digest(_canonical([key, archive["sha256"], document["sha256"] if document else None]))
                report_path = self.root / "imports" / (import_id + ".json")
                if report_path.exists():
                    existing = read_json(report_path)
                    if existing.get("status") == "imported":
                        self._enqueue_messages(task_id, "import_" + key[:16], source, existing["messages"])
                        state.update(status="imported", latest_import=import_id, error=None, updated_at=utc_now())
                        atomic_json(state_path, state)
                        return existing
                report = {"id": import_id, "task_id": task_id, "source": source, "original_path": str(path),
                          "session_id": "import_" + key[:16],
                          "raw_source": archive, "messages": messages, "history_versions_missing": True,
                          "history_status": "missing: transcript does not establish historical document versions",
                          "document_source": document, "association": "current_document_observation_only",
                          "status": "saved", "imported_at": utc_now()}
                atomic_json(report_path, report)
                captured = self._capture(task_id, import_id, source, document, "import_" + key[:16])
                self._enqueue_messages(task_id, "import_" + key[:16], source, messages)
                report.update(status="imported", capture_result=captured.get("capture_result"))
                atomic_json(report_path, report)
                self._store().commit_path(report_path, "Record imported source " + import_id)
                state.update(status="imported", latest_import=import_id, error=None, updated_at=utc_now())
                atomic_json(state_path, state)
                self._store().commit_path(state_path, "Update file import cursor")
                return report
            except (OSError, UnicodeError, ValueError) as exc:
                state.update(status="error", error=type(exc).__name__, updated_at=utc_now())
                atomic_json(state_path, state)
                self._store().commit_path(state_path, "Record file import failure")
                raise

    def _document(self, path):
        if path is None:
            return None
        path = Path(path).expanduser().resolve()
        raw = path.read_bytes()
        info = self._archive(raw, ".document" + (path.suffix.lower() or ".txt"))
        # Preserve bytes before attempting decoding; do not silently replace bad bytes.
        info.update(original_path=str(path), content=raw.decode("utf-8-sig"))
        return info

    def _capture(self, task_id, event_id, source, document, session_id):
        return self._store().capture(task_id, document["content"] if document else None,
                                     "采集当时的材料；历史消息与文稿版本对应关系待补充确认。",
                                     session_id, event_id, source=source)

    def _enqueue_messages(self, task_id, session_id, source, messages):
        outbox = Outbox(self.root)
        for message in messages:
            outbox.enqueue(message["event_id"], {
                "name": "Imported " + source + " " + str(message.get("payload_type") or message["role"]),
                "sessionId": session_id, "input": message,
                "metadata": {"task_id": task_id, "source": source, "historical_version_status": "unavailable"}})

    def import_codex(self, task_id, path, session_id, document_path=None, project_path=None,
                     live=False, baseline_offset=None, stop_turn_id=None) -> dict:
        """Import only the explicitly named rollout and explicitly bound session.

        Offsets include complete newline-terminated JSON records only. Every raw
        read, including any partial tail, is independently content-addressed.
        """
        validate_id(task_id)
        validate_id(session_id)
        self._store().get_task(task_id)
        path = Path(path).expanduser().resolve()
        project = str(Path(project_path).resolve()) if project_path else None
        key = digest(_canonical([task_id, session_id, str(path)]))
        state_path = self.root / "imports" / ("codex_" + key + ".json")
        with file_lock(self.root):
            state = read_json(state_path) if state_path.exists() else {
                "id": "codex_" + key, "task_id": task_id, "session_id": session_id,
                "source": "codex", "original_path": str(path), "offset": 0,
                "prefix_sha256": digest(b""), "messages": [], "raw_sources": [],
                "history_versions_missing": True, "association": "current_document_observation_only",
                "turn_status": "unknown"}
            state.update(status="reading", updated_at=utc_now())
            atomic_json(state_path, state)
            prefix_validated = False
            try:
                raw = path.read_bytes()
                archive = self._archive(raw, ".jsonl")
                if archive not in state["raw_sources"]:
                    state["raw_sources"].append(archive)
                state["raw_source"] = archive
                atomic_json(state_path, state)
                offset = state["offset"]
                if len(raw) < offset or digest(raw[:offset]) != state["prefix_sha256"]:
                    raise ValueError("会话文件已被改写或截断；原始记录保留，请显式处理新来源")
                prefix_validated = True
                # Session identity must be established by the transcript itself,
                # not by an unchecked hook parameter or the filename.
                first_end = raw.find(b"\n")
                if first_end < 0:
                    state.update(status="pending_tail", pending_bytes=len(raw), completed=False, error=None)
                    atomic_json(state_path, state)
                    return state
                first = json.loads(raw[:first_end])
                meta = first.get("payload", {})
                if first.get("type") != "session_meta" or meta.get("id") != session_id:
                    raise ValueError("JSONL session_meta 与显式绑定的 session_id 不一致")
                if project and (not meta.get("cwd") or str(Path(meta["cwd"]).resolve()) != project):
                    raise ValueError("JSONL cwd 与选定项目不一致")
                while offset < len(raw):
                    end = raw.find(b"\n", offset)
                    if end < 0:
                        break
                    line = raw[offset:end + 1]
                    if line.strip():
                        entry = json.loads(line)
                        if not isinstance(entry, dict):
                            raise ValueError("JSONL 记录必须为对象")
                        payload = entry.get("payload", {})
                        if not isinstance(payload, dict):
                            payload = {"raw_payload": payload}
                        if entry.get("type") == "session_meta" and payload.get("id") != session_id:
                            raise ValueError("同一 JSONL 包含其他会话")
                        message = {"event_id": "codexmsg_" + digest(_canonical([task_id, session_id, str(path), offset, digest(line)])),
                                   "sequence": len(state["messages"]), "byte_start": offset, "byte_end": end + 1,
                                   "raw_line": line.decode("utf-8"), "record": entry,
                                   "role": payload.get("role", "unknown"), "content": payload.get("content"),
                                   "record_type": entry.get("type"), "payload_type": payload.get("type"),
                                   "historical_version_id": None, "history_version_status": "unavailable"}
                        if payload.get("type") == "user_message":
                            message.update(role="user", content=payload.get("message"))
                        elif payload.get("type") == "agent_message":
                            message.update(role="assistant", content=payload.get("message"))
                        if entry.get("type") == "event_msg":
                            if payload.get("type") in {"task_started", "turn_started"}:
                                state["turn_status"] = "open"
                            elif payload.get("type") in {"task_complete", "turn_complete", "turn_completed"}:
                                state["turn_status"] = "completed"
                            elif payload.get("type") == "turn_aborted":
                                state["turn_status"] = "aborted"
                        state["messages"].append(message)
                    offset = end + 1
                    state["offset"] = offset
                    if len(state["messages"]) % 32 == 0:
                        state["prefix_sha256"] = digest(raw[:offset])
                        atomic_json(state_path, state)
                state["prefix_sha256"] = digest(raw[:offset])
                atomic_json(state_path, state)
                document = self._document(document_path)
                state.update(document_source=document, capture_mode="live" if live else "historical",
                             requested_baseline_offset=baseline_offset, stop_turn_id=stop_turn_id,
                             status="saved_for_capture", capture_pending=True)
                atomic_json(state_path, state)
                self._enqueue_messages(task_id, session_id, "codex", state["messages"])
                if live:
                    self._capture_live(state, document, baseline_offset, stop_turn_id)
                else:
                    capture_id = "codexcap_" + digest(_canonical([key, state["prefix_sha256"], document["sha256"] if document else None]))
                    if state.get("last_capture_event_id") != capture_id:
                        captured = self._capture(task_id, capture_id, "codex", document, session_id)
                        state.update(last_capture_event_id=capture_id, capture_result=captured.get("capture_result"))
                state.update(status="pending_tail" if offset < len(raw) else "caught_up",
                             pending_bytes=len(raw) - offset, document_source=document, error=None,
                             capture_pending=False,
                             completed=offset == len(raw) and state["turn_status"] in {"completed", "aborted"},
                             updated_at=utc_now())
                atomic_json(state_path, state)
                self._store().commit_path(state_path, "Capture selected Codex session")
                return state
            except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc:
                if prefix_validated:
                    state["prefix_sha256"] = digest(raw[:state["offset"]])
                state.update(status="error", completed=False, error=type(exc).__name__, updated_at=utc_now())
                atomic_json(state_path, state)
                self._store().commit_path(state_path, "Record Codex capture failure")
                raise

    def _capture_live(self, state, document, baseline_offset, stop_turn_id=None):
        if "live_offset" not in state:
            if baseline_offset is None:
                capture_id = "codexbaseline_" + digest(state["id"])
                captured = self._store().capture(state["task_id"], document["content"] if document else None,
                    "开启定向记录时保存文稿基线；此前会话不关联到该版本。", state["session_id"], capture_id, source="codex-baseline")
                state.update(live_offset=state["offset"], live_status="armed", live_armed_at=utc_now(),
                             capture_result=captured.get("capture_result"), last_capture_event_id=capture_id)
                return
            if not isinstance(baseline_offset, int) or baseline_offset < 0 or baseline_offset > state["offset"]:
                raise ValueError("live baseline_offset 必须是已完整读取的字节位置")
            if baseline_offset and not any(m["byte_end"] == baseline_offset for m in state["messages"]):
                raise ValueError("live baseline_offset 必须对齐完整 JSONL 行末")
            state["live_offset"] = baseline_offset
        since = state["live_offset"]
        fresh = [message for message in state["messages"] if message["byte_start"] >= since]
        if not fresh:
            state["live_status"] = "unchanged"
            return
        turns, active, user_fallback = [], None, []
        for message in fresh:
            payload = message["record"].get("payload", {})
            kind = payload.get("type") if isinstance(payload, dict) else None
            if message["record_type"] == "event_msg" and kind in {"task_started", "turn_started"}:
                active = {"id": payload.get("turn_id") or message["event_id"], "instructions": [], "start": message["byte_start"]}
                user_fallback = []
            if active and message["role"] == "user":
                if kind == "user_message":
                    active["instructions"].append(str(message.get("content") or ""))
                elif kind == "message":
                    parts = message.get("content") or []
                    user_fallback.append("".join(part.get("text", "") for part in parts if isinstance(part, dict)))
            if active and message["record_type"] == "event_msg" and kind in {"task_complete", "turn_complete", "turn_completed", "turn_aborted"}:
                active.update(end=message["byte_end"], aborted=kind == "turn_aborted")
                if not active["instructions"]:
                    active["instructions"] = user_fallback
                turns.append(active)
                active, user_fallback = None, []
        # Stop can run before the completion marker reaches disk. Its explicit
        # turn ID lets us preserve *this* file observation and attach it later,
        # even if the next turn has already edited the working document.
        if active and stop_turn_id and active["id"] == stop_turn_id and document:
            pending = state.setdefault("pending_document_observations", {})
            previous = pending.get(stop_turn_id)
            if previous and previous["document"]["sha256"] != document["sha256"]:
                state["live_status"] = "conflicting_stop_snapshot"
                return
            pending[stop_turn_id] = {"document": document, "observed_at": utc_now()}
            atomic_json(self.root / "imports" / (state["id"] + ".json"), state)
        resolved = 0
        while turns and turns[0]["id"] in state.get("pending_document_observations", {}):
            completed_turn = turns.pop(0)
            observation = state["pending_document_observations"][completed_turn["id"]]
            if not any(text.strip() for text in completed_turn["instructions"]):
                state["live_status"] = "missing_instruction"
                return
            self._apply_live_turn(state, completed_turn, observation["document"])
            observation["resolved_event_id"] = state["last_capture_event_id"]
            resolved += 1
        # A newer open turn can already be editing the document. Waiting avoids
        # associating its in-progress file with a previously finished turn.
        if active or state["offset"] < state["raw_source"]["bytes"] or state["turn_status"] not in {"completed", "aborted"}:
            state["live_status"] = "pending_completion"
            return
        if not turns:
            state["live_status"] = "captured" if resolved else "missing_turn_start"
            return
        latest = turns[-1]
        gap = len(turns) > 1
        instruction = "\n\n".join(latest["instructions"])
        if not instruction.strip() and not gap:
            state["live_status"] = "missing_instruction"
            return
        if gap:
            instruction = "发现多个未逐轮采集的回合；仅保存当前文稿，历史中间版本缺失，不建立批示因果关联。"
        self._apply_live_turn(state, latest, document, instruction, gap)
        if gap:
            state.setdefault("live_gaps", []).append({"from_offset": since, "to_offset": latest["end"],
                "unobserved_intermediate_versions": len(turns) - 1, "turn_ids": [turn["id"] for turn in turns]})

    def _apply_live_turn(self, state, latest, document, instruction=None, gap=False):
        instruction = instruction if instruction is not None else "\n\n".join(latest["instructions"])
        event_id = "codexlive_" + digest(_canonical([state["task_id"], state["session_id"], latest["id"], latest["end"]]))
        captured = self._store().capture(state["task_id"], document["content"] if document else None,
            instruction, state["session_id"], event_id, source="codex-gap" if gap else "codex-live")
        state.update(live_offset=latest["end"], live_status="unobserved_intermediate_versions" if gap else "captured",
                     last_capture_event_id=event_id, capture_result=captured.get("capture_result"))

    def recover_saved(self) -> dict:
        """Finish durable manifests using archived facts only, without network."""
        recovered = []
        with file_lock(self.root):
            for path in sorted((self.root / "imports").glob("*.json")):
                state = read_json(path)
                if state.get("status") not in {"saved", "saved_for_capture"} and not state.get("capture_pending"):
                    continue
                if not state.get("task_id") or not state.get("session_id"):
                    continue
                document = state.get("document_source")
                if state.get("id", "").startswith("codex_"):
                    if state.get("capture_mode") == "live":
                        self._capture_live(state, document, state.get("requested_baseline_offset"), state.get("stop_turn_id"))
                    else:
                        key = state["id"].removeprefix("codex_")
                        event_id = "codexcap_" + digest(_canonical([key, state["prefix_sha256"], document["sha256"] if document else None]))
                        captured = self._capture(state["task_id"], event_id, "codex", document, state["session_id"])
                        state.update(last_capture_event_id=event_id, capture_result=captured.get("capture_result"))
                    state.update(status="pending_tail" if state["offset"] < state["raw_source"]["bytes"] else "caught_up",
                                 pending_bytes=state["raw_source"]["bytes"] - state["offset"],
                                 completed=state["offset"] == state["raw_source"]["bytes"] and state["turn_status"] in {"completed", "aborted"})
                else:
                    captured = self._capture(state["task_id"], state["id"], state["source"], document, state["session_id"])
                    state.update(status="imported", capture_result=captured.get("capture_result"))
                self._enqueue_messages(state["task_id"], state["session_id"], state["source"], state.get("messages", []))
                state["capture_pending"] = False
                atomic_json(path, state)
                self._store().commit_path(path, "Recover durable import capture")
                recovered.append(state["id"])
        return {"recovered_import_ids": recovered, "network_requests": 0}


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


class Outbox:
    """Immutable local events with observable remote delivery states.

    OTLP v4 does not promise server-side retry deduplication. After an ambiguous
    transport failure we verify, and require explicit retry_uncertain to resend.
    """
    MAX_REQUEST_BYTES = 3_000_000

    def __init__(self, root, timeout=15):
        self.root = Path(root).resolve()
        self.directory = self.root / "outbox"
        self.timeout = timeout

    def enqueue(self, event_id, payload, kind="trace") -> dict:
        validate_id(event_id)
        if kind not in {"trace", "score"}:
            raise ValueError("outbox kind 只支持 trace 或 score")
        if not isinstance(payload, dict):
            raise ValueError("payload 必须为 JSON 对象")
        if payload.get("metadata") is not None and not isinstance(payload["metadata"], dict):
            raise ValueError("payload.metadata 必须为对象")
        if payload.get("name") is not None and not isinstance(payload["name"], str):
            raise ValueError("payload.name 必须为字符串")
        canonical = _canonical(payload)
        path = self.directory / (event_id + ".json")
        with file_lock(self.root):
            if path.exists():
                item = read_json(path)
                if item["kind"] != kind or item["payload_sha256"] != digest(canonical):
                    raise ValueError("稳定 event_id 已存在且内容不同，不允许覆盖")
                return item
            item = {"id": event_id, "kind": kind, "payload": json.loads(canonical),
                    "payload_sha256": digest(canonical), "status": "pending", "attempts": 0,
                    "verification_attempts": 0, "created_at": utc_now(), "error": None,
                    "trace_id": trace_id(event_id) if kind == "trace" else payload.get("traceId"),
                    "span_id": _span_id(event_id) if kind == "trace" else None}
            atomic_json(path, item)
            return item

    def _save(self, item):
        atomic_json(self.directory / (item["id"] + ".json"), item)

    def status(self) -> dict:
        with file_lock(self.root):
            items = [read_json(path) for path in sorted(self.directory.glob("*.json"))]
            counts = {name: sum(item["status"] == name for item in items)
                      for name in ("pending", "sending", "delivery_uncertain", "accepted", "verified")}
            return {"total": len(items), **counts, "items": [
                {key: item.get(key) for key in ("id", "kind", "status", "attempts", "verification_attempts", "error", "retry_after", "trace_id", "span_id")}
                for item in items]}

    def _config(self):
        host = os.environ.get("LANGFUSE_HOST", "").rstrip("/")
        public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
        secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
        if not host or not public or not secret:
            raise ValueError("LANGFUSE_HOST / PUBLIC_KEY / SECRET_KEY 未完整配置")
        url = parse.urlsplit(host)
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("LANGFUSE_HOST 不得包含凭据、查询或片段")
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("LANGFUSE_HOST 需要 HTTPS（本机测试除外）")
        auth = base64.b64encode((public + ":" + secret).encode()).decode()
        return host, {"Authorization": "Basic " + auth, "Content-Type": "application/json", "x-langfuse-ingestion-version": "4"}

    def _http(self, method, path, payload=None):
        host, headers = self._config()
        data = _canonical(payload).encode() if payload is not None else None
        req = request.Request(host + path, data=data, method=method, headers=headers)
        with request.build_opener(_NoRedirect).open(req, timeout=self.timeout) as response:
            raw = response.read()
            decoded = json.loads(raw) if raw.strip() else {}
            if not isinstance(decoded, dict):
                raise ValueError("invalid_response_shape")
            return decoded

    def _span(self, item):
        payload = item["payload"]
        timestamp = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        nanos = str(int(timestamp.timestamp() * 1_000_000_000))
        attributes = {"langfuse.observation.type": "span", "langfuse.trace.name": payload.get("name", "writing-memory"),
                      "langfuse.observation.input": _canonical(payload.get("input")),
                      "langfuse.observation.output": _canonical(payload.get("output")),
                      "langfuse.observation.metadata.writing_memory_event_id": item["id"],
                      "langfuse.observation.metadata.writing_memory_payload_sha256": item["payload_sha256"]}
        if payload.get("sessionId"):
            attributes["langfuse.session.id"] = payload["sessionId"]
        for key, value in (payload.get("metadata") or {}).items():
            if not key.startswith("writing_memory_"):
                attributes["langfuse.observation.metadata." + key] = value if isinstance(value, str) else _canonical(value)
        return {"traceId": item["trace_id"], "spanId": item["span_id"], "name": payload.get("name", "writing-memory"),
                "kind": 1, "startTimeUnixNano": nanos, "endTimeUnixNano": nanos,
                "attributes": [{"key": key, "value": {"stringValue": str(value)}} for key, value in attributes.items()],
                "status": {"code": 1}}

    def _verify(self, item):
        item["verification_attempts"] += 1
        item["last_verification_at"] = utc_now()
        self._save(item)
        if item["kind"] == "trace":
            created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            params = {"traceId": item["trace_id"], "fields": "core,io,metadata", "limit": 100,
                      "fromStartTime": (created - timedelta(seconds=1)).isoformat(),
                      "toStartTime": (created + timedelta(seconds=1)).isoformat()}
            response = self._http("GET", "/api/public/v2/observations?" + parse.urlencode(params))
            def matches(row):
                if row.get("id") != item["span_id"] or row.get("traceId") != item["trace_id"]:
                    return False
                meta = row.get("metadata") or {}
                if not isinstance(meta, dict):
                    return False
                if meta.get("writing_memory_payload_sha256") != item["payload_sha256"]:
                    return False
                for field in ("input", "output"):
                    value = row.get(field)
                    expected = item["payload"].get(field)
                    if isinstance(value, str):
                        try:
                            value = json.loads(value)
                        except ValueError:
                            pass
                    if value != expected:
                        return False
                return True
            rows = response.get("data", [])
            if not isinstance(rows, list):
                raise ValueError("invalid_observation_response")
            found = any(matches(row) for row in rows if isinstance(row, dict))
        else:
            score_id = item["payload"].get("id", item["id"])
            response = self._http("GET", "/api/public/v3/scores?" + parse.urlencode({"id": score_id, "fields": "details,subject"}))
            def matches_score(row):
                payload = item["payload"]
                if row.get("id") != score_id or row.get("name") != payload.get("name") or row.get("value") != payload.get("value"):
                    return False
                metadata = row.get("metadata") or {}
                if not isinstance(metadata, dict) or metadata.get("writing_memory_payload_sha256") != item["payload_sha256"]:
                    return False
                subject = row.get("subject") or {}
                if not isinstance(subject, dict):
                    return False
                if payload.get("observationId"):
                    return subject.get("kind") == "observation" and subject.get("id") == payload["observationId"] and subject.get("traceId") == payload.get("traceId")
                if payload.get("traceId"):
                    return subject.get("kind") == "trace" and subject.get("id") == payload["traceId"]
                return bool(payload.get("sessionId")) and subject.get("kind") == "session" and subject.get("id") == payload["sessionId"]
            rows = response.get("data", [])
            if not isinstance(rows, list):
                raise ValueError("invalid_score_response")
            found = any(matches_score(row) for row in rows if isinstance(row, dict))
        if found:
            item.update(status="verified", verified_at=utc_now(), error=None)
        else:
            item["error"] = "remote_not_yet_visible_or_content_mismatch"
        self._save(item)
        return found

    def _failure(self, item, exc, verification=False):
        # Do not persist remote bodies, credential-bearing URLs, or arbitrary exception text.
        label = type(exc).__name__
        definite = False
        if isinstance(exc, error.HTTPError):
            label = "http_" + str(exc.code)
            definite = exc.code < 500
            if exc.code == 429:
                retry = exc.headers.get("Retry-After", "") if exc.headers else ""
                if retry.isdigit():
                    item["retry_after"] = (datetime.now(timezone.utc) + timedelta(seconds=min(int(retry), 86400))).isoformat()
        elif isinstance(exc, error.URLError):
            definite = isinstance(exc.reason, (ConnectionRefusedError, socket.gaierror, ssl.SSLCertVerificationError))
        elif isinstance(exc, ConnectionRefusedError):
            definite = True
        if not verification:
            item["status"] = "pending" if definite or item["kind"] == "score" else "delivery_uncertain"
        item["error"] = label
        self._save(item)

    def sync(self, retry_uncertain=False, max_items=100) -> dict:
        """One bounded attempt per event. Accepted events are verified, never resent."""
        if not isinstance(max_items, int) or max_items < 1 or max_items > 1000:
            raise ValueError("max_items 必须在 1 到 1000 之间")
        try:
            self._config()
        except ValueError as exc:
            result = self.status()
            result["configuration_error"] = str(exc)
            return result
        with file_lock(self.root / ".sync-lock"):
            pending_scores = []
            available = [read_json(path) for path in self.directory.glob("*.json")]
            available = [item for item in available if item["status"] != "verified"]
            available = [item for item in available if not item.get("retry_after") or datetime.fromisoformat(item["retry_after"]) <= datetime.now(timezone.utc)]
            available.sort(key=lambda item: (max(item.get("last_attempt_at", ""), item.get("last_verification_at", "")), item["created_at"], item["id"]))
            for item in available[:max_items]:
                if item["status"] == "sending":  # process died after durable pre-send marker
                    item["status"] = "delivery_uncertain" if item["kind"] == "trace" else "pending"
                    self._save(item)
                if item["status"] in {"accepted", "delivery_uncertain"}:
                    try:
                        if self._verify(item):
                            continue
                    except (OSError, ValueError) as exc:
                        self._failure(item, exc, verification=True)
                    if item["status"] == "accepted" or not retry_uncertain:
                        continue
                if item.get("retry_after") and datetime.fromisoformat(item["retry_after"]) > datetime.now(timezone.utc):
                    continue
                if item["kind"] == "score":
                    pending_scores.append(item)
                    continue
                payload = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "writing-memory"}}]},
                             "scopeSpans": [{"scope": {"name": "writing-memory"}, "spans": [self._span(item)]}]}]}
                if len(_canonical(payload).encode()) > self.MAX_REQUEST_BYTES:
                    item.update(status="pending", error="payload_too_large: full local event retained")
                    self._save(item)
                    continue
                item.update(status="sending", attempts=item["attempts"] + 1, last_attempt_at=utc_now(), error=None)
                self._save(item)
                try:
                    response = self._http("POST", "/api/public/otel/v1/traces", payload)
                    partial = response.get("partialSuccess", response.get("partial_success", {})) or {}
                    if not isinstance(partial, dict):
                        raise ValueError("invalid_otel_partial_response")
                    if int(partial.get("rejectedSpans", partial.get("rejected_spans", 0))) > 0:
                        item.update(status="pending", error="otel_span_rejected")
                    else:
                        item.update(status="accepted", accepted_at=utc_now(), error=None)
                    self._save(item)
                except (OSError, ValueError, TypeError) as exc:
                    self._failure(item, exc)
                    continue
                if item["status"] == "accepted":
                    try:
                        self._verify(item)
                    except (OSError, ValueError) as exc:
                        self._failure(item, exc, verification=True)
            # Score-create remains supported by the legacy envelope endpoint.
            batch, items, size = [], [], 0
            for item in pending_scores:
                body = dict(item["payload"])
                body.setdefault("id", item["id"])
                body["metadata"] = {**(body.get("metadata") or {}), "writing_memory_payload_sha256": item["payload_sha256"]}
                event = {"id": item["id"], "type": "score-create", "timestamp": item["created_at"], "body": body}
                event_size = len(_canonical(event).encode())
                if event_size > self.MAX_REQUEST_BYTES - 100:
                    item["error"] = "payload_too_large: full local event retained"
                    self._save(item)
                    continue
                if items and size + event_size > self.MAX_REQUEST_BYTES - 100:
                    self._send_scores(batch, items)
                    batch, items, size = [], [], 0
                batch.append(event)
                items.append(item)
                size += event_size + 1
            if items:
                self._send_scores(batch, items)
        return self.status()

    def _send_scores(self, batch, items):
        for item in items:
            item.update(status="sending", attempts=item["attempts"] + 1, last_attempt_at=utc_now())
            self._save(item)
        try:
            response = self._http("POST", "/api/public/ingestion", {"batch": batch})
            if not isinstance(response.get("successes", []), list) or not isinstance(response.get("errors", []), list):
                raise ValueError("invalid_ingestion_response")
            successes = {row.get("id") for row in response.get("successes", [])
                         if isinstance(row, dict) and isinstance(row.get("status"), int) and 200 <= row["status"] < 300}
            errors = {row.get("id"): row.get("status") for row in response.get("errors", []) if isinstance(row, dict)}
            for item in items:
                if item["id"] in successes and item["id"] not in errors:
                    item.update(status="accepted", accepted_at=utc_now(), error=None)
                else:
                    item.update(status="pending", error="ingestion_event_" + str(errors.get(item["id"], "missing_ack")))
                self._save(item)
        except (OSError, ValueError, TypeError) as exc:
            for item in items:
                self._failure(item, exc)
            return
        for item in items:
            if item["status"] == "accepted":
                try:
                    self._verify(item)
                except (OSError, ValueError) as exc:
                    self._failure(item, exc, verification=True)


def run_stop_hook(payload: dict, cwd=None) -> dict:
    """Read only the exact working directory's explicit opt-in binding."""
    project = Path(cwd or os.getcwd()).resolve()
    config_path = project / ".writing-memory-hook.json"
    if not config_path.is_file():
        return {"status": "skipped", "reason": "no_project_binding"}
    config = read_json(config_path)
    if config.get("enabled") is not True:
        return {"status": "skipped", "reason": "disabled"}
    if Path(config.get("project_path", "")).resolve() != project:
        return {"status": "skipped", "reason": "project_mismatch"}
    if payload.get("cwd") and Path(payload["cwd"]).resolve() != project:
        return {"status": "skipped", "reason": "hook_cwd_mismatch"}
    if payload.get("hook_event_name") != "Stop" or payload.get("session_id") != config.get("session_id"):
        return {"status": "skipped", "reason": "event_or_session_mismatch"}
    if not payload.get("transcript_path") or not config.get("transcript_path"):
        return {"status": "skipped", "reason": "missing_explicit_transcript"}
    transcript = Path(config["transcript_path"]).expanduser().resolve()
    if Path(payload["transcript_path"]).expanduser().resolve() != transcript:
        return {"status": "skipped", "reason": "transcript_mismatch"}
    document = (project / config["document_path"]).resolve()
    if not document.is_relative_to(project):
        raise ValueError("绑定文稿必须位于选定项目内")
    root = (project / config["storage_root"]).resolve()
    result = SourceImporter(root).import_codex(config["task_id"], transcript, config["session_id"], document, project,
                                              live=True, baseline_offset=config.get("live_baseline_offset"), stop_turn_id=payload.get("turn_id"))
    # Sync remains a separate explicit operation; a Stop hook only preserves data.
    return {"status": result["status"], "task_id": result["task_id"], "offset": result["offset"],
            "pending_bytes": result.get("pending_bytes", 0), "completed": result.get("completed", False),
            "live_status": result.get("live_status")}
