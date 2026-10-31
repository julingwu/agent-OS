"""设备配对与设备令牌（契约 03 第 2 节的 v1 实现）。

安全约定：
  * 数据库里只存 token 的 sha256，明文只在签发那一刻返回一次；
  * 设备被吊销（revoked_at 非空）后立即失效；
  * exe 侧持有明文（Windows Credential Manager），不落配置文件、不进前端。
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3

DEFAULT_CAPS = ["exec", "fs", "open_app", "clipboard", "screenshot"]
DEFAULT_POLICY = {"default": "ask", "allow": [], "deny": []}


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(
    conn: sqlite3.Connection,
    *,
    name: str | None = None,
    kind: str = "exe",
    caps: list[str] | None = None,
    policy: dict | None = None,
    now: str | None = None,
) -> dict:
    """签发一台设备。返回明文 token —— 调用方必须只显示一次。"""
    import json

    from .events import now_iso

    device_id = "dev_" + secrets.token_hex(8)
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO devices (id, name, kind, token_hash, caps_json, policy_json,"
        " created_at) VALUES (?,?,?,?,?,?,?)",
        (
            device_id,
            name or kind,
            kind,
            hash_token(token),
            json.dumps(caps or DEFAULT_CAPS, ensure_ascii=False),
            json.dumps(policy or DEFAULT_POLICY, ensure_ascii=False),
            now or now_iso(),
        ),
    )
    return {"device_id": device_id, "token": token, "kind": kind}


def authenticate(conn: sqlite3.Connection, token: str | None) -> dict | None:
    """校验设备令牌；成功返回设备行。
    显式使用 'local'/'default'/'anonymous' 进入本机免配对模式。
    缺失、无效或已吊销的真实令牌必须拒绝，不能静默降级到另一台设备。
    """
    if not token:
        return None
    if token not in ("local", "default", "none", "anonymous"):
        row = conn.execute(
            "SELECT * FROM devices WHERE token_hash = ? AND revoked_at IS NULL",
            (hash_token(token),),
        ).fetchone()
        return dict(row) if row is not None else None

    # 本地免令牌模式：优先查找任意未吊销的本地守护进程或工作站设备
    row = conn.execute(
        "SELECT * FROM devices WHERE revoked_at IS NULL ORDER BY (CASE WHEN kind IN ('daemon', 'exe', 'local') THEN 0 ELSE 1 END), created_at ASC LIMIT 1"
    ).fetchone()
    if row is not None:
        return dict(row)

    # 首次启动没有任何设备时，自动签发默认本地工作站设备
    created = issue(conn, name="Local Workstation", kind="daemon")
    conn.commit()
    row = conn.execute(
        "SELECT * FROM devices WHERE id = ? AND revoked_at IS NULL",
        (created["device_id"],),
    ).fetchone()
    return dict(row) if row is not None else None


def touch(conn: sqlite3.Connection, device_id: str, now: str) -> None:
    conn.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (now, device_id))


def revoke(conn: sqlite3.Connection, device_id: str, now: str) -> int:
    cur = conn.execute(
        "UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (now, device_id),
    )
    return cur.rowcount
