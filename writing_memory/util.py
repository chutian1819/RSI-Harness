"""Small, dependency-free durable storage primitives."""
from __future__ import annotations

import contextlib
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from datetime import datetime, timezone
from uuid import uuid4

_locks: dict[str, threading.RLock] = {}
_lock_guard = threading.Lock()
_held = threading.local()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def digest(text: str | bytes) -> str:
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def text_diff(before: str, after: str, fromfile="before", tofile="after") -> str:
    """Keep final lines separate even when manuscripts have no trailing newline."""
    lines = difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=fromfile, tofile=tofile)
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def validate_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", value) or ".." in value:
        raise ValueError("编号只能包含字母、数字、下划线、点和短横线，且不能含 ..")
    return value


def read_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_write(path: str | Path, content: str | bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = content.encode("utf-8") if isinstance(content, str) else content
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: str | Path, obj) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


@contextlib.contextmanager
def file_lock(root: str | Path):
    """Serialize threads/processes; nested calls in one thread are reentrant."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    key = str(root)
    with _lock_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held, "roots", set())
        if key in held:
            yield
            return
        with (root / ".lock").open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            _held.roots = held | {key}
            try:
                yield
            finally:
                _held.roots = held
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
