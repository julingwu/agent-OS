"""Sandbox environment shared by every runner.

Two problems this solves (both real, both found on the machine):

1. **Node/npm invisible to the agent.** The ACP child only sees what we pass it.
   If uvicorn was started from a shell without Node on PATH, `npm` inside a task
   dies with "not recognized". We probe the usual Windows locations and prepend
   whatever we find.
2. **npm writes outside the workspace.** By default npm puts its cache in
   `%LOCALAPPDATA%\\npm-cache`, which a Low-integrity sandbox cannot write, and
   `npm install` fails with EPERM. We point every npm path into the task dir.
"""
from __future__ import annotations

import os
import pathlib

CANDIDATES = [
    r"C:\Program Files\nodejs",
    r"C:\Program Files (x86)\nodejs",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\nodejs"),
    os.path.expandvars(r"%APPDATA%\npm"),
    os.path.expandvars(r"%NVM_HOME%"),
    os.path.expandvars(r"%NVM_SYMLINK%"),
]


def node_bin_dirs() -> list[str]:
    seen: list[str] = []
    for raw in CANDIDATES:
        if "%" in raw:                     # expandvars left it alone -> not set
            continue
        p = pathlib.Path(raw)
        if not p.is_dir():
            continue
        # AppData\Roaming\npm has npm.cmd but no node.exe; still worth adding
        if (p / "node.exe").exists() or (p / "npm.cmd").exists():
            s = str(p)
            if s not in seen:
                seen.append(s)
    return seen


def build_env(workspace: pathlib.Path | str, base: dict | None = None) -> dict:
    """os.environ + Node on PATH + npm/npx caches pinned inside the task dir."""
    env = dict(base or os.environ)
    dirs = node_bin_dirs()
    path = env.get("PATH", "")
    missing = [d for d in dirs if d.lower() not in path.lower()]
    if missing:
        env["PATH"] = os.pathsep.join(missing + [path]) if path else os.pathsep.join(missing)

    ws = pathlib.Path(workspace)
    cache = ws / ".npm-cache"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    env.update({
        "npm_config_cache": str(cache),
        "npm_config_fund": "false",
        "npm_config_audit": "false",
        "npm_config_update_notifier": "false",
        # 让 npx 不去碰用户目录里的全局包
        "npm_config_prefix": str(ws / ".npm-global"),
    })

    try:
        from .. import llm_config
        cfg = llm_config.resolve_llm_config()
        if cfg.get("api_key"):
            env.setdefault("OPENAI_API_KEY", cfg["api_key"])
            env.setdefault("DEEPSEEK_API_KEY", cfg["api_key"])
        if cfg.get("base_url"):
            env.setdefault("OPENAI_BASE_URL", cfg["base_url"])
            env.setdefault("LLM_API_BASE", cfg["base_url"])
            env.setdefault("MODEL_BASE_URL", cfg["base_url"])
        if cfg.get("chat_url"):
            env.setdefault("LLM_URL", cfg["chat_url"])
        if cfg.get("model"):
            env.setdefault("LLM_MODEL", cfg["model"])
    except Exception:
        pass

    return env


def which_node(env: dict | None = None) -> str | None:
    """Diagnostic helper: 真实解析一次 PATH，确认 node 能被找到。"""
    import shutil
    e = env or build_env(".")
    return shutil.which("node", path=e.get("PATH", ""))
