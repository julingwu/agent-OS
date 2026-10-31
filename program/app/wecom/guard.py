"""Callback hardening: MsgId idempotency (anti-replay) + source-IP allowlist.

Both are opt-in via env so local mock work stays frictionless:

    WECOM_IP_ALLOWLIST=1        enforce the allowlist (default off)
    WECOM_IP_RANGES=1.2.3.0/24  extra CIDRs (comma separated)
    WECOM_TRUST_PROXY=1         read the client IP from CF-Connecting-IP / X-Forwarded-For

WeCom retries a callback it did not get an answer for, so the same MsgId can arrive
twice; without a ledger that is two tasks for one sentence.
"""
from __future__ import annotations

import ipaddress
import os
import sqlite3
import time

import httpx

CACHE_TTL = 3600
_cached_ranges: list[ipaddress._BaseNetwork] = []
_cached_at = 0.0


def _env_ranges() -> list[ipaddress._BaseNetwork]:
    out = []
    for chunk in (os.environ.get("WECOM_IP_RANGES") or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            continue
    return out


def wecom_ranges(refresh: bool = False) -> list[ipaddress._BaseNetwork]:
    """Tencent's own callback egress IPs (getcallbackip) + anything configured locally."""
    global _cached_ranges, _cached_at
    if not refresh and _cached_ranges and time.time() - _cached_at < CACHE_TTL:
        return _cached_ranges
    ranges = _env_ranges()
    corp, secret = os.environ.get("WECOM_CORP_ID", ""), os.environ.get("WECOM_SECRET", "")
    if corp and secret and os.environ.get("WECOM_MOCK") != "1":
        try:
            with httpx.Client(base_url="https://qyapi.weixin.qq.com", timeout=8,
                              trust_env=False) as http:
                tok = http.get("/cgi-bin/gettoken",
                               params={"corpid": corp, "corpsecret": secret}).json()
                if tok.get("errcode") == 0:
                    ips = http.get("/cgi-bin/getcallbackip",
                                   params={"access_token": tok["access_token"]}).json()
                    for ip in ips.get("ip_list", []):
                        try:
                            ranges.append(ipaddress.ip_network(ip, strict=False))
                        except ValueError:
                            continue
        except Exception:            # noqa: BLE001 - never block a callback on this
            pass
    _cached_ranges, _cached_at = ranges, time.time()
    return ranges


def client_ip(request) -> str:
    if os.environ.get("WECOM_TRUST_PROXY", "1") == "1":
        for header in ("CF-Connecting-IP", "X-Real-IP"):
            value = request.headers.get(header)
            if value:
                return value.strip()
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def ip_allowed(ip: str) -> bool:
    if os.environ.get("WECOM_IP_ALLOWLIST", "0") != "1":
        return True
    if not ip:
        return False
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True        # loopback is this machine only; the tunnel never sets it
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in wecom_ranges())


# ------------------------------------------------------------------ replay
def seen(conn: sqlite3.Connection, key: str) -> bool:
    """True if this message was already handled; records it when it is new."""
    if not key:
        return False
    cur = conn.execute("INSERT OR IGNORE INTO wecom_seen (msg_key) VALUES (?)", (key,))
    conn.commit()
    return cur.rowcount == 0


def message_key(msg: dict, raw: str) -> str:
    """MsgId when present; events sometimes omit it, so fall back to a stable digest."""
    msg_id = (msg.get("MsgId") or "").strip()
    if msg_id:
        return f"msg:{msg_id}"
    parts = [msg.get("FromUserName", ""), msg.get("CreateTime", ""),
             msg.get("MsgType", ""), msg.get("Event", ""), msg.get("EventKey", "")]
    if all(parts):
        return "evt:" + "|".join(parts)
    import hashlib
    return "raw:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def prune(conn: sqlite3.Connection, days: int = 14) -> int:
    cur = conn.execute(
        "DELETE FROM wecom_seen WHERE created_at < datetime('now', ?)", (f"-{days} days",))
    conn.commit()
    return cur.rowcount
