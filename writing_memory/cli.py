"""Command line entry point. Imported text is always data, never commands."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .core import Store


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="团队写作经验：记录、追溯、审核与复用")
    p.add_argument("--root", type=Path, default=Path(".writing-memory"), help="独立资产仓库（默认 .writing-memory）")
    s = p.add_subparsers(dest="command", required=True)
    s.add_parser("init", help="初始化独立本地资产仓库")
    s.add_parser("status", help="查看记录、Git 与同步状态")
    s.add_parser("doctor", help="检查本地及接入配置，不上传数据")
    s.add_parser("tasks", help="列出写作任务")
    q = s.add_parser("start", help="建立任务，保存文稿初始版本")
    for name in ["title", "purpose", "audience", "document-type"]:
        q.add_argument("--" + name, required=True)
    q.add_argument("--topic", default="")
    q.add_argument("--document", type=Path)
    q = s.add_parser("show", help="查看任务及不可变版本")
    q.add_argument("task_id")
    q = s.add_parser("capture", help="记录一轮指令和当前文稿；讨论轮用 --no-document")
    q.add_argument("task_id")
    q.add_argument("--instruction", required=True)
    q.add_argument("--session", required=True)
    q.add_argument("--event", required=True, help="稳定的轮次编号；重试时使用原编号")
    q.add_argument("--document", type=Path)
    q.add_argument("--no-document", action="store_true")
    q.add_argument("--target-version")
    q.add_argument("--target-paragraph")
    q.add_argument("--excerpt")
    q = s.add_parser("accept", help="记录人类的明确认可（并非事实核验）")
    q.add_argument("task_id")
    q.add_argument("version")
    q.add_argument("--status", choices=["pending", "accepted", "rejected", "final", "withdrawn"], default="accepted")
    q.add_argument("--actor", required=True)
    q = s.add_parser("restore", help="恢复旧版某段，生成新版本")
    q.add_argument("task_id")
    q.add_argument("--from-version", required=True)
    q.add_argument("--from-paragraph", required=True)
    q.add_argument("--current-paragraph", required=True)
    q.add_argument("--instruction", required=True)
    q.add_argument("--actor", required=True)
    q = s.add_parser("resolve", help="人工补充或取消待确认段落关联")
    q.add_argument("task_id")
    q.add_argument("relation_id")
    q.add_argument("--paragraph", help="目标段落；省略表示取消关联")
    q.add_argument("--actor", required=True)
    q = s.add_parser("document-retry", help="补写中断的段落恢复；保留未记录的外部修改")
    q.add_argument("task_id")
    q = s.add_parser("import", help="导入 Markdown/TXT，保留原文与缺失标记")
    q.add_argument("task_id")
    q.add_argument("path", type=Path)
    q.add_argument("--source", default="manual")
    q.add_argument("--document", type=Path)
    q = s.add_parser("import-codex", help="只采集明确指定的会话文件，支持半行续传")
    q.add_argument("task_id")
    q.add_argument("path", type=Path)
    q.add_argument("--session", required=True)
    q.add_argument("--document", type=Path)
    q.add_argument("--project", type=Path)
    q.add_argument("--live", action="store_true", help="写作前先运行以建立采集基线，后续关联完整新轮次与文稿")
    q = s.add_parser("extract", help="生成提炼上下文和提示词快照，由 Codex 阅读提炼")
    q.add_argument("task_id")
    q.add_argument("--prompt-name", help="从 Langfuse 获取此名称的 text 提示词；省略时使用本地初始模板")
    q.add_argument("--prompt-version", type=int)
    q.add_argument("--prompt-label", default="production")
    q = s.add_parser("candidates", help="导入并校验 AI 候选 JSON")
    q.add_argument("task_id")
    q.add_argument("path", type=Path)
    q.add_argument("--extraction-id", required=True, help="关联 extract 返回的提炼编号，保存实际提示词依据")
    q = s.add_parser("review", help="导出可放入 GitHub PR 的候选文件")
    q.add_argument("candidate_ids", nargs="+")
    q = s.add_parser("revoke", help="准备撤销变更，合并审核后生效")
    q.add_argument("experience_id")
    q.add_argument("--reason", required=True)
    q = s.add_parser("confirm-merge", help="核验 GitHub 合并与人工审核，登记正式经验")
    q.add_argument("--repo", required=True, help="owner/repository")
    q.add_argument("--pr", required=True, type=int)
    q = s.add_parser("export", help="按任务文种、读者和主题导出正式经验（尚未加载）")
    q.add_argument("task_id")
    q = s.add_parser("load", help="确认经验文件已实际提供给本次 AI")
    q.add_argument("task_id")
    q.add_argument("export_id")
    q.add_argument("--mode", choices=["codex_read", "manual_attachment", "manual_copy", "skill"], default="manual_attachment")
    q.add_argument("--actor", required=True)
    q = s.add_parser("feedback", help="评价实际加载的某条经验")
    q.add_argument("load_id")
    q.add_argument("experience_id")
    q.add_argument("--rating", choices=["useful", "useless", "misapplied"], required=True)
    q.add_argument("--reason", required=True)
    q = s.add_parser("sync", help="将待处理记录同步到环境变量指定的 Langfuse")
    q.add_argument("--retry-uncertain", action="store_true", help="显式重试送达不确定的记录；远端可能重复")
    q.add_argument("--limit", type=int, default=100, help="本次最多处理多少条待投递记录（默认100）")
    s.add_parser("recover", help="从已保存轮次重建待同步队列，重试失败的本地 Git 提交")
    return p


def doctor(root: Path) -> dict:
    git = shutil.which("git")
    gh = shutil.which("gh")
    configured = {key: bool(os.environ.get(key)) for key in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")}
    codex = shutil.which("codex")
    codex_status = "not_found"
    if codex:
        try:
            result = subprocess.run([codex, "--version"], capture_output=True, timeout=10)
            codex_status = "available" if result.returncode == 0 else "installed_but_not_runnable"
        except (OSError, subprocess.TimeoutExpired):
            codex_status = "installed_but_not_runnable"
    remote = ""
    if git and (root / ".git").exists():
        result = subprocess.run([git, "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True)
        # Only report presence; credentials can be embedded in a remote URL.
        remote = "configured" if result.returncode == 0 else "not_configured"
    return {"python": sys.version.split()[0], "git": bool(git), "github_cli": bool(gh),
            "asset_repository": str(root.resolve()), "asset_remote": remote or "not_initialized",
            "langfuse_environment": configured, "codex_cli": codex_status,
            "desktop_hook_acceptance": "not_verified", "network_requests": 0}


def execute(args):
    store = Store(args.root)
    command = args.command
    if command == "init":
        return store.init()
    if command == "doctor":
        return doctor(args.root)
    # Validate before constructing helpers whose output directories are created
    # eagerly; a typo must not leave a half-initialized, unusable root.
    store._require_init()
    if command == "tasks":
        return store.list_tasks()
    if command == "start":
        return store.create_task(args.title, args.purpose, args.audience, args.document_type,
                                 document_path=str(args.document.resolve()) if args.document else None, topic=args.topic)
    if command == "show":
        return store.get_task(args.task_id)
    if command == "capture":
        if args.no_document and args.document:
            raise ValueError("--document 与 --no-document 不能同时使用")
        task = store.get_task(args.task_id)
        path = args.document or task.get("document_path")
        content = None if args.no_document or not path else Path(path).read_text(encoding="utf-8")
        result = store.capture(args.task_id, content, args.instruction, args.session, args.event,
                               target_version=args.target_version, target_paragraph=args.target_paragraph, excerpt=args.excerpt)
        from .delivery import enqueue_turn
        turn = next(t for t in result["turns"] if t["event_id"] == args.event)
        enqueue_turn(args.root, result, turn)
        return result
    if command == "accept":
        return store.set_acceptance(args.task_id, args.version, args.status, args.actor)
    if command == "restore":
        return store.restore_paragraph(args.task_id, args.from_version, args.from_paragraph,
                                       args.current_paragraph, args.instruction, args.actor)
    if command == "resolve":
        return store.resolve_relation(args.task_id, args.relation_id, args.paragraph, args.actor)
    if command == "document-retry":
        return store.retry_documents(args.task_id)
    if command in ("import", "import-codex"):
        from .integrations import SourceImporter
        importer = SourceImporter(args.root)
        if command == "import":
            return importer.import_file(args.task_id, args.path, source=args.source, document_path=args.document)
        return importer.import_codex(args.task_id, args.path, args.session, document_path=args.document, project_path=args.project, live=args.live)
    if command == "recover":
        from .delivery import recover
        return recover(args.root)
    if command in ("sync", "status"):
        from .integrations import Outbox
        outbox = Outbox(args.root)
        if command == "sync":
            from .delivery import recover
            recover(args.root)
            return outbox.sync(retry_uncertain=args.retry_uncertain, max_items=args.limit)
        from .delivery import source_status
        return {"records": store.status(), "sources": source_status(args.root), "delivery": outbox.status()}
    from .experience import ExperienceLibrary
    library = ExperienceLibrary(args.root)
    if command == "extract":
        task = store.get_task(args.task_id)
        if args.prompt_version is not None and not args.prompt_name:
            raise ValueError("--prompt-version 需要同时指定 --prompt-name")
        prompt_snapshot = None
        if args.prompt_name:
            from .prompt_source import fetch_prompt
            prompt_snapshot = fetch_prompt(args.root, args.prompt_name, args.prompt_version, args.prompt_label)
        return library.prepare_extraction(task, prompt_snapshot=prompt_snapshot)
    if command == "candidates":
        return library.import_candidates(store.get_task(args.task_id), args.path, extraction_id=args.extraction_id)
    if command == "review":
        return library.prepare_review(args.candidate_ids)
    if command == "revoke":
        return library.prepare_revocation(args.experience_id, args.reason)
    if command == "confirm-merge":
        return library.confirm_from_github(args.repo, args.pr)
    if command == "export":
        return library.export_for_task(store.get_task(args.task_id))
    if command == "load":
        store.get_task(args.task_id)
        return library.record_load(args.task_id, args.export_id, args.mode, args.actor)
    if command == "feedback":
        from .delivery import enqueue_feedback
        result = library.feedback(args.load_id, args.experience_id, args.rating, args.reason)
        result["delivery"] = enqueue_feedback(args.root, result)
        return result
    raise ValueError(f"未知命令：{command}")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (ValueError, OSError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc), "command": args.command}, ensure_ascii=False), file=sys.stderr)
        return 1
