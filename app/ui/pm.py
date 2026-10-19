"""项目制协作：PM 先出方案，老板点头，才允许动代码。

单兵执行的问题很具体：一句「重构前端」直接变成一个任务，PM 自己冲上去写，
预算烧完、状态机报错、老板什么也没看到。这里把流程拆成两阶段：

    阶段一  设计：PM 建群 → 设计同学出一份可预览的静态稿（index.html）
             → 群里发【设计确认卡】，等老板点「同意方案」
    阶段二  开发：老板同意后，PM 才派开发任务

PM 自己不写代码 —— 它只做三件事：识别项目、选人、发消息。
"""
from __future__ import annotations

import re
import sqlite3

from . import store

# 什么时候算「项目」而不是「一件事」
PROJECT_HINTS = re.compile(
    r"(重构|改版|新功能|从零|整套|系统|平台|小程序|App|仪表盘|大屏|落地页|官网|"
    r"设计稿|原型|多页面|工程化)", re.I)

DESIGNER_HINTS = re.compile(r"(设计|UI|UX|视觉|美术)", re.I)
DEV_HINTS = re.compile(r"(前端|后端|开发|工程|全栈|代码)", re.I)
QA_HINTS = re.compile(r"(测试|QA|质量|验收)", re.I)


def is_project(text: str) -> bool:
    return bool(PROJECT_HINTS.search(text or ""))


def _score(member: dict, pattern: re.Pattern) -> int:
    title = f"{member.get('title') or ''} {member.get('name') or ''}"
    return 2 if pattern.search(title) else 0


def pick(members: list[dict], pattern: re.Pattern, fallback_index: int) -> dict:
    """按职位关键词挑人；挑不到就按加入顺序兜底。"""
    if len(members) <= 1:
        return members[0]
    ranked = sorted(members[1:], key=lambda m: _score(m, pattern), reverse=True)
    if ranked and _score(ranked[0], pattern) > 0:
        return ranked[0]
    idx = min(fallback_index, len(members) - 1)
    return members[idx]


def roles(members: list[dict]) -> dict:
    pm = members[0]
    designer = pick(members, DESIGNER_HINTS, 1)
    dev = pick(members, DEV_HINTS, 1 if designer is not members[1] else min(2, len(members) - 1))
    if dev["employee_id"] == designer["employee_id"] and len(members) >= 3:
        dev = members[2]
    qa = pick(members, QA_HINTS, len(members) - 1)
    return {"pm": pm, "designer": designer, "dev": dev, "qa": qa}


def design_brief(text: str, chat_title: str) -> str:
    """给设计同学的活：先给方案，不写实现。"""
    return (
        f"项目：{chat_title}\n"
        f"老板的需求：{text}\n\n"
        "你现在只做【阶段一：设计】。请产出一份 index.html 静态设计稿：\n"
        "- 用 Tailwind CDN，深色玻璃拟态风格；\n"
        "- 必须体现：左侧栏三个 Tab、中间聊天流（含验收卡样式）、右侧详情栏；\n"
        "- 顶部用一行大字写出配色方案与布局说明（这是给老板看的设计要点）；\n"
        "- 只要静态稿，不要接后端、不要写复杂 JS。\n"
        "完成后只回复 DONE。"
    )


def dev_brief(text: str, plan: str = "") -> str:
    extra = f"\n已确认的设计要点：\n{plan[:800]}\n" if plan else ""
    return (
        f"老板已确认设计方案，进入【阶段二：开发实施】。\n原需求：{text}\n{extra}"
        "请产出 index.html：完整可运行的单页，Tailwind CDN，含左侧栏三 Tab、"
        "聊天流（Markdown 气泡 + 验收卡三按钮）、右侧详情栏，响应式。\n"
        "完成后只回复 DONE。"
    )


def announce(conn: sqlite3.Connection, chat_id: str, roles_map: dict, text: str) -> None:
    pm, designer, dev = roles_map["pm"]["name"], roles_map["designer"]["name"], roles_map["dev"]["name"]
    store.add_message(
        conn, chat_id, "agent",
        f"收到，这是项目制任务，我不自己写代码。\n"
        f"1) 先让 {designer} 出设计方案（静态稿）；\n"
        f"2) 方案发您确认，您点头后我再派 {dev} 开发。\n"
        f"需求摘要：{text[:60]}",
        author=pm, kind="text")


def design_confirm_message(chat_id: str, design_task_id: str, plan_html: str | None) -> str:
    return "设计方案已出，请老板确认后再进入开发。"


def approve_prompt(text: str, design_task_id: str, plan: str = "") -> str:
    return dev_brief(text, plan)
