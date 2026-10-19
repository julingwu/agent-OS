"""Intent recognition for the Octo bridge.

Before this existed every human sentence became a task — "你好" produced a
task with acceptance criteria and a rejected verdict, which is a terrible
experience and burns tokens. Now a cheap model call decides:

    chat  -> the PM answers in natural language, nothing is created
    work  -> the PM acknowledges ("收到，正在拆解…"), then the shell gets a task

Failure policy: if the model is unreachable or returns junk, fall back to a
keyword heuristic instead of blocking the message.
"""
from __future__ import annotations

import json
import logging
import os
import re

import httpx

log = logging.getLogger("octo.intent")

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

WORK_HINTS = re.compile(
    r"(写|做|生成|开发|实现|整理|翻译|分析|抓|爬|设计|画|产出|输出|帮我|给我|"
    r"文案|文章|脚本|代码|页面|报告|方案|表格|PPT|摘要|总结)", re.I)
CHAT_HINTS = re.compile(r"^(你好|您好|hi|hello|在吗|在不在|早上好|下午好|晚上好|"
                        r"谢谢|辛苦了|ok|好的|收到|嗯|哈哈|嗨)", re.I)

SYSTEM = """你是「小文」，一家 AI 协作公司的 PM（项目经理），在群里和董事长对话。语气亲切、简洁、专业，中文回答。

判断董事长这句话是哪一类：
- chat：闲聊、问候、确认、表扬、闲问，不需要交付任何东西。
- work：需要产出具体交付物（文案、代码、页面、报告、方案、分析、整理等）。

只输出一个 JSON，不要 markdown 代码块，格式：
{"intent":"chat|work","reply":"<你对董事长说的话，1-2 句>"}

规则：
- chat：reply 自然亲切地回应，不要提任务、不要提验收、不要承诺产出。
- work：reply 第一句表示收到，并说明你正在拆解任务、会拉相关同事进群；不要说出具体交付细节。
"""


def _endpoint() -> tuple[str, str, str]:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("LLM_API_BASE", DEFAULT_BASE).rstrip("/")
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    return base, key, model


def _fallback(text: str) -> dict:
    t = (text or "").strip()
    if CHAT_HINTS.search(t) and len(t) <= 12:
        return {"intent": "chat", "reply": "在的，董事长。有事随时吩咐。", "source": "fallback"}
    if WORK_HINTS.search(t):
        return {"intent": "work", "reply": "收到，我正在拆解任务，稍后拉人对齐。", "source": "fallback"}
    return {"intent": "chat", "reply": "收到，我在这儿。需要我做什么直接说就行。", "source": "fallback"}


def classify(text: str, timeout: float = 12.0, prefs: dict[str, str] | None = None,
             facts: str = "", history: str = "") -> dict:
    """prefs 决定称呼，facts 是查库结果，history 是群里最近几句。

    三者都拼进 system 消息：模型只负责措辞，事实由服务端提供，
    这样它不会把「我的秘书」答成「我没有秘书」。
    """
    base, key, model = _endpoint()
    if not key:
        return _fallback(text)

    system = SYSTEM
    NL = chr(10)
    if prefs and prefs.get("boss_title"):
        system += NL + NL + "【重要偏好】称呼用户为「" + str(prefs["boss_title"]) + "」。"
    if facts:
        system += (NL + NL + "以下是刚从数据库查到的真实情况，回答时只能依据它，"
                   "不要编造：" + NL + facts)
    if history:
        system += NL + NL + history
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": text}],
        "temperature": 0.3,
        "max_tokens": 220,
    }
    try:
        with httpx.Client(base_url=base, timeout=timeout, trust_env=False) as c:
            r = c.post("/chat/completions",
                       headers={"Authorization": f"Bearer {key}"}, json=payload)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:                      # noqa: BLE001
        log.warning("intent call failed (%s), falling back", exc)
        return _fallback(text)

    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return _fallback(text)
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return _fallback(text)
    intent = str(data.get("intent", "")).lower()
    reply = str(data.get("reply", "")).strip()
    if intent not in ("chat", "work") or not reply:
        return _fallback(text)
    return {"intent": intent, "reply": reply, "source": "llm"}
