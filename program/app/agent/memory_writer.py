"""一轮对话结束后，抽取值得长期记住的信息。

只在"这轮确实有内容"时调用一次小模型（flash），一次 300~600 token：
  * 闲聊/寒暄 → 返回 []，不写；
  * 用户偏好、项目决定、稳定事实 → 1~3 条；
  * 写入走 memory.add 去重，重复内容不重复占位。
失败一律静默（记忆失败不该影响对话本身）。
"""

from __future__ import annotations

import json
import re

from .. import bot_memory as memory
from .. import llm

MAX_ITEMS = 3
MAX_INPUT = 1600

PROMPT = """你是记忆整理器：从一轮对话里抽取**值得长期记住**的信息，供这个助手以后使用。

只抽这些类型：
- fact：关于用户/项目/环境的稳定事实（"我们的服务器是腾讯云 4C8G"）
- preference：用户的偏好或习惯（"回答尽量先给结论"）
- decision：已经做出的决定（"选方案 B，不用 Kubernetes"）

不要抽：寒暄、临时情绪、一次性的执行细节、助手自己编的推测、已经在对话里明显是假设的内容。
**只抽用户在消息里明确说出或确认的信息。** 助手回答里复述的内容不算新信息 —— 用户只是提问、
助手凭记忆回答的，不要当成学到新东西。每条不超过 80 字，独立成句，能脱离上下文看懂。
最多 3 条，宁少勿多。

只输出 JSON 数组，不要解释：
[{"topic":"公司","kind":"fact","content":"...","importance":1}]

topic **只能**从下面选一个（同槽位新值覆盖旧值，所以同类信息必须用同一个槽位）：
公司 / 产品 / 客户 / 成员 / 偏好 / 回答风格 / 技术栈 / 服务器 / 流程 / 其它
认不出类别就用"其它"。不要自造新槽位名。
content 只写**当前**事实，不要写"原名 X，现已改为 Y"这种变更史。
importance: 1=一般 2=比较重要 3=很重要。没有可记的就输出 []。"""


def _parse(text: str) -> list[dict]:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    items = []
    for entry in data[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        try:
            importance = int(entry.get("importance") or 1)
        except (TypeError, ValueError):
            importance = 1
        items.append({"kind": str(entry.get("kind") or "fact"),
                      "content": content[:400],
                      "topic": str(entry.get("topic") or "").strip()[:40],
                      "importance": max(1, min(importance, 3))})
    return items


def capture(conn, *, bot_id: str, session_id: str, user_text: str,
            reply_text: str, model: str | None = None,
            source_event_id: int | None = None) -> list[dict]:
    """抽取并落库，返回真正新增的记忆（重复的不算）。"""
    written: list[dict] = []
    for item in extract(user_text=user_text, reply_text=reply_text, model=model):
        saved = memory.add(conn, bot_id, item["content"], kind=item["kind"],
                           importance=item["importance"], session_id=session_id,
                           topic=item.get("topic") or "", source_event_id=source_event_id)
        if saved:
            written.append(saved)
    return written

def extract(*, user_text: str, reply_text: str, model: str | None = None) -> list[dict]:
    """调模型抽记忆；任何失败都返回空列表。"""
    if len((user_text or "").strip()) < 6 or len((reply_text or "").strip()) < 40:
        return []
    try:
        resp = llm.chat(
            [{"role": "system", "content": PROMPT},
             {"role": "user", "content": f"用户说：{user_text[:MAX_INPUT]}\n\n助手回答：{reply_text[:MAX_INPUT]}"}],
            temperature=0.1, max_tokens=400, model=model,
        )
    except Exception:  # noqa: BLE001
        return []
    if not resp:
        return []
    return _parse((resp.get("message") or {}).get("content") or "")
