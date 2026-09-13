"""Replay saved facts into delivery jobs after a crash, without making network calls."""
from pathlib import Path

from .core import Store
from .integrations import Outbox, trace_id
from .util import digest, read_json


def turn_event_id(task_id: str, event_id: str) -> str:
    return "turn_" + digest(task_id + "\x00" + event_id)


def source_status(root: Path) -> dict:
    """Expose incomplete or newly appended sources without printing their text."""
    items = []
    for path in sorted((Path(root) / "imports").glob("*.json")):
        record = read_json(path)
        if not record.get("messages") and not record.get("error"):
            continue
        item = {key: record.get(key) for key in ("id", "task_id", "source", "status", "completed", "live_status", "offset", "pending_bytes", "history_versions_missing", "error")}
        original = record.get("original_path")
        if record.get("source") == "codex" and original:
            try:
                item["current_source_bytes"] = Path(original).stat().st_size
                item["needs_capture"] = item["current_source_bytes"] != record.get("offset", 0)
            except OSError:
                item["source_unavailable"] = True
        items.append(item)
    return {"total": len(items), "items": items}


def enqueue_turn(root: Path, task: dict, turn: dict) -> dict:
    version = next((v for v in task["versions"] if v["id"] == turn.get("after_version")), None)
    return Outbox(root).enqueue(turn_event_id(task["id"], turn["event_id"]), {
        "name": task["title"], "input": turn["instruction"],
        "output": version["content"] if version else None, "sessionId": turn["session_id"],
        "metadata": {"task_id": task["id"], "event_id": turn["event_id"], "source": turn.get("source"),
                     "before_version": turn.get("before_version"), "after_version": turn.get("after_version")}})


def enqueue_feedback(root: Path, feedback: dict) -> dict:
    box = Outbox(root)
    context_id = "load_" + digest(feedback["load_id"])
    loaded = read_json(Path(root) / "usage/loads" / f"{feedback['load_id']}.json")
    box.enqueue(context_id, {"name": "Writing experience use", "input": loaded,
        "sessionId": feedback["task_id"], "metadata": {"task_id": feedback["task_id"], "load_id": feedback["load_id"]}})
    return box.enqueue(feedback["id"], {
        "name": "writing-experience-" + feedback["experience_id"], "value": feedback["rating"],
        "dataType": "CATEGORICAL", "traceId": trace_id(context_id), "comment": feedback["reason"],
        "metadata": {key: feedback[key] for key in ("task_id", "load_id", "export_id", "experience_id", "release_id", "revision", "content_hash")}}, kind="score")


def recover(root: Path) -> dict:
    root = Path(root)
    store = Store(root)
    from .integrations import SourceImporter
    importer = SourceImporter(root)
    restored_imports = importer.recover_saved()
    turns = 0
    feedback_count = 0
    for task in store.list_tasks():
        for turn in task["turns"]:
            enqueue_turn(root, task, turn)
            turns += 1
    for path in sorted((root / "usage/feedback").glob("*.json")):
        enqueue_feedback(root, read_json(path))
        feedback_count += 1
    # Imports retain their normalized messages, so queue recovery never needs
    # the original external file to remain available.
    imports = 0
    for path in sorted((root / "imports").glob("*.json")):
        record = read_json(path)
        if record.get("messages") and record.get("task_id"):
            session = record.get("session_id")
            if not session:
                # Text-import capture contains the stable session assigned by importer.
                session = (record.get("capture_result") or {}).get("session_id")
            if session:
                importer._enqueue_messages(record["task_id"], session, record.get("source", "manual"), record["messages"])
                imports += 1
    return {"turns_reconciled": turns, "feedback_reconciled": feedback_count,
            "imports_reconciled": imports, "saved_imports_recovered": restored_imports,
            "git_retries": store.retry_git(), "delivery": Outbox(root).status(), "network_requests": 0}
