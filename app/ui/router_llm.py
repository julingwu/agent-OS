"""LLM 兜底路由：关键词没命中时，把原话丢给便宜模型换一个 JSON。

约束：
  * 只在关键词**完全没命中**时调用（省 token、快）。
  * 输出必须是 {"tags": [...], "reason": "..."}，解析失败就当没命中。
  * 任何异常（超时/没 key/网络）都吞掉并返回 None —— 路由不能把消息卡死。
"""
from __future__ import annotations

import json
import os
import re

import httpx

ALLOWED = ["dev", "design", "content", "data", "qa", "translate"]
API_URL = os.environ.get("ROUTER_LLM_URL", "https://api.deepseek.com/chat/completions")
MODEL = os.environ.get("ROUTER_LLM_MODEL", "deepseek-chat")

SYSTEM = f"""你是任务路由器。把用户需求映射到下列标签（可多选）：
{", ".join(ALLOWED)}
只输出 JSON：{{"tags": ["dev"], "reason": "一句话理由"}}
规则：
- 既要写代码又要写文案 → ["dev","content"]
- 只做界面 → ["design"]；只有数据分析 → ["data"]
- 判断不了 → {{"tags": [], "reason": "..."}}"""


def _key() -> str | None:
    return os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")


def _extract(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    tags = [t for t in (data.get("tags") or []) if t in ALLOWED]
    return {"tags": tags[:3], "reason": str(data.get("reason") or "")[:120]}


def classify(text: str, timeout: float = 20.0) -> dict | None:
    key = _key()
    if not key:
        return None
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": text[:500]}],
        "temperature": 0,
        "max_tokens": 120,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for trust_env in (False, True):        # 先直连，失败再走系统代理
        try:
            with httpx.Client(timeout=timeout, trust_env=trust_env) as c:
                r = c.post(API_URL, json=body, headers=headers)
            if r.status_code != 200:
                continue
            out = _extract(r.json()["choices"][0]["message"]["content"])
            if out is not None:
                return out
        except Exception:                  # noqa: BLE001
            continue
    return None
