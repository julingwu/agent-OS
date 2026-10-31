"""设备配对：管理端生成一次性配对码，新客户端用码换设备令牌。

设计要点（回应"别人下载 exe 后怎么绑定电脑"）：
  * 不需要固定 IP、不需要域名白名单 —— 绑定的凭据是设备令牌；
  * 新设备只知道"服务器地址"（IP 或域名都行）+ 一个 6 位一次性配对码；
  * 配对码 5 分钟过期、用一次即废；兑换成功才签发长期设备令牌；
  * 之后该设备的所有请求都带这个令牌，服务器据此识别"哪台电脑"。
"""

from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db, devices
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["pairing"])

CODE_TTL_SECONDS = 300
_RATE_LOCK = threading.Lock()
_CLAIM_HITS: dict[str, list[float]] = {}
CLAIM_PER_MINUTE = 10


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _rate_ok(ip: str) -> bool:
    now = time.time()
    with _RATE_LOCK:
        hits = [t for t in _CLAIM_HITS.get(ip, []) if now - t < 60]
        if len(hits) >= CLAIM_PER_MINUTE:
            _CLAIM_HITS[ip] = hits
            return False
        hits.append(now)
        _CLAIM_HITS[ip] = hits
        return True


@router.get("/server-info")
def server_info() -> dict:
    """公开信息：客户端在配对前用它确认"这个地址确实是 AgentOS"。"""
    return {"name": "AgentOS", "version": "1.0.0", "pairing": True,
            "ttl_seconds": CODE_TTL_SECONDS}


class CodeOut(BaseModel):
    code: str
    expires_in: int


@router.post("/pairing/codes")
def create_code(device: dict = Depends(require_device)) -> dict:
    """已登录的设备生成配对码，给另一台电脑/手机用。"""
    expires = (datetime.now().astimezone()
               + timedelta(seconds=CODE_TTL_SECONDS)).isoformat(timespec="milliseconds")
    for _ in range(8):
        code = f"{secrets.randbelow(1_000_000):06d}"
        try:
            with db.get_conn() as conn:
                conn.execute(
                    "INSERT INTO pairing_codes (code, created_at, expires_at)"
                    " VALUES (?,?,?)",
                    (code, _now(), expires),
                )
            return {"code": code, "expires_in": CODE_TTL_SECONDS,
                    "expires_at": expires}
        except Exception:  # noqa: BLE001 - 六位码撞车极小概率，重试即可
            continue
    raise HTTPException(status_code=500, detail="could not allocate pairing code")


class ClaimIn(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    name: str | None = Field(default=None, max_length=60)
    kind: str = Field(default="exe", pattern="^(exe|pwa|wecli|cli)$")


@router.post("/pairing/claim")
def claim_code(body: ClaimIn, request: Request) -> dict:
    """新客户端凭配对码换设备令牌（不需要任何已有凭据）。"""
    ip = request.client.host if request.client else "unknown"
    if not _rate_ok(ip):
        raise HTTPException(status_code=429, detail="too many attempts; wait a minute")
    if body.kind == "wecli":
        body.kind = "cli"
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM pairing_codes WHERE code=?", (body.code,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="配对码不存在")
        if row["used_at"]:
            raise HTTPException(status_code=409, detail="配对码已被使用，请重新生成")
        if row["expires_at"] <= _now():
            raise HTTPException(status_code=410, detail="配对码已过期，请重新生成")
        conn.execute("UPDATE pairing_codes SET used_at=? WHERE code=? AND used_at IS NULL",
                     (_now(), body.code))
        issued = devices.issue(conn, name=body.name or "未命名设备", kind=body.kind)
    return {"device_id": issued["device_id"], "token": issued["token"],
            "server": {"name": "AgentOS", "version": "1.0.0"}}
