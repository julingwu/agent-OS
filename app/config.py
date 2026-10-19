"""Minimal .env loader (no extra dependency).

Why: the runner spawns `dsh` as a child, and headless/ACP do not read the
desktop credential store — they need DEEPSEEK_API_KEY in the environment of the
process that starts them. Loading .env into os.environ at startup makes
`uvicorn app.main:app` self-sufficient, instead of depending on how the shell
happened to be launched.
"""
from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_env(path: str | pathlib.Path | None = None) -> dict[str, str]:
    p = pathlib.Path(path) if path else (ROOT / ".env")
    loaded: dict[str, str] = {}
    if not p.is_file():
        return loaded
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not key:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)      # real environment always wins
    return loaded
