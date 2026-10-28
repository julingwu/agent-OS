"""免登录进入项目工作台（/app/ 赛博指挥舱）。

背景：/app/ 自己的登录是 identity + secret 换签名 token（localStorage.oa_token）。
exe / 网页已经用设备令牌登录过，不应该再让人登录第二次。

做法：
  1. 已登录设备调 `POST /v1/legacy-sso/ticket`（设备令牌鉴权）→ 服务端确保存在一个
     "exe-sso" 身份，并签发一次性 ticket（120 秒，一次即废）；
  2. 前端把 ticket 拼到 iframe 地址 `/app/?sso=<ticket>`；
  3. 控制台启动时调 `/v1/legacy-sso/exchange?ticket=`（公开，凭票）换到签名 token，
     写进 localStorage.oa_token 后继续，无需人工登录。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import db, identity
from ..ui.common import _make_cookie
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["legacy-sso"])

TICKET_TTL_SECONDS = 120
IDENTITY_NAME = "exe-sso"
IDENTITY_SETTING = "legacy_sso_identity"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _sso_identity(conn) -> str:
    row = conn.execute(
        "SELECT value_json FROM app_settings WHERE key=?", (IDENTITY_SETTING,)).fetchone()
    if row:
        try:
            import json
            ident_id = json.loads(row["value_json"])
            exists = conn.execute(
                "SELECT 1 FROM identities WHERE id=? AND state='active'", (ident_id,)).fetchone()
            if exists:
                return ident_id
        except (TypeError, ValueError):
            pass
    # 建一个专用身份（ceo 角色，拥有工作台全部视图）
    created = identity.issue(conn, name=IDENTITY_NAME, role="ceo",
                             legal_entity_id="LE-GROUP-HQ")
    import json
    conn.execute(
        "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,"
        " updated_at=excluded.updated_at",
        (IDENTITY_SETTING, json.dumps(created["id"]), _now()),
    )
    return created["id"]


class TicketOut(BaseModel):
    ticket: str
    expires_in: int


@router.post("/legacy-sso/ticket")
def create_ticket(device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        ident_id = _sso_identity(conn)
        ticket = secrets.token_urlsafe(24)
        expires = (datetime.now().astimezone()
                   + timedelta(seconds=TICKET_TTL_SECONDS)).isoformat(timespec="milliseconds")
        conn.execute(
            "INSERT INTO legacy_sso_tickets (ticket, identity_id, token, expires_at)"
            " VALUES (?,?,?,?)",
            (ticket, ident_id, _make_cookie(ident_id), expires),
        )
    return {"ticket": ticket, "expires_in": TICKET_TTL_SECONDS}


@router.get("/legacy-sso/exchange")
def exchange_ticket(ticket: str = Query(min_length=8, max_length=128)) -> dict:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM legacy_sso_tickets WHERE ticket=?", (ticket,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="ticket not found")
        if row["used_at"]:
            raise HTTPException(status_code=409, detail="ticket already used")
        if row["expires_at"] <= _now():
            raise HTTPException(status_code=410, detail="ticket expired")
        conn.execute("UPDATE legacy_sso_tickets SET used_at=? WHERE ticket=?",
                     (_now(), ticket))
    return {"token": row["token"], "identity_id": row["identity_id"]}


@router.post("/legacy-sso")
def direct_token(device: dict = Depends(require_device)) -> dict:
    """同源场景可直接拿 token（写进 localStorage），不必走 ticket。"""
    with db.get_conn() as conn:
        ident_id = _sso_identity(conn)
    return {"token": _make_cookie(ident_id), "identity_id": ident_id}
