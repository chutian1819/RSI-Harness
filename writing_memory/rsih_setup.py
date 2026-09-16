"""First-run setup for a colleague's machine; no private bootstrap files needed."""
from __future__ import annotations

import getpass
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

from .util import atomic_json, atomic_write, file_lock, read_json


def locate_binary(state: Path, explicit=None):
    runtime = state / "runtime.json"
    configured = read_json(runtime).get("rsih_binary") if runtime.exists() else None
    return Path(explicit or configured or shutil.which("rsih") or Path.home() / ".local/bin/rsih").expanduser().resolve()


def setup(state: Path, binary=None, skip_key=False, replace_key=False, model_id=None):
    state = Path(state).expanduser().resolve()
    if skip_key and replace_key:
        raise ValueError("--skip-key 与 --replace-key 不能同时使用")
    if model_id is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", model_id):
        raise ValueError("模型名称只能包含字母、数字、点、短横线和下划线")
    if state.exists() and any(state.iterdir()) and not any((state / p).exists() for p in (
            "agent/models.json", "runtime.json", ".setup-in-progress", "manuscript-assets")):
        raise ValueError("该目录已有其他内容，请指定空目录或现有写作工作台目录")
    executable = locate_binary(state, binary)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("找不到可执行的 RSIH。请先按 docs/GETTING_STARTED.md 安装引擎，或通过 --rsih 指定路径")
    if not shutil.which("git"):
        raise ValueError("缺少 Git；请先安装 Git 后重试")
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o700)
    with file_lock(state / "setup-lock"):
        atomic_write(state / ".setup-in-progress", "setup\n")
        agent = state / "managed-agent"
        agent.mkdir(exist_ok=True)
        env = dict(os.environ, RSIH_CODING_AGENT_DIR=str(agent))
        version = subprocess.run([str(executable), "--version"], cwd=state, env=env,
                                 capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        models_path = state / "agent/models.json"
        models = read_json(models_path) if models_path.exists() else {"providers": {}}
        models.setdefault("providers", {}).setdefault("deepseek-study", {
            "baseUrl": "https://api.deepseek.com", "api": "openai-completions",
            "apiKey": "$DEEPSEEK_API_KEY", "models": [{"id": "deepseek-flash",
                "name": "DeepSeek Flash", "reasoning": False, "input": ["text"],
                "contextWindow": 65536, "maxTokens": 8192,
                "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": False}}]})
        if model_id:
            configured_models = models["providers"]["deepseek-study"]["models"]
            if not any(item["id"] == model_id for item in configured_models):
                configured_models.append({"id": model_id, "name": model_id, "reasoning": False,
                    "input": ["text"], "contextWindow": 65536, "maxTokens": 8192,
                    "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": False}})
        atomic_json(models_path, models)
        settings_path = state / "writing-settings.json"
        settings = read_json(settings_path) if settings_path.exists() else {"model": "deepseek-study/deepseek-flash", "max_context_bytes": 60000}
        if model_id:
            settings["model"] = "deepseek-study/" + model_id
        atomic_json(settings_path, settings)
        genome = state / "genomes/writing-demo"
        # Existing Genomes belong to the user. Never replace them during setup.
        if not (genome / "genome.json").exists():
            template = Path(__file__).parent / "templates/writing-genome"
            for source in sorted(template.rglob("*")):
                if source.is_file() and source.name != "genome.json":
                    target = genome / source.relative_to(template)
                    if not target.exists(): atomic_write(target, source.read_bytes())
            atomic_write(genome / "genome.json", (template / "genome.json").read_bytes())
        validation = subprocess.run([str(executable), "genome", "validate", str(genome)],
                                    cwd=state, env=env, capture_output=True, text=True, timeout=30)
        if validation.returncode:
            raise ValueError("Genome 校验未通过：" + (validation.stderr or validation.stdout)[-1200:])
        credential_path = state / "credentials.json"
        exists = credential_path.exists() and bool(read_json(credential_path).get("DEEPSEEK_API_KEY"))
        if not skip_key and (not exists or replace_key):
            if not os.environ.get("DEEPSEEK_API_KEY") and not sys.stdin.isatty():
                raise ValueError("请在交互式终端运行 setup 以隐藏输入密钥，或设置 DEEPSEEK_API_KEY；离线配置可用 --skip-key")
            key = os.environ.get("DEEPSEEK_API_KEY") or getpass.getpass(
                "请输入你自己的 DeepSeek API Key（输入不显示，将保存在本机）：")
            if not key.strip():
                raise ValueError("未输入密钥。配置已保留，可重新运行 setup；也可用 --skip-key 仅配置本地功能")
            atomic_json(credential_path, {"DEEPSEEK_API_KEY": key.strip()})
        if credential_path.exists(): credential_path.chmod(0o600)
        atomic_json(state / "runtime.json", {"rsih_binary": str(executable), "setup_version": 1})
        (state / ".setup-in-progress").unlink(missing_ok=True)
    return {"state": str(state), "rsih_version": version, "genome_valid": True,
            "credential_ready": bool(os.environ.get("DEEPSEEK_API_KEY")) or
                (credential_path.exists() and bool(read_json(credential_path).get("DEEPSEEK_API_KEY"))),
            "model_request_made": False,
            "next": "运行 writing-memory-rsih web；在浏览器中新建材料，再输入修改要求。menu 是旧版终端入口。"}


def diagnose(state: Path):
    state = Path(state).expanduser().resolve()
    binary = locate_binary(state)
    credentials = state / "credentials.json"
    saved = read_json(credentials) if credentials.exists() else {}
    checks = {"git_available": bool(shutil.which("git")),
              "rsih_executable": binary.is_file() and os.access(binary, os.X_OK),
              "model_config_exists": (state / "agent/models.json").exists(),
              "genome_exists": (state / "genomes/writing-demo/genome.json").exists(),
              "credential_available": bool(os.environ.get("DEEPSEEK_API_KEY") or saved.get("DEEPSEEK_API_KEY")),
              "credential_permissions_ok": not credentials.exists() or not bool(credentials.stat().st_mode & 0o077)}
    return {"state": str(state), "checks": checks, "ready": all(checks.values()),
            "network_requests": 0, "note": "本命令只检查本机配置，不验证 API 余额或网络连通。"}
