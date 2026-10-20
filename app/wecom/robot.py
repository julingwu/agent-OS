"""WeCom group robot (群机器人) push: one HTTPS webhook, no corp token needed.

This is deliberately separate from ``WeComClient``: the corp client needs
``CORP_ID``/``SECRET``/``AGENT_ID`` and a user id to address, while a group robot
is a single key-bound URL that posts into one chat. Business events (project
closed 9/9, red-line block) must not depend on the corp app being enabled.

Configuration lives per tenant in ``wecom_robots``; a deployment may instead set
``WECOM_ROBOT_WEBHOOK`` for a single-tenant install. The webhook key is a
credential: reads only ever return ``webhook_set`` plus a short tail hint.

``WECOM_MOCK=1`` (the switch the corp client already honours) or
``WECOM_ROBOT_DRY_RUN=1`` writes the payload into ``wecom_robot_outbox`` instead
of the network, so the whole chain is testable without a real group.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from urllib.parse import parse_qs, urlparse

import httpx

from ..audit import new_id

log = logging.getLogger("wecom.robot")

OFFICIAL_HOST = "qyapi.weixin.qq.com"
OFFICIAL_PATH = "/cgi-bin/webhook/send"

DDL = """
CREATE TABLE IF NOT EXISTS wecom_robots (
  tenant_id TEXT PRIMARY KEY,
  webhook TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  on_project_done INTEGER NOT NULL DEFAULT 1,
  on_redline INTEGER NOT NULL DEFAULT 1,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS wecom_robot_outbox (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  event TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def _mock() -> bool:
    return (os.environ.get("WECOM_MOCK", "0") == "1"
            or os.environ.get("WECOM_ROBOT_DRY_RUN", "0") == "1")


def validate_webhook(url: str) -> str:
    """Only the official group-robot endpoint is accepted.

    An admin-supplied URL is an SSRF primitive if unchecked, so the host and
    path are pinned; ``WECOM_ROBOT_ALLOW_CUSTOM_HOST=1`` exists for on-prem
    gateways and is documented as such.
    """
    value = (url or "").strip()
    if not value:
        raise ValueError("WebhookRequired")
    if len(value) > 512:
        raise ValueError("InvalidWebhook")
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ValueError("WebhookMustBeHttps")
    if os.environ.get("WECOM_ROBOT_ALLOW_CUSTOM_HOST") != "1":
        if parsed.hostname != OFFICIAL_HOST or parsed.path != OFFICIAL_PATH:
            raise ValueError("UnsupportedWebhookHost")
    key = (parse_qs(parsed.query).get("key") or [""])[0].strip()
    if not 8 <= len(key) <= 128:
        raise ValueError("InvalidWebhookKey")
    return f"https://{parsed.hostname}{parsed.path}?key={key}"


def _row(conn, tenant_id: str):
    return conn.execute("SELECT * FROM wecom_robots WHERE tenant_id=?",
                        (tenant_id,)).fetchone()


def _hint(webhook: str) -> str:
    if not webhook:
        return ""
    key = (parse_qs(urlparse(webhook).query).get("key") or [""])[0]
    return "…" + key[-6:] if len(key) > 6 else "…"


def resolve(conn, tenant_id: str) -> dict:
    """Effective robot config: DB row first, deployment env as fallback."""
    row = _row(conn, tenant_id)
    if row is not None and row["webhook"]:
        return {"webhook": row["webhook"], "source": "tenant",
                "enabled": bool(row["enabled"]),
                "on_project_done": bool(row["on_project_done"]),
                "on_redline": bool(row["on_redline"])}
    env = (os.environ.get("WECOM_ROBOT_WEBHOOK") or "").strip()
    return {"webhook": env, "source": "env" if env else "none",
            "enabled": bool(env), "on_project_done": True, "on_redline": True}


def settings(conn, *, tenant_id: str, actor: str) -> dict:
    from .. import mcp
    mcp.require_admin(conn, actor, tenant_id)
    cfg = resolve(conn, tenant_id)
    return {"webhook_set": bool(cfg["webhook"]), "webhook_hint": _hint(cfg["webhook"]),
            "enabled": cfg["enabled"], "on_project_done": cfg["on_project_done"],
            "on_redline": cfg["on_redline"], "source": cfg["source"],
            "mock": _mock()}


def save(conn, *, tenant_id: str, actor: str, webhook: str | None = None,
         enabled: bool | None = None, on_project_done: bool | None = None,
         on_redline: bool | None = None) -> dict:
    from .. import mcp
    ident = mcp.require_admin(conn, actor, tenant_id)
    old = _row(conn, tenant_id)
    cur_webhook = old["webhook"] if old is not None else ""
    if webhook is not None:
        webhook = webhook.strip()
        # 传空串 = 清除；传新地址 = 校验后替换
        cur_webhook = validate_webhook(webhook) if webhook else ""
    enabled = (bool(old["enabled"]) if old is not None else bool(cur_webhook)) \
        if enabled is None else bool(enabled)
    if on_project_done is None:
        on_project_done = bool(old["on_project_done"]) if old is not None else True
    if on_redline is None:
        on_redline = bool(old["on_redline"]) if old is not None else True
    if not cur_webhook:
        enabled = False
    conn.execute("""INSERT INTO wecom_robots
        (tenant_id,webhook,enabled,on_project_done,on_redline)
        VALUES (?,?,?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET
        webhook=excluded.webhook,enabled=excluded.enabled,
        on_project_done=excluded.on_project_done,on_redline=excluded.on_redline,
        updated_at=CURRENT_TIMESTAMP""",
        (tenant_id, cur_webhook, int(enabled), int(bool(on_project_done)),
         int(bool(on_redline))))
    mcp._audit(conn, actor, tenant_id, "wecom_robot_saved", "wecom_robot",
               {"configured": bool(cur_webhook), "enabled": bool(enabled),
                "on_project_done": bool(on_project_done),
                "on_redline": bool(on_redline),
                "legal_entity_id": ident["legal_entity_id"]})
    conn.commit()
    return settings(conn, tenant_id=tenant_id, actor=actor)


def _deliver(webhook: str, body: dict) -> dict:
    with httpx.Client(timeout=8, trust_env=False) as http:
        resp = http.post(webhook, json=body)
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text[:200]}
    errcode = data.get("errcode")
    return {"ok": resp.status_code == 200 and errcode == 0,
            "status": resp.status_code, "errcode": errcode,
            "errmsg": data.get("errmsg") or data.get("raw")}


def push(conn, *, tenant_id: str, event: str, markdown: str) -> dict:
    """Send one markdown message. Never raises: a broken robot must not break work.

    Returns a small dict the caller records as evidence (transport + ok).
    """
    cfg = resolve(conn, tenant_id)
    payload = {"msgtype": "markdown", "markdown": {"content": markdown[:4000]}}
    if not cfg["webhook"]:
        return {"ok": False, "transport": "unconfigured", "detail": "未配置企微群机器人"}
    if not cfg["enabled"]:
        return {"ok": False, "transport": "disabled", "detail": "机器人已停用"}
    if _mock():
        out_id = new_id("RMO")
        conn.execute("INSERT INTO wecom_robot_outbox (id,tenant_id,event,body)"
                     " VALUES (?,?,?,?)",
                     (out_id, tenant_id, event, json.dumps(payload, ensure_ascii=False)))
        conn.commit()
        return {"ok": True, "transport": "outbox", "id": out_id}
    try:
        result = _deliver(cfg["webhook"], payload)
    except Exception as exc:                 # noqa: BLE001 - 网络问题不能冒泡
        log.warning("group robot push failed: %s", exc)
        return {"ok": False, "transport": "webhook", "detail": str(exc)[:200]}
    if not result["ok"]:
        log.warning("group robot rejected: %s", result)
    return {"ok": result["ok"], "transport": "webhook",
            "errcode": result["errcode"], "detail": result["errmsg"]}


def send_test(conn, *, tenant_id: str, actor: str) -> dict:
    from .. import mcp
    mcp.require_admin(conn, actor, tenant_id)
    out = push(conn, tenant_id=tenant_id, event="test",
               markdown="### Agent OS 通知自检\n> 如果你在群里看到这条，企微群机器人配置已生效。")
    mcp._audit(conn, actor, tenant_id, "wecom_robot_tested", "wecom_robot",
               {"ok": bool(out.get("ok")), "transport": out.get("transport")})
    conn.commit()
    return out
