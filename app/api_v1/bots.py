"""Bot / 会话 / 消息 的最小投影 API（W6 的第一刀）。

范围：
  * Bot：列表/创建/详情
  * 会话：列表/创建（单聊或群）
  * 消息：发消息（落事件、幂等）/ 拉消息（从事件流投影）

Agent 回复（turn 执行）在 W5 接上；这里先把"人说话"和"存得住"跑通。
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db
from .. import events as ev
from ..agent import loop as agent_loop
from .stream import require_device

MENTION_RE = re.compile(r"@([^\s@，,。.：:；;！!？?]{1,20})")
MAX_GROUP_TARGETS = 3

router = APIRouter(prefix="/v1", tags=["bots"])


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


# ---------------------------------------------------------------- 入参
class BotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    role: str | None = None
    persona: str | None = None
    model: str | None = None
    role_id: str | None = None
    skills: list[str] | None = None


class BotPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    role: str | None = None
    persona: str | None = None
    model: str | None = None


class SessionCreate(BaseModel):
    kind: str = Field(default="direct", pattern="^(direct|group)$")
    bot_ids: list[str] = Field(min_length=1, max_length=6)
    title: str | None = None


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    client_msg_id: str | None = Field(default=None, max_length=64)
    reply_to: int | None = None


# ---------------------------------------------------------------- Bot
def _bot_row(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "role": row["role"],
        "persona": row["persona"], "model": row["model"], "avatar": row["avatar"],
        "tags": row["tags_json"], "status": row["status"],
        "created_at": row["created_at"],
    }


@router.get("/bots")
def list_bots(device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bots WHERE deleted_at IS NULL ORDER BY created_at ASC"
        ).fetchall()
    return {"items": [_bot_row(r) for r in rows]}


@router.get("/roles")
def list_roles(device: dict = Depends(require_device)) -> dict:
    """获取系统中可用的角色模板库（43 个内置角色）。"""
    from ..ui import roles as roles_mod

    with db.get_conn() as conn:
        roles_mod.ensure(conn)
        items = roles_mod.list_roles(conn)
    return {"items": items, "count": len(items)}


@router.post("/bots", status_code=201)
def create_bot(body: BotCreate, device: dict = Depends(require_device)) -> dict:
    bot_id = _new_id("bot")
    name = body.name.strip()
    role = body.role
    persona = body.persona
    model = body.model
    role_skills = list(body.skills or [])

    with db.get_conn() as conn:
        from ..ui import roles as roles_mod

        roles_mod.ensure(conn)
        if body.role_id:
            r = conn.execute(
                "SELECT * FROM role_templates WHERE id=?", (body.role_id,)
            ).fetchone()
            if r:
                if not role:
                    role = r["tag"] or r["name"]
                if not persona:
                    persona = r["prompt"]
                if not model and r["model"]:
                    model = r["model"]
                if not role_skills and r["skills"]:
                    role_skills = [s.strip() for s in r["skills"].split(",") if s.strip()]

        conn.execute(
            "INSERT INTO bots (id, name, role, persona, model, status, created_at)"
            " VALUES (?,?,?,?,?,'idle',?)",
            (bot_id, name, role, persona, model, _now()),
        )
        from .. import skills as skills_mod

        skills_mod.ensure(conn)
        for sk in role_skills:
            if not skills_mod.exists(conn, bot_id=bot_id, name=sk):
                skills_mod.create(
                    conn,
                    bot_id=bot_id,
                    name=sk,
                    description=f"【{name}】专属技能：{sk}",
                    body=f"【{name}技能：{sk}】\n针对当前任务「{{input}}」，按照专业标准执行「{sk}」，给出准确、结构化且具备实操性的结论。",
                )
        row = conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
    return _bot_row(row)


@router.get("/bots/{bot_id}")
def get_bot(bot_id: str, device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM bots WHERE id=? AND deleted_at IS NULL",
                           (bot_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="bot not found")
        sessions = [r["session_id"] for r in conn.execute(
            "SELECT session_id FROM session_members WHERE member_id=?", (bot_id,)
        ).fetchall()]
    return {**_bot_row(row), "sessions": sessions}


@router.patch("/bots/{bot_id}")
def patch_bot(bot_id: str, body: BotPatch,
              device: dict = Depends(require_device)) -> dict:
    """改名字/职责/人格/模型。模型留空表示跟随全局默认。"""
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="nothing to update")
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM bots WHERE id=? AND deleted_at IS NULL",
                           (bot_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="bot not found")
        if "model" in fields and not fields["model"]:
            fields.pop("model")
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE bots SET {sets} WHERE id=?", (*fields.values(), bot_id))
        row = conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
    return _bot_row(row)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, device: dict = Depends(require_device)) -> dict:
    """软删会话：聊天记录保留在事件流里（审计用），列表不再显示。"""
    now = _now()
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM sessions WHERE id=? AND deleted_at IS NULL",
                           (session_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        conn.execute("UPDATE sessions SET deleted_at=? WHERE id=?", (now, session_id))
    return {"deleted": session_id}


@router.delete("/bots/{bot_id}")
def delete_bot(bot_id: str, device: dict = Depends(require_device)) -> dict:
    """软删 Bot，并把它独占的单聊会话一起下架（群聊保留，只移出成员列表逻辑）。"""
    now = _now()
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM bots WHERE id=? AND deleted_at IS NULL",
                           (bot_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="bot not found")
        conn.execute("UPDATE bots SET deleted_at=? WHERE id=?", (now, bot_id))
        conn.execute(
            "UPDATE sessions SET deleted_at=? WHERE id IN ("
            " SELECT s.id FROM sessions s JOIN session_members cm ON cm.session_id=s.id"
            " WHERE cm.member_id=? AND s.kind='direct')",
            (now, bot_id),
        )
    return {"deleted": bot_id}


@router.get("/bots/{bot_id}/details")
def bot_details(bot_id: str, device: dict = Depends(require_device)) -> dict:
    """详情页聚合：Bot 信息 + 今天的 token 用量 + 会话数。"""
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM bots WHERE id=? AND deleted_at IS NULL",
                           (bot_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="bot not found")
        used = 0
        for e in ev.read(conn, since=0, bot_id=bot_id, kinds=["budget.updated"],
                         limit=ev.MAX_READ_LIMIT):
            if e.ts.startswith(today):
                used += int(e.payload.get("used") or 0)
        sessions = [r["session_id"] for r in conn.execute(
            "SELECT session_id FROM session_members WHERE member_id=?", (bot_id,)
        ).fetchall()]
    return {**_bot_row(row), "sessions": sessions,
            "usage_today_tokens": used,
            "routines": [], "skills": []}


@router.get("/bots/{bot_id}/library")
def bot_library(bot_id: str, path: str = "",
                device: dict = Depends(require_device)) -> dict:
    """资料库页：工作区文件 + 记忆/技能占位（后续聚合）。"""
    from .workspace import list_workspace

    files = list_workspace(path=path, device=device)
    with db.get_conn() as conn:
        skills = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='skills'"
        ).fetchone()
        skill_items = []
        if skills:
            skill_items = [dict(r) for r in conn.execute(
                "SELECT id, name FROM skills LIMIT 50").fetchall()]
    return {"bot_id": bot_id, "files": files, "memories": [], "skills": skill_items}


# ---------------------------------------------------------------- 会话
def _session_row(conn, row) -> dict:
    from ..agent.loop import is_running

    members = [r["member_id"] for r in conn.execute(
        "SELECT member_id FROM session_members WHERE session_id=? ORDER BY joined_at",
        (row["id"],),
    ).fetchall()]
    last = conn.execute(
        "SELECT COALESCE(MAX(id),0) AS c FROM events WHERE session_id=?", (row["id"],)
    ).fetchone()["c"]
    return {
        "id": row["id"], "kind": row["kind"], "title": row["title"],
        "admin_bot_id": row["admin_bot_id"], "members": members,
        "last_event_id": last, "created_at": row["created_at"],
        "running": is_running(row["id"]),
    }


@router.get("/sessions")
def list_sessions(device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE deleted_at IS NULL ORDER BY created_at ASC"
        ).fetchall()
        return {"items": [_session_row(conn, r) for r in rows]}


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreate, device: dict = Depends(require_device)) -> dict:
    session_id = _new_id("ses")
    now = _now()
    with db.get_conn() as conn:
        known = {r["id"] for r in conn.execute(
            "SELECT id FROM bots WHERE deleted_at IS NULL ORDER BY created_at"
        ).fetchall()}
        missing = [b for b in body.bot_ids if b not in known]
        if missing:
            raise HTTPException(status_code=400, detail=f"unknown bots: {missing}")
        admin = body.bot_ids[0]
        conn.execute(
            "INSERT INTO sessions (id, kind, title, admin_bot_id, created_at)"
            " VALUES (?,?,?,?,?)",
            (session_id, body.kind, body.title or ("群聊" if body.kind == "group" else None),
             admin, now),
        )
        for bid in body.bot_ids:
            conn.execute(
                "INSERT INTO session_members (session_id, member_id, role, joined_at)"
                " VALUES (?,?,?,?)",
                (session_id, bid, "admin" if bid == admin else "member", now),
            )
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return _session_row(conn, row)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=? AND deleted_at IS NULL",
                           (session_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        return _session_row(conn, row)


# ---------------------------------------------------------------- 消息
@router.post("/sessions/{session_id}/messages", status_code=202)
def post_message(session_id: str, body: MessageIn,
                 device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id=? AND deleted_at IS NULL", (session_id,)
        ).fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        bot_id = session["admin_bot_id"] or "bot_chief"
        event = ev.emit(
            conn, bot_id=bot_id, session_id=session_id,
            kind="message.user", origin="user",
            actor={"type": "user", "id": device["id"]},
            payload={"text": body.text, "device_id": device["id"]},
            client_msg_id=body.client_msg_id,
            refs={"reply_to": body.reply_to} if body.reply_to else None,
        )
        targets = _route_targets(conn, session, body.text)
        cursor = ev.cursor(conn)
    ev.broadcaster.publish(event)

    scheduled = os.environ.get("AGENT_AUTORUN", "1") == "1"
    if scheduled:
        for target in targets:
            agent_loop.run_turn_async(target, session_id)

    return {"event_id": event.id, "cursor": cursor, "reply_scheduled": scheduled,
            "targets": targets}


def _route_targets(conn, session, text: str) -> list[str]:
    """群聊按 @ 点名路由；没点名就给管理员 Bot。单聊永远给管理员。"""
    admin = session["admin_bot_id"] or "bot_chief"
    if session["kind"] != "group":
        return [admin]
    wanted = {m.strip() for m in MENTION_RE.findall(text or "") if m.strip()}
    if not wanted:
        return [admin]
    rows = conn.execute(
        "SELECT m.member_id, b.name FROM session_members m JOIN bots b ON b.id = m.member_id"
        " WHERE m.session_id = ?", (session["id"],),
    ).fetchall()
    hits = [r["member_id"] for r in rows if r["name"] in wanted and r["member_id"] != admin]
    return (hits or [admin])[:MAX_GROUP_TARGETS]


@router.get("/sessions/{session_id}/messages")
def list_messages(
    session_id: str,
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=ev.MAX_READ_LIMIT),
    device: dict = Depends(require_device),
) -> dict:
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="session not found")
        items = ev.read(conn, since=since, session_id=session_id,
                        kinds=["message.user", "message.bot", "message.system"],
                        limit=limit)
        cursor = ev.cursor(conn)
    return {
        "items": [
            {"id": e.id, "ts": e.ts, "kind": e.kind,
             "text": e.payload.get("text", ""), "actor": e.actor}
            for e in items
        ],
        "cursor": cursor,
    }
