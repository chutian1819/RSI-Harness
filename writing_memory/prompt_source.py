"""Fetch versioned prompts from Langfuse without creating a second editor."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import Store
from .util import atomic_json, digest, utc_now


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_prompt(root: Path, name: str, version: int | None = None, label: str = "production") -> dict:
    if not name.strip():
        raise ValueError("提示词名称不能为空")
    if version is not None and (isinstance(version, bool) or version < 1):
        raise ValueError("提示词版本必须是正整数")
    host = os.environ.get("LANGFUSE_HOST", "").rstrip("/")
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    url = urlparse(host)
    if not public or not secret or not host:
        raise ValueError("请在本机配置 LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY")
    if url.username or url.password or url.query or url.fragment or not url.hostname:
        raise ValueError("LANGFUSE_HOST 必须是无凭据的服务地址")
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("远程 Langfuse 地址必须使用 HTTPS")
    query = urlencode({"version": version} if version is not None else {"label": label})
    request = Request(host + "/api/public/v2/prompts/" + quote(name, safe="") + "?" + query,
                      headers={"Authorization": "Basic " + base64.b64encode(f"{public}:{secret}".encode()).decode(), "Accept": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            data = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Langfuse 提示词读取失败：HTTP {exc.code}，请核查项目及版本") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Langfuse 提示词读取失败：网络不可用；未使用其他提示词替代") from None
    if not isinstance(data, dict) or data.get("name") != name or data.get("type") != "text" or not isinstance(data.get("prompt"), str):
        raise ValueError("服务未返回指定名称的 text 提示词")
    actual_version = data.get("version")
    if not isinstance(actual_version, int) or isinstance(actual_version, bool) or actual_version < 1:
        raise ValueError("服务未提供有效的提示词版本")
    if version is not None and actual_version != version:
        raise ValueError("返回的提示词版本与请求不一致")
    snapshot = {"provider": "langfuse", "name": name, "version": actual_version, "type": "text",
                "prompt": data["prompt"], "content_hash": digest(data["prompt"]), "labels": data.get("labels", []),
                "fetched_at": utc_now(), "host": host}
    path = Path(root) / "prompt_snapshots" / f"{digest(host + ':' + name)[:20]}-v{actual_version}-{snapshot['content_hash'][:12]}.json"
    atomic_json(path, snapshot)
    Store(root).commit_path(path.resolve(), f"Snapshot Langfuse prompt {name} version {actual_version}")
    return snapshot
