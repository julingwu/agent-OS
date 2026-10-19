"""WeCom callback endpoints + a local simulator (no corp account needed)."""
from __future__ import annotations

import asyncio
import logging
import os
from defusedxml import ElementTree as ET  # XXE / entity-expansion safe

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from ..db import connect
from . import approval, bridge, crypto, guard, watcher
from .client import WeComClient

router = APIRouter(tags=["wecom"], prefix="/wecom")
log = logging.getLogger("wecom.api")


def _sim_token() -> str:
    return os.environ.get("WECOM_SIM_TOKEN", "dev-token")


def _require_sim(token: str | None) -> None:
    if (token or "") != _sim_token():
        raise HTTPException(401, detail={"code": "BadSimToken"})


def _check_ip(request: Request) -> None:
    ip = guard.client_ip(request)
    if not guard.ip_allowed(ip):
        raise HTTPException(403, detail={"code": "IpNotAllowed", "ip": ip})


def _xml(text: str) -> dict:
    root = ET.fromstring(text)
    return {child.tag: (child.text or "") for child in root}


# ------------------------------------------------------------------ callback
@router.get("/callback", response_class=PlainTextResponse)
def verify(request: Request, msg_signature: str = Query(...), timestamp: str = Query(...),
           nonce: str = Query(...), echostr: str = Query(...)):
    """WeCom's URL check: verify the signature, decrypt echostr, echo it back."""
    _check_ip(request)
    token = os.environ.get("WECOM_TOKEN", "")
    aes = os.environ.get("WECOM_AES_KEY", "")
    corp = os.environ.get("WECOM_CORP_ID", "")
    if not crypto.verify(token, timestamp, nonce, echostr, msg_signature):
        raise HTTPException(403, detail={"code": "BadSignature"})
    return crypto.decrypt(echostr, aes, corp)


@router.post("/callback", response_class=PlainTextResponse)
async def callback(request: Request, msg_signature: str = Query(default=""),
                   timestamp: str = Query(default=""), nonce: str = Query(default="")):
    _check_ip(request)
    raw = (await request.body()).decode("utf-8", "replace")
    token = os.environ.get("WECOM_TOKEN", "")
    aes = os.environ.get("WECOM_AES_KEY", "")
    corp = os.environ.get("WECOM_CORP_ID", "")

    envelope = _xml(raw)
    if "Encrypt" in envelope:
        enc = envelope["Encrypt"]
        if not crypto.verify(token, timestamp, nonce, enc, msg_signature):
            raise HTTPException(403, detail={"code": "BadSignature"})
        try:
            raw = crypto.decrypt(enc, aes, corp)
        except crypto.WeComCryptoError as exc:
            raise HTTPException(400, detail={"code": "BadCipher", "message": str(exc)})

    msg = _xml(raw)
    with connect() as conn:
        if guard.seen(conn, guard.message_key(msg, raw)):
            log.info("duplicate callback ignored: %s", guard.message_key(msg, raw))
            return "success"          # WeCom only needs an ack; work already happened
    user = msg.get("FromUserName", "")
    client = WeComClient(conn_factory=connect)

    # This handler is async: doing the work inline would block the event loop and
    # deadlock the nested signed HTTP call back into this same server.
    if msg.get("MsgType") == "event" and msg.get("Event") == "template_card_event":
        reply = await asyncio.to_thread(_handle_action, user, msg.get("EventKey", ""))
    elif msg.get("MsgType") == "text":
        reply = await asyncio.to_thread(_handle_text, user, msg.get("Content", ""))
    else:
        reply = None

    if reply and user:
        try:
            await asyncio.to_thread(client.send_text, user, reply)
        except Exception as exc:                 # noqa: BLE001
            log.warning("reply send failed: %s", exc)
    return "success"


def _handle_text(user: str, text: str) -> str:
    with connect() as conn:
        return bridge.handle_text(conn, user, text)


def _handle_action(user: str, key: str) -> str:
    with connect() as conn:
        return bridge.handle_action(conn, user, key)


# ------------------------------------------------------------------ simulator
@router.post("/simulate")
def simulate(payload: dict, x_wecom_token: str | None = Header(default=None)):
    """Send a text message *as if* WeCom delivered it (local development)."""
    _require_sim(x_wecom_token)
    user, text = payload.get("userid", ""), payload.get("text", "")
    if not user:
        raise HTTPException(400, detail={"code": "MissingUser"})
    return {"reply": _handle_text(user, text)}


@router.post("/simulate/event")
def simulate_event(payload: dict, x_wecom_token: str | None = Header(default=None)):
    """Click a card button (EventKey) as if WeCom forwarded the event."""
    _require_sim(x_wecom_token)
    user, key = payload.get("userid", ""), payload.get("event_key", "")
    if not (user and key):
        raise HTTPException(400, detail={"code": "MissingUserOrKey"})
    return {"reply": _handle_action(user, key)}


@router.post("/bind")
def bind(payload: dict, x_wecom_token: str | None = Header(default=None)):
    _require_sim(x_wecom_token)
    with connect() as conn:
        ident = conn.execute("SELECT id FROM identities WHERE id=? AND state='active'",
                             (payload.get("identity_id"),)).fetchone()
        if ident is None:
            raise HTTPException(404, detail={"code": "UnknownIdentity"})
        return bridge.bind_user(conn, payload.get("userid", ""), payload["identity_id"],
                                payload.get("name"))


@router.get("/status")
def status(x_wecom_token: str | None = Header(default=None)):
    _require_sim(x_wecom_token)
    with connect() as conn:
        users = [dict(r) for r in conn.execute(
            "SELECT wecom_userid, identity_id, name FROM wecom_users WHERE state='active'")]
        pushes = conn.execute("SELECT COUNT(*) FROM wecom_pushes").fetchone()[0]
    return {"watcher": watcher.status(), "mock": os.environ.get("WECOM_MOCK", "0") == "1",
            "users": users, "pushes": pushes,
            "callback_url": os.environ.get("WECOM_CALLBACK_URL", "")}


@router.get("/outbox")
def outbox(limit: int = 20, x_wecom_token: str | None = Header(default=None)):
    """Mock mode: what would have been sent."""
    _require_sim(x_wecom_token)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM wecom_outbox ORDER BY created_at DESC LIMIT ?", (limit,))]
    return {"count": len(rows), "messages": rows}


@router.post("/watcher/tick")
def tick(x_wecom_token: str | None = Header(default=None)):
    _require_sim(x_wecom_token)
    w = watcher.Watcher(interval=0)
    return {"sent": w.tick(), "last_error": w.last_error}


# ------------------------------------------------------------------ approvals
@router.post("/approval/create")
def approval_create(payload: dict, x_wecom_token: str | None = Header(default=None)):
    """Open a WeCom native approval for the next pending human criterion."""
    _require_sim(x_wecom_token)
    task_id = (payload or {}).get("task_id") or ""
    userid = (payload or {}).get("userid") or ""
    if not (task_id and userid):
        raise HTTPException(400, detail={"code": "MissingTaskOrUser"})
    with connect() as conn:
        try:
            return approval.create(conn, task_id=task_id, userid=userid,
                                   criterion_id=(payload or {}).get("criterion_id"))
        except approval.ApprovalError as exc:
            raise HTTPException(400, detail={"code": "ApprovalFailed", "message": str(exc)})


@router.post("/approval/poll")
def approval_poll(x_wecom_token: str | None = Header(default=None)):
    """One poll tick (the background thread does this automatically)."""
    _require_sim(x_wecom_token)
    with connect() as conn:
        return approval.poll_once(conn)


@router.get("/approval/status")
def approval_status(sp_no: str, x_wecom_token: str | None = Header(default=None)):
    _require_sim(x_wecom_token)
    try:
        return approval.status(sp_no)
    except approval.ApprovalError as exc:
        raise HTTPException(400, detail={"code": "ApprovalStatusFailed", "message": str(exc)})


@router.get("/approval/list")
def approval_list(x_wecom_token: str | None = Header(default=None)):
    _require_sim(x_wecom_token)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM wecom_approvals ORDER BY created_at DESC LIMIT 20")]
    return {"count": len(rows), "approvals": rows, "poller": approval.poller_status()}
