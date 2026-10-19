"""最小的 DeepSeek 文本生成封装（非流式）。

用途：产线文档（立项书 / 研讨发言 / WBS / 发布清单 / 复盘）。
失败一律返回 None，由调用方回落到模板 —— 文档生成不能把项目卡死。
"""
from __future__ import annotations

import os

import httpx

API_URL = os.environ.get("LLM_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")


def _key() -> str | None:
    return os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")


def complete(prompt: str, system: str | None = None, *, max_tokens: int = 1800,
             temperature: float = 0.5, timeout: float = 120.0) -> str | None:
    key = _key()
    if not key:
        return None
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    body = {"model": MODEL, "messages": msgs, "temperature": temperature,
            "max_tokens": max_tokens}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for trust_env in (False, True):
        try:
            with httpx.Client(timeout=timeout, trust_env=trust_env) as c:
                r = c.post(API_URL, json=body, headers=headers)
            if r.status_code != 200:
                continue
            data = r.json()
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if text and text.strip():
                return text.strip()
        except Exception:                      # noqa: BLE001
            continue
    return None
