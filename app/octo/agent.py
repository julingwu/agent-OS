"""PM agent: turn one big ask into a group + a dispatch plan.

Deterministic by default (no model call, so the demo always works); if a model is
configured the plan text can be generated, but the routing decision stays explicit
and auditable.
"""
from __future__ import annotations

import os
import re
import sqlite3

from . import tools

SPLIT_RULES = [
    ("frontend", re.compile(r"(前端|页面|界面|UI|web|React|小程序|样式|落地页|官网)", re.I),
     "前端：界面与交互实现"),
    ("backend", re.compile(r"(后端|接口|API|服务|数据库|脚本|爬|Python)", re.I),
     "后端：接口与数据处理"),
    ("content", re.compile(r"(文案|文章|内容|营销|推文|脚本稿)", re.I),
     "内容：文案与素材"),
]


def plan_for(text: str) -> list[dict]:
    """Pick the lines of work this ask needs. Always returns at least one."""
    out = []
    for role, pattern, label in SPLIT_RULES:
        if pattern.search(text or ""):
            out.append({"role": role, "title": label})
    if not out:
        out.append({"role": "content", "title": "内容：按需求直接产出"})
    return out


def roster(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM octo_bots WHERE state = 'active'").fetchall()
    return {r["role"]: dict(r) for r in rows}


def dispatch(conn: sqlite3.Connection, *, text: str, human_uid: str,
             group_name: str | None = None) -> dict:
    """Create the group, pull the right bots in, and post the plan as the PM bot."""
    people = roster(conn)
    pm = people.get("pm")
    if pm is None:
        raise RuntimeError("no bot with role='pm' in octo_bots")

    wanted = [p["role"] for p in plan_for(text)]
    members = [human_uid, pm["bot_uid"]]
    for role in wanted:
        if role in people and people[role]["bot_uid"] not in members:
            members.append(people[role]["bot_uid"])

    name = (group_name or text.strip())[:20] or "协作群"
    group = tools.create_group(name, members)
    group_no = group.get("group_no") or group.get("data", {}).get("group_no")

    lines = [f"收到：{text}", "", f"已建群「{name}」，拉入 "
             + "、".join(people[r]["display_name"] for r in wanted if r in people)
             + " 和董事长。", "分工："]
    for i, item in enumerate(plan_for(text), 1):
        owner = people.get(item["role"], {}).get("display_name") or pm["display_name"]
        lines.append(f"{i}. [{item['role']}] {item['title']} —— @{owner}")

    reply = "\n".join(lines)
    tools.say(conn, pm["bot_uid"], group_no, 2, reply)
    return {"group_no": group_no, "name": name, "members": members,
            "plan": plan_for(text), "message": reply}
