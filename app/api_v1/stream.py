"""v1 投影 API：事件流（契约 01 + 03 的最小可用切片）。

本文件只做三件事：
  1. 设备令牌鉴权（Bearer）
  2. REST：事件补拉 / 会话快照
  3. WSS：先 backlog 再实时，**同一条连接内完成切换**，带游标

投影（聊天/电脑/资料库）在后续工作包里加；它们只读这里的事件流与聚合表。
"""

from __future__ import annotations

import asyncio

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)

from .. import db, devices
from .. import events as ev

router = APIRouter(prefix="/v1", tags=["v1"])

VERSION = "1.0.0"
CAPS = ["screen", "local", "voice", "processes"]

_TICK = 0.05          # 实时轮询间隔（秒）
_MAX_FRAME = 200      # 单帧事件上限（契约 03）


# ---------------------------------------------------------------- 鉴权
def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def require_device(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    with db.get_conn() as conn:
        device = devices.authenticate(conn, token)
    if device is None:
        raise HTTPException(status_code=401, detail="invalid or missing device token")
    return device


# ---------------------------------------------------------------- REST
@router.get("/health")
def health() -> dict:
    with db.get_conn() as conn:
        cur = ev.cursor(conn)
    return {
        "ok": True,
        "service": "agent-os-v1",
        "version": VERSION,
        "caps": CAPS,
        "cursor": cur,
        "subscribers": ev.broadcaster.subscriber_count(),
    }


@router.get("/events")
def list_events(
    since: int = Query(default=0, ge=0),
    session_id: str | None = None,
    bot_id: str | None = None,
    kinds: str | None = Query(default=None, description="逗号分隔的事件类型"),
    limit: int = Query(default=ev.DEFAULT_READ_LIMIT, ge=1, le=ev.MAX_READ_LIMIT),
    device: dict = Depends(require_device),
) -> dict:
    """按游标补拉。WSS 不可用时的降级通道。"""
    kind_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    try:
        with db.get_conn() as conn:
            items = ev.read(
                conn, since=since, session_id=session_id, bot_id=bot_id,
                kinds=kind_list, limit=limit,
            )
            cur = ev.cursor(conn)
    except ev.EventError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": [e.to_dict() for e in items], "cursor": cur}


@router.get("/sessions/{session_id}/snapshot")
def session_snapshot(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=ev.MAX_READ_LIMIT),
    before_id: int | None = Query(default=None, ge=1),
    device: dict = Depends(require_device),
) -> dict:
    with db.get_conn() as conn:
        return ev.snapshot(conn, session_id, limit=limit, before_id=before_id)


# ---------------------------------------------------------------- 后台读库
def _read_batch(*, since: int, limit: int = _MAX_FRAME) -> list[dict]:
    """在独立连接里读一批事件（WAL 读不阻塞写）。"""
    with db.get_conn() as conn:
        return [e.to_dict() for e in ev.read(conn, since=since, limit=limit)]


def _ws_device(token: str | None) -> dict | None:
    with db.get_conn() as conn:
        return devices.authenticate(conn, token)


# ---------------------------------------------------------------- WSS
@router.websocket("/stream")
async def stream(
    websocket: WebSocket,
    since: int = 0,
    token: str | None = None,
) -> None:
    """事件流。契约 03 第 4 节：

    * 连接后先发 hello（带当前游标）
    * 若 since 落后：先 backlog（标 replay=true），再切实时，同一条连接内完成
    * 收到 {"t":"ack","cursor":N} 只记录，不落库（v1 不依赖 ack 裁剪）
    """
    header_token = _bearer(websocket.headers.get("authorization"))
    device = _ws_device(header_token or token)
    if device is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    sub = ev.broadcaster.subscribe()
    sent = int(since or 0)

    async def send_events(items: list[dict], *, replay: bool) -> None:
        nonlocal sent
        if not items:
            return
        # 瞬态事件（typing / message.delta）id=0，不能拿它们推进游标，否则游标被打回 0，
        # 下次重连会把整库事件重放一遍。
        last_id = max((int(it.get("id") or 0) for it in items), default=0)
        if last_id:
            sent = last_id
        await websocket.send_json(
            {"t": "events", "replay": replay, "items": items, "cursor": sent}
        )

    try:
        await websocket.send_json(
            {"t": "hello", "server": {"version": VERSION, "caps": CAPS},
             "cursor": sent, "device_id": device["id"]}
        )

        # 1) backlog：按游标分帧补发
        while True:
            batch = await asyncio.to_thread(_read_batch, since=sent, limit=_MAX_FRAME)
            if not batch:
                break
            await send_events(batch, replay=True)
            if len(batch) < _MAX_FRAME:
                break

        # 2) 实时 + 收包（ping/ack）并行
        async def sender() -> None:
            while True:
                items, dropped = ev.broadcaster.drain(sub)
                if dropped:
                    await websocket.send_json(
                        {"t": "resync", "reason": "slow consumer", "cursor": sent}
                    )
                if items:
                    await send_events(items, replay=False)
                await asyncio.sleep(_TICK)

        async def receiver() -> None:
            while True:
                raw = await websocket.receive_text()
                try:
                    import json

                    msg = json.loads(raw)
                except Exception:  # noqa: BLE001 - 非法帧直接忽略
                    continue
                if msg.get("t") == "ping":
                    await websocket.send_json({"t": "pong"})
                # ack 在 v1 只做语义确认，不参与裁剪

        send_task = asyncio.create_task(sender())
        recv_task = asyncio.create_task(receiver())
        try:
            await asyncio.wait({send_task, recv_task},
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (send_task, recv_task):
                task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - 连接异常不应打崩进程
        pass
    finally:
        ev.broadcaster.unsubscribe(sub)
