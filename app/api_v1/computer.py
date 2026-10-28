"""电脑页 API：状态 / 单帧 / 实时推流（契约 03 第 5 节）。

预览通道是 JPEG over WSS（按需帧），不是 VNC —— 5Mbps 带宽下的选择。
接管（takeover）走 KasmVNC/x11vnc，等 exe 阶段再接。
"""

from __future__ import annotations

import asyncio
import struct

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..computer import screen
from .stream import _bearer, _ws_device, require_device

router = APIRouter(prefix="/v1", tags=["computer"])

MAX_FPS = 5.0
MIN_FPS = 0.2
SEQ_PREFIX = struct.Struct(">I")


class InputAction(BaseModel):
    type: str          # click | move | type | key | drag
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    button: int = 1
    text: str | None = None
    keys: str | None = None


@router.get("/computer")
def computer_status(device: dict = Depends(require_device)) -> dict:
    info = screen.probe()
    return {
        "bot_id": "bot_chief",
        "state": "awake" if info["awake"] and info["capture_ok"] else (
            "sleeping" if not info["awake"] else "error"),
        "desktop": {"manager": "lightdm+xfce", "display": info["display"]},
        "screen": info["geometry"],
        "capture_ok": info["capture_ok"],
        "frame_bytes": info["frame_bytes"],
        "error": info["error"],
        "stream": {"ws": "/v1/computer/screen", "default_fps": 2, "max_fps": MAX_FPS},
    }


@router.post("/computer/wake")
def computer_wake(device: dict = Depends(require_device)) -> dict:
    try:
        screen.wake()
    except screen.CaptureUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"wake failed: {exc}") from exc
    return {"state": "awake"}


@router.post("/computer/sleep")
def computer_sleep(device: dict = Depends(require_device)) -> dict:
    try:
        screen.sleep()
    except screen.CaptureUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"sleep failed: {exc}") from exc
    return {"state": "sleeping"}


@router.post("/computer/input")
def computer_input(body: InputAction, device: dict = Depends(require_device)) -> dict:
    """接管：在服务器桌面上注入鼠标/键盘事件（坐标是真实屏幕像素）。"""
    try:
        if body.type == "click":
            if body.x is None or body.y is None:
                raise HTTPException(status_code=400, detail="click needs x,y")
            screen.input_click(body.x, body.y, body.button)
        elif body.type == "move":
            if body.x is None or body.y is None:
                raise HTTPException(status_code=400, detail="move needs x,y")
            screen.input_move(body.x, body.y)
        elif body.type == "drag":
            if None in (body.x, body.y, body.x2, body.y2):
                raise HTTPException(status_code=400, detail="drag needs x,y,x2,y2")
            screen.input_drag(body.x, body.y, body.x2, body.y2)
        elif body.type == "type":
            if not body.text:
                raise HTTPException(status_code=400, detail="type needs text")
            screen.input_type(body.text)
        elif body.type == "key":
            if not body.keys:
                raise HTTPException(status_code=400, detail="key needs keys")
            screen.input_key(body.keys)
        else:
            raise HTTPException(status_code=400, detail=f"unsupported input type: {body.type}")
    except screen.CaptureUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"input failed: {exc}") from exc
    return {"ok": True}


@router.get("/computer/frame.jpg")
def computer_frame(
    max_w: int = Query(default=1280, ge=320, le=2560),
    quality: int = Query(default=5, ge=2, le=20),
    device: dict = Depends(require_device),
) -> Response:
    try:
        jpg = screen.grab_jpeg(max_w=max_w, quality=quality)
    except screen.CaptureUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"capture unavailable: {exc}") from exc
    return Response(content=jpg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.websocket("/computer/screen")
async def computer_stream(websocket: WebSocket, token: str | None = None) -> None:
    """实时预览。

    客户端首帧发订阅参数：
        {"t":"subscribe","fps":2,"quality":6,"max_w":1280}
    服务端回 meta，然后持续推送二进制帧（4 字节大端 seq + JPEG）。
    客户端可发 {"t":"unsubscribe"} 或直接断开。
    """
    header_token = _bearer(websocket.headers.get("authorization"))
    device = _ws_device(header_token or token)
    if device is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    fps, quality, max_w = 2.0, 6, 1280
    try:
        sub = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if isinstance(sub, dict) and sub.get("t") == "subscribe":
            fps = max(MIN_FPS, min(float(sub.get("fps", 2) or 2), MAX_FPS))
            quality = max(2, min(int(sub.get("quality", 6) or 6), 20))
            max_w = max(320, min(int(sub.get("max_w", 1280) or 1280), 2560))
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - 首帧缺失用默认参数
        pass

    w, h = screen.geometry()
    await websocket.send_json({"t": "meta", "geometry": {"w": w, "h": h},
                               "fps": fps, "quality": quality, "max_w": max_w})

    interval = 1.0 / fps
    seq = 0
    try:
        while True:
            try:
                jpg = await asyncio.to_thread(screen.grab_jpeg, max_w, quality)
            except screen.CaptureUnavailable as exc:
                await websocket.send_json({"t": "capture_error", "error": str(exc)[:200]})
                await asyncio.sleep(1.0)
                continue
            seq += 1
            await websocket.send_bytes(SEQ_PREFIX.pack(seq) + jpg)
            # 非阻塞看一眼客户端消息（unsubscribe / ack），不阻塞出帧
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=interval)
                if isinstance(msg, dict) and msg.get("t") == "unsubscribe":
                    break
            except asyncio.TimeoutError:
                pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
