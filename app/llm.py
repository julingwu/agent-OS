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


def has_key() -> bool:
    """是否配了真实模型凭据（NativeKernel 用它决定走 LLM 还是确定性模板）。"""
    return bool(_key())


def _url() -> str:
    """运行时读环境：测试可以用本地 mock server 顶替，不需要改模块常量。"""
    return os.environ.get("LLM_URL") or API_URL


def _model() -> str:
    return os.environ.get("LLM_MODEL") or MODEL


def chat(messages: list[dict], *, tools: list[dict] | None = None,
         max_tokens: int = 4000, temperature: float = 0.3,
         timeout: float = 180.0) -> dict | None:
    """带工具调用的单轮对话，返回 {'message': {...}, 'usage': {...}}。

    与 complete() 的区别：这个必须拿到 usage —— 真实 token 数要进 finance 账本和
    遥测，不能凭空估。失败返回 None，由调用方决定回落还是报错。
    """
    key = _key()
    if not key:
        return None
    body: dict = {"model": _model(), "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last_err: Exception | None = None
    for trust_env in (False, True):
        try:
            with httpx.Client(timeout=timeout, trust_env=trust_env) as c:
                r = c.post(_url(), json=body, headers=headers)
            if r.status_code != 200:
                last_err = RuntimeError(f"http_{r.status_code}: {r.text[:200]}")
                continue
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            usage = data.get("usage") or {}
            return {
                "message": msg,
                "usage": {
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                },
                "finish_reason": choice.get("finish_reason"),
            }
        except Exception as exc:                       # noqa: BLE001
            last_err = exc
            continue
    if last_err is not None:
        raise RuntimeError(f"llm_call_failed: {last_err}") from last_err
    return None


def complete(prompt: str, system: str | None = None, *, max_tokens: int = 1800,
             temperature: float = 0.5, timeout: float = 120.0) -> str | None:
    key = _key()
    if not key:
        return None
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    body = {"model": _model(), "messages": msgs, "temperature": temperature,
            "max_tokens": max_tokens}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for trust_env in (False, True):
        try:
            with httpx.Client(timeout=timeout, trust_env=trust_env) as c:
                r = c.post(_url(), json=body, headers=headers)
            if r.status_code != 200:
                continue
            data = r.json()
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if text and text.strip():
                return text.strip()
        except Exception:                      # noqa: BLE001
            continue
    return None
