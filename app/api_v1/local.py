"""本地电脑桥的 HTTP/WS 入口（exe 侧连接、审批决定）。

  * WS  /v1/device?token=   exe 出站连接，登记能力，接收工具调用并回结果
  * GET /v1/local/devices   在线的本地电脑
  * POST /v1/local/approvals/{id}  {allow: bool}  前端审批按钮
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .. import db, local_bridge
from .stream import _bearer, _ws_device, require_device

router = APIRouter(prefix="/v1", tags=["local"])


@router.get("/local/devices")
def list_devices(device: dict = Depends(require_device)) -> dict:
    return {"items": local_bridge.online_devices(),
            "count": len(local_bridge.online_devices())}


class ApprovalIn(BaseModel):
    allow: bool


@router.get("/local/approvals")
def list_approvals(device: dict = Depends(require_device)) -> dict:
    """当前还悬着的审批（界面启动时用来恢复卡片）。"""
    items = local_bridge.pending()
    return {"items": items, "count": len(items)}


@router.post("/local/approvals/{approval_id}")
def decide_approval(approval_id: str, body: ApprovalIn,
                    device: dict = Depends(require_device)) -> dict:
    ok = local_bridge.resolve_approval(approval_id, body.allow)
    if not ok:
        raise HTTPException(status_code=404, detail="approval not found or expired")
    return {"approval_id": approval_id, "allow": body.allow}


@router.websocket("/device")
async def device_ws(websocket: WebSocket, token: str | None = None) -> None:
    header_token = _bearer(websocket.headers.get("authorization"))
    device = _ws_device(header_token or token)
    if device is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    device_id = device["id"]
    local_bridge.register(device_id, websocket, [])
    try:
        # hello 声明本机能力
        first = await websocket.receive_json()
        if isinstance(first, dict) and first.get("t") == "hello":
            local_bridge.register(device_id, websocket, first.get("caps") or [])
            await websocket.send_json({"t": "hello_ack",
                                       "server": {"version": "1.0.0"}})
        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            if msg.get("t") == "tool_result":
                local_bridge.resolve_result(
                    str(msg.get("call_id") or ""),
                    bool(msg.get("ok")),
                    msg.get("result"),
                    msg.get("error"),
                )
            elif msg.get("t") == "artifact":
                # exe 在 local_screenshot 之后推来的图片：落盘 + 挂到这次 call_id 上
                local_bridge.accept_artifact(
                    str(msg.get("call_id") or ""),
                    str(msg.get("name") or "screenshot.png"),
                    str(msg.get("kind") or "image/png"),
                    str(msg.get("data_b64") or ""),
                )
            elif msg.get("t") == "ping":
                await websocket.send_json({"t": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - 桥断开不应打崩服务
        pass
    finally:
        local_bridge.unregister(device_id, websocket)
