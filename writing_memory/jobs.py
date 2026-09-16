"""Durable single-worker queue. Uncertain calls require an explicit retry."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .util import atomic_json, file_lock, read_json, utc_now, validate_id


class Jobs:
    def __init__(self, workbench):
        self.app = workbench
        self.root = workbench.state / "web-jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="writing-model")
        self.futures = {}
        with file_lock(self.root):
            for path in self.root.glob("*.json"):
                item = read_json(path)
                if item["state"] in {"queued", "running"}:
                    item.update(state="interrupted", error="上次服务已停止，是否产生模型费用尚不确定。记录已保留；请显式重试。", updated_at=utc_now())
                    atomic_json(path, item)

    def path(self, job_id):
        return self.root / (validate_id(job_id) + ".json")

    def list(self):
        return sorted([read_json(p) for p in self.root.glob("*.json")], key=lambda j: j["created_at"], reverse=True)

    def submit(self, job_id, kind, doc_id, payload=None, retry=False):
        payload = payload or {}
        with file_lock(self.root):
            path = self.path(job_id)
            old = read_json(path) if path.exists() else None
            if old:
                if (old["kind"], old["document_id"], old["payload"]) != (kind, doc_id, payload):
                    raise ValueError("同一操作编号不能复用为其他请求")
                if old["state"] in {"queued", "running", "succeeded"}:
                    return old
                if not retry:
                    return old
            item = {"id": job_id, "kind": kind, "document_id": doc_id, "payload": payload,
                    "state": "queued", "created_at": old["created_at"] if old else utc_now(),
                    "updated_at": utc_now(), "attempt": old["attempt"] + 1 if old else 1, "retry": retry}
            atomic_json(path, item)
            self.futures[job_id] = self.pool.submit(self._run, job_id)
            return item

    def _run(self, job_id):
        with file_lock(self.root):
            item = read_json(self.path(job_id))
            if item["state"] != "queued":
                return
            item.update(state="running", updated_at=utc_now())
            atomic_json(self.path(job_id), item)
        try:
            if item["kind"] == "draft":
                self.app.prepare_draft(item["document_id"], event_id=job_id, **item["payload"])
                result = self.app.generate_draft(item["document_id"], job_id, item["retry"])
                result = {"draft_id": result["id"]}
            elif item["kind"] == "extract":
                result = self.app.extract_once(item["document_id"], job_id, item["retry"])
            else:
                raise ValueError("不支持的作业")
            item.update(state="succeeded", result=result)
        except Exception as exc:
            item.update(state="failed", error=str(exc))
        item["updated_at"] = utc_now()
        with file_lock(self.root):
            atomic_json(self.path(job_id), item)

    def retry(self, job_id):
        item = read_json(self.path(job_id))
        return self.submit(job_id, item["kind"], item["document_id"], item["payload"], retry=True)

    def cancel(self, job_id):
        with file_lock(self.root):
            item = read_json(self.path(job_id))
            if item["state"] != "queued":
                raise ValueError("只能取消排队中的操作。已发送的请求会保留结果，不会自动采用；退出服务后也不会自动重发。")
            item.update(state="cancelled", updated_at=utc_now())
            atomic_json(self.path(job_id), item)
            future = self.futures.get(job_id)
            if future:
                future.cancel()
            return item

    def schedule_learning(self, doc_id):
        task = self.app.task(doc_id)
        for event_id in task.get("learning_due", {}):
            self.submit("learn_" + event_id, "extract", doc_id)

    def recover_learning(self):
        for doc in self.app.documents_list():
            self.schedule_learning(doc["id"])

    def close(self):
        # Finish an already-issued request; pending queue remains on disk for explicit retry.
        self.pool.shutdown(wait=True, cancel_futures=True)
