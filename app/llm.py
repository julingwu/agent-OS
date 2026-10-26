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


# ---------------------------------------------------------------- 统一离线阀门（v5.4.1）
# 历史缺陷：M0_MOCK_LLM 只在 runner_native._native_mode() 里生效，本模块两个函数都没有阀门。
# 结果 flows.classify -> llm.complete() 会绕过 mock 直接发 HTTP（v5.4.0 封版时实测：本机
# .env 有 key 就会真的调一次 DeepSeek，且测试是否通过取决于这台机器有没有 .env）。
# 现在把阀门收到本模块，chat() 与 complete() 共用同一判定，优先级与 _native_mode 一致：
#   显式 M0_LLM_MODE > M0_MOCK_LLM > 有 key 才联网
_OFFLINE_MODES = {"off", "mock", "offline", "template", "0", "false", "no"}
_LIVE_MODES = {"live", "on", "llm", "real", "1", "true", "yes"}


def _llm_mode() -> str:
    """'off' 或 'live'。显式开关优先于 M0_MOCK_LLM（与 runner_native 同序）。"""
    raw = (os.environ.get("M0_LLM_MODE") or "auto").strip().lower()
    if raw in _OFFLINE_MODES:
        return "off"
    if raw in _LIVE_MODES:
        return "live"
    return "off" if os.environ.get("M0_MOCK_LLM") == "1" else "live"


def is_offline() -> bool:
    """当前进程是否禁止一切真实模型调用。"""
    return _llm_mode() == "off"


# 可选的使用量接收方：谁有 run/项目上下文，谁就 set_usage_sink()。没注册就只进本地计数。
_usage_sink = None
_usage_totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


def set_usage_sink(fn) -> None:
    """注册一个 callable(kind: str, usage: dict) 来接管用量记账；传 None 取消。"""
    global _usage_sink
    _usage_sink = fn


def usage_totals() -> dict:
    """本进程内 complete() 的累计用量（chat() 的用量由调用方自己记）。"""
    return dict(_usage_totals)


def _record_usage(kind: str, usage: dict) -> None:
    _usage_totals["calls"] += 1
    _usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    _usage_totals["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    if _usage_sink is not None:
        try:
            _usage_sink(kind, usage)
        except Exception:                       # noqa: BLE001 - 记账失败不能拖垮生成
            pass



def chat(messages: list[dict], *, tools: list[dict] | None = None,
         max_tokens: int = 4000, temperature: float = 0.3,
         timeout: float = 180.0) -> dict | None:
    """带工具调用的单轮对话，返回 {'message': {...}, 'usage': {...}}。

    与 complete() 的区别：这个必须拿到 usage —— 真实 token 数要进 finance 账本和
    遥测，不能凭空估。失败返回 None，由调用方决定回落还是报错。
    """
    key = _key()
    if not key or is_offline():
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


def complete_with_usage(prompt: str, system: str | None = None, *,
                        max_tokens: int = 1800, temperature: float = 0.5,
                        timeout: float = 120.0) -> tuple[str | None, dict]:
    """同 complete()，但把 API 返回的 usage 一起带出来。

    为什么要这个：complete() 以前把 response 里的 usage 整个丢掉，所有走它的路径
    （意图分类、立项书/研讨/WBS/复盘文档）在 FinOps 上一律记 0 —— 成本黑洞。
    需要记账的调用方用 `set_usage_sink()` 注册接收器，或直接用本函数拿 (text, usage)。
    """
    key = _key()
    if not key or is_offline():
        return None, {}
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
                raw = data.get("usage") or {}
                usage = {"prompt_tokens": int(raw.get("prompt_tokens") or 0),
                         "completion_tokens": int(raw.get("completion_tokens") or 0)}
                _record_usage("complete", usage)
                return text.strip(), usage
        except Exception:                      # noqa: BLE001
            continue
    return None, {}


def complete(prompt: str, system: str | None = None, *, max_tokens: int = 1800,
             temperature: float = 0.5, timeout: float = 120.0) -> str | None:
    """单轮文本生成。离线阀门与 chat() 完全一致，不联网时直接返回 None。"""
    text, _usage = complete_with_usage(prompt, system, max_tokens=max_tokens,
                                       temperature=temperature, timeout=timeout)
    return text
