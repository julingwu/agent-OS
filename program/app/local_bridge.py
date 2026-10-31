"""本地电脑桥（exe → 云端 Bot）。

模型：
  * exe 启动后用设备令牌出站连 `WS /v1/device`，登记自己的本地能力；
  * 云端 Agent 的工具（local_*）通过这里把调用发过去，等结果回来；
  * 默认每次调用先发 `approval.requested` 事件，等用户在界面上点允许；
  * exe 离线时工具直接报错，不长时间挂起。

线程模型：工具跑在后台工作线程，WebSocket 在主事件循环。跨线程用
`run_coroutine_threadsafe` + 每调用一个 Future。
"""

from __future__ import annotations

import asyncio
import secrets
import threading
import time
from typing import Any, Callable

MAX_CALL_TIMEOUT = 180.0
DEFAULT_CALL_TIMEOUT = 90.0
APPROVAL_TIMEOUT = 180.0

_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()
_connections: dict[str, Any] = {}          # device_id -> WebSocket
_caps: dict[str, list[str]] = {}
_pending: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Future]] = {}
# call_id -> 本次调用产出的 artifact 元数据（截图等二进制不进事件，只存引用）
_artifacts: dict[str, list[dict]] = {}
_approvals: dict[str, dict] = {}           # approval_id -> {"event": Event, "decision": bool|None}
_session_allowlist: set[str] = set()       # session_id -> 会话级免审授权白名单


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def loop() -> asyncio.AbstractEventLoop | None:
    return _loop


def online_devices() -> list[dict]:
    with _lock:
        return [{"device_id": did, "caps": _caps.get(did, [])} for did in _connections]


def first_device() -> str | None:
    with _lock:
        return next(iter(_connections), None)


def is_online(device_id: str | None = None) -> bool:
    with _lock:
        if device_id:
            return device_id in _connections
        return bool(_connections)


def register(device_id: str, ws, caps: list[str] | None = None) -> None:
    with _lock:
        _connections[device_id] = ws
        _caps[device_id] = caps or []


def unregister(device_id: str, ws=None) -> None:
    with _lock:
        current = _connections.get(device_id)
        if ws is None or current is ws:
            _connections.pop(device_id, None)
            _caps.pop(device_id, None)


def accept_artifact(call_id: str, name: str, kind: str, data_b64: str) -> dict | None:
    """exe 在截图类工具之后推来的二进制：落盘成 artifact，挂到这次调用上。

    这里只做 IO + 内存记账（跑在 WS 接收线程），**不 emit**：
    sqlite 连接属于 worker 线程，跨线程 emit 会踩 check_same_thread。
    事件由拿到工具结果的一方（tools.py 的 _local_tool）补发。
    """
    from . import artifacts

    try:
        info = artifacts.save_b64(name or "screenshot.png",
                                  kind or "image/png", data_b64)
    except Exception as exc:                        # noqa: BLE001 - 存不下不能把桥打崩
        print(f"[artifact] save failed: {exc}")
        return None
    with _lock:
        _artifacts.setdefault(call_id, []).append(info)
    return info


def resolve_result(call_id: str, ok: bool, result: Any = None, error: str | None = None) -> None:
    with _lock:
        entry = _pending.pop(call_id, None)
    if entry is None:
        return
    fut_loop, fut = entry
    if fut.done():
        return
    try:
        fut_loop.call_soon_threadsafe(
            fut.set_result, {"ok": ok, "result": result, "error": error})
    except RuntimeError:
        pass


# ---------------------------------------------------------------- 调用
async def _call_async(device_id: str, tool: str, args: dict, timeout: float) -> dict:
    with _lock:
        ws = _connections.get(device_id)
    if ws is None:
        return {"ok": False, "error": "本地设备已离线"}
    call_id = "lc_" + secrets.token_hex(8)
    fut = asyncio.get_running_loop().create_future()
    with _lock:
        _pending[call_id] = (asyncio.get_running_loop(), fut)
    try:
        await ws.send_json({"t": "tool_call", "call_id": call_id,
                            "tool": tool, "args": args or {}})
        result = await asyncio.wait_for(fut, timeout=timeout)
        with _lock:
            made = _artifacts.pop(call_id, [])
        if made and isinstance(result, dict):
            result["_artifacts"] = made
        return result
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"本地设备超时（{timeout:.0f}s）"}
    except Exception as exc:  # noqa: BLE001 - 连接断开等
        return {"ok": False, "error": f"本地桥异常：{exc}"}
    finally:
        with _lock:
            _pending.pop(call_id, None)
            _artifacts.pop(call_id, None)


def call(device_id: str, tool: str, args: dict,
         timeout: float = DEFAULT_CALL_TIMEOUT) -> dict:
    """同步接口（工作线程里用）。超时/离线都返回结构化错误。"""
    target_loop = _loop
    if target_loop is None or target_loop.is_closed():
        return {"ok": False, "error": "本地桥未就绪（服务端事件循环不可用）"}
    timeout = max(5.0, min(float(timeout), MAX_CALL_TIMEOUT))
    try:
        fut = asyncio.run_coroutine_threadsafe(
            _call_async(device_id, tool, args, timeout), target_loop)
        return fut.result(timeout=timeout + 5)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"本地调用失败：{exc}"}


# ---------------------------------------------------------------- 审批
def is_session_approved(session_id: str | None) -> bool:
    """检查指定会话是否已被用户授予全会话免确认操作权限。"""
    if not session_id:
        return False
    with _lock:
        return session_id in _session_allowlist


def set_session_approval(session_id: str | None, approved: bool) -> None:
    """授予或撤回指定会话的全操作免审权限。若授予，同时自动放行该会话下当前所有挂起审批。"""
    if not session_id:
        return
    with _lock:
        if approved:
            _session_allowlist.add(session_id)
            for aid, meta in list(_approvals.items()):
                if meta.get("session_id") == session_id:
                    meta["decision"] = True
                    meta["event"].set()
        else:
            _session_allowlist.discard(session_id)


def request_approval(emit: Callable[[str, dict], None] | None, *,
                     action: str, detail: str, risk: str = "normal",
                     preview: dict | None = None,
                     session_id: str | None = None) -> bool:
    """发一个审批事件并阻塞等待用户决定。emit 为空（测试）时默认放行。"""
    if emit is None:
        return True
    if session_id and is_session_approved(session_id):
        return True

    approval_id = "apr_" + secrets.token_hex(8)
    decision: dict[str, Any] = {"event": threading.Event(), "decision": None,
                                "action": action, "detail": detail, "risk": risk,
                                "preview": preview or {}, "session_id": session_id}
    with _lock:
        _approvals[approval_id] = decision
    try:
        emit("approval.requested", {
            "approval_id": approval_id,
            "action": action,
            "detail": detail,
            "risk": risk,
            "preview": preview or {},
            "session_id": session_id,
        })
        decision["event"].wait(timeout=APPROVAL_TIMEOUT)
        return bool(decision["decision"])
    finally:
        with _lock:
            _approvals.pop(approval_id, None)
        # Broadcast every resolution, including decisions made on another
        # client. Otherwise its approval overlay remains after the task ends.
        try:
            resolved = decision["decision"]
            emit("approval.decided",
                 {"approval_id": approval_id,
                  "decision": "expired" if resolved is None else ("allowed" if resolved else "denied"),
                  "session_id": session_id})
        except Exception:  # noqa: BLE001
            pass


def pending() -> list[dict]:
    with _lock:
        return [{"approval_id": aid, "action": meta.get("action", ""),
                 "detail": meta.get("detail", ""), "risk": meta.get("risk", "normal"),
                 "preview": meta.get("preview", {}), "session_id": meta.get("session_id")}
                for aid, meta in _approvals.items()]


def resolve_approval(approval_id: str, allow: bool, scope: str = "once") -> bool:
    with _lock:
        decision = _approvals.get(approval_id)
    if decision is None:
        return False
    decision["decision"] = allow
    decision["event"].set()

    if allow and scope == "session":
        sess_id = decision.get("session_id")
        if sess_id:
            set_session_approval(sess_id, True)
    return True
