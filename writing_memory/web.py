"""Loopback-only browser entry. Local reference uploads; no account server or remote publishing."""
from __future__ import annotations

from contextlib import asynccontextmanager
import secrets
import socket
import threading
import fcntl
from pathlib import Path
from urllib.parse import urlsplit
import webbrowser

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .agent_export import agent_markdown
from .jobs import Jobs
from .references import MAX_FILE_BYTES
from .rsih_setup import diagnose
from .rsih_workspace import RsihClient
from .util import validate_id
from .workbench import Workbench


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewDocument(StrictBody):
    reference_ids: list[str] = Field(default_factory=list, max_length=30)
    title: str = Field(min_length=1, max_length=200)
    initial: str = Field(default="", max_length=200000)
    purpose: str = Field(default="", max_length=2000)
    audience: str = Field(min_length=1, max_length=200)
    document_type: str = Field(min_length=1, max_length=200)
    topic: str = Field(default="", max_length=200)


class Draft(StrictBody):
    request_id: str = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=20000)
    expected_hash: str = Field(min_length=64, max_length=64)
    keep_requirement: bool = True
    reference_ids: list[str] | None = Field(default=None, max_length=30)


class Actor(StrictBody):
    actor: str = Field(min_length=1, max_length=100)


class Manual(Actor):
    request_id: str = Field(min_length=1, max_length=100)
    content: str = Field(max_length=200000)
    instruction: str = Field(default="", max_length=20000)
    expected_hash: str
    restore_version: str | None = None


class Decision(Actor):
    decision: str
    content: str | None = Field(default=None, max_length=4000)
    scope: dict | None = None
    reviewed_overlap_ids: list[str] = Field(default_factory=list)
    replace_ids: list[str] = Field(default_factory=list)


class Requirement(StrictBody):
    enabled: bool


class Selection(StrictBody):
    ids: list[str]


def create_app(state, token=None, origin="http://127.0.0.1:8765", client=None):
    app_token = token or secrets.token_urlsafe(32)
    workbench = Workbench(state, client)
    assets = Path(__file__).parent / "static"
    jobs = Jobs(workbench)

    @asynccontextmanager
    async def lifespan(app):
        jobs.recover_learning()
        yield
        jobs.close()

    app = FastAPI(title="RSIH 写作工作台", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.workbench = workbench
    app.state.jobs = jobs
    app.state.token = app_token

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.headers.get("host") != urlsplit(origin).netloc:
            return JSONResponse({"detail": "仅接受本机工作台地址"}, status_code=403)
        if request.url.path.startswith("/api/"):
            if not secrets.compare_digest(request.headers.get("x-workbench-token", "").encode(), app_token.encode()):
                return JSONResponse({"detail": "连接凭据失效，请从终端显示的完整地址重新打开网页"}, status_code=401)
            if request.headers.get("origin") not in {None, origin}:
                return JSONResponse({"detail": "拒绝其他网站发起的请求"}, status_code=403)
            limit = MAX_FILE_BYTES if request.url.path == "/api/references" else 2 * 1024 * 1024
            chunks, length = [], 0
            async for chunk in request.stream():
                length += len(chunk)
                if length > limit:
                    return JSONResponse({"detail": "请求过大，请拆分材料（单文件上限 20 MB）"}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(FileNotFoundError)
    async def missing(request, exc):
        return JSONResponse({"detail": "记录或配置不存在，请刷新页面或先执行 setup"}, status_code=404)

    @app.get("/")
    def index():
        return FileResponse(assets / "index.html")

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.get("/assets/{name}")
    def asset(name: str):
        if name not in {"app.js", "style.css"}:
            return JSONResponse({"detail": "不存在"}, status_code=404)
        return FileResponse(assets / name)

    @app.get("/api/bootstrap")
    def bootstrap():
        return {"documents": workbench.documents_list(), "diagnostics": diagnose(workbench.state),
                "model": workbench.client.model, "version": "0.3.0"}

    @app.post("/api/documents", status_code=201)
    def create(body: NewDocument):
        values = body.model_dump()
        for key in ("title", "purpose", "audience", "document_type", "topic"):
            values[key] = values[key].strip()
        if not values["audience"] or not values["document_type"]:
            raise ValueError("请填写文种和读者，不能只输入空格")
        return workbench.create(**values)

    @app.post("/api/references", status_code=201)
    async def upload_reference(request: Request, name: str):
        return await run_in_threadpool(workbench.references.upload, name, await request.body())

    @app.post("/api/documents/{doc_id}/references")
    def attach_references(doc_id: str, body: Selection):
        return workbench.attach_references(doc_id, body.ids)

    @app.get("/api/documents/{doc_id}")
    def detail(doc_id: str):
        return workbench.detail(doc_id)

    @app.post("/api/documents/{doc_id}/drafts", status_code=202)
    def draft(doc_id: str, body: Draft):
        workbench.directory(doc_id)
        return jobs.submit(validate_id(body.request_id), "draft", doc_id, body.model_dump(exclude={"request_id"}))

    @app.post("/api/documents/{doc_id}/drafts/{draft_id}/adopt")
    def adopt(doc_id: str, draft_id: str, body: Actor):
        result = workbench.adopt(doc_id, draft_id, body.actor)
        jobs.schedule_learning(doc_id)
        return result

    @app.post("/api/documents/{doc_id}/drafts/{draft_id}/reject")
    def reject(doc_id: str, draft_id: str, body: Actor):
        return workbench.reject_draft(doc_id, draft_id, body.actor)

    @app.post("/api/documents/{doc_id}/manual")
    def manual(doc_id: str, body: Manual):
        result = workbench.save_manual(doc_id, event_id=validate_id(body.request_id), **body.model_dump(exclude={"request_id"}))
        jobs.schedule_learning(doc_id)
        return result

    @app.post("/api/documents/{doc_id}/requirements/{event_id}")
    def requirement(doc_id: str, event_id: str, body: Requirement):
        return workbench.set_requirement(doc_id, event_id, body.enabled)

    @app.get("/api/jobs")
    def job_list():
        return jobs.list()

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str):
        return jobs.retry(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return jobs.cancel(job_id)

    @app.get("/api/memory")
    def memory():
        return {"candidates": workbench.memory.candidates(), "rules": workbench.memory.rules()}

    @app.post("/api/memory/preview")
    def preview(body: dict):
        return workbench.memory.preview(body)

    @app.post("/api/memory/candidates/{candidate_id}")
    def decide(candidate_id: str, body: Decision):
        return workbench.memory.decide(candidate_id, **body.model_dump())

    @app.post("/api/memory/rules/{rule_id}/revoke")
    def revoke(rule_id: str, body: Actor):
        return workbench.memory.revoke(rule_id, body.actor)

    @app.post("/api/memory/export")
    def export(body: Selection):
        return workbench.memory.export(body.ids)

    @app.post("/api/memory/export-agent")
    def export_agent(body: dict):
        return agent_markdown(body)

    @app.post("/api/memory/import")
    def import_rules(body: dict):
        return {"ids": workbench.memory.import_bundle(body)}

    return app


def serve(state, port=8765, open_browser=True, model=None):
    state = Path(state).expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    with (state / "web-server.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("这个数据目录已有网页服务运行，请使用原来的网页，或先关闭旧服务") from None
        try:
            _serve_locked(state, port, open_browser, model)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _serve_locked(state, port=8765, open_browser=True, model=None):
    import uvicorn
    if not 1 <= port <= 65535:
        raise ValueError("端口应在 1–65535 之间")
    # Bind before launching browser, so an occupied port never receives our session link.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise ValueError("端口已被占用，请关闭旧工作台或使用 web --port 8766") from None
    token = secrets.token_urlsafe(32)
    origin = f"http://127.0.0.1:{port}"
    app = create_app(state, token, origin, RsihClient(state, model))
    url = origin + "/#token=" + token
    print("写作工作台已启动（仅本机）：" + url, flush=True)
    print("关闭时按 Ctrl+C；已经发送的模型请求会先保存结果。", flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False)).run(sockets=[sock])
    finally:
        sock.close()
