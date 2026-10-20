"""Tenant-scoped SMTP configuration and explicit administrator test sends."""
from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import mcp


def ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS smtp_configs (
        tenant_id TEXT NOT NULL, entity_id TEXT NOT NULL,
        host TEXT DEFAULT '', port INTEGER DEFAULT 587,
        user TEXT DEFAULT '', password TEXT DEFAULT '', from_addr TEXT DEFAULT '',
        secure INTEGER DEFAULT 0, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(tenant_id,entity_id))""")


def _config(conn, tenant_id: str, entity_id: str) -> dict:
    row = conn.execute("SELECT * FROM smtp_configs WHERE tenant_id=? AND entity_id=?",
                       (tenant_id, entity_id)).fetchone()
    return dict(row) if row else dict(host="", port=587, user="", password="", from_addr="", secure=0)


def smtp_settings(conn, *, tenant_id: str, actor: str) -> dict:
    ident = mcp.require_admin(conn, actor, tenant_id)
    cfg = _config(conn, tenant_id, ident["legal_entity_id"])
    return {"host": cfg["host"], "port": cfg["port"], "user": cfg["user"],
            "from_addr": cfg["from_addr"], "secure": bool(cfg["secure"]),
            "pass_set": bool(cfg["password"]), "enabled": bool(cfg["host"]),
            "gateway_delivery": "outbox_only"}


def validate_address(address: str) -> str:
    value = (address or "").strip()
    if not re.fullmatch(r"[^@\s<>,;\r\n]+@[^@\s<>,;\r\n]+\.[^@\s<>,;\r\n]+", value):
        raise ValueError("InvalidEmailAddress")
    return value


def save_smtp(conn, *, tenant_id: str, actor: str, host: str, port: int, user: str,
              password: str | None, from_addr: str, secure: bool) -> dict:
    ident = mcp.require_admin(conn, actor, tenant_id)
    host, user, from_addr = host.strip(), user.strip(), from_addr.strip()
    if len(host) > 253 or (host and not re.fullmatch(r"[A-Za-z0-9.-]+", host)):
        raise ValueError("InvalidSmtpHost")
    if not 1 <= port <= 65535:
        raise ValueError("InvalidSmtpPort")
    if any(c in user for c in "\r\n") or len(user) > 320:
        raise ValueError("InvalidSmtpUser")
    if from_addr:
        validate_address(from_addr)
    old = _config(conn, tenant_id, ident["legal_entity_id"])
    if password is None:
        password = old["password"] if (host, port, user) == (
            old["host"], old["port"], old["user"]) else ""
    if len(password) > 4096:
        raise ValueError("InvalidSmtpPassword")
    conn.execute("""INSERT INTO smtp_configs(tenant_id,entity_id,host,port,user,password,from_addr,secure)
        VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,entity_id) DO UPDATE SET
        host=excluded.host,port=excluded.port,user=excluded.user,password=excluded.password,
        from_addr=excluded.from_addr,secure=excluded.secure,updated_at=CURRENT_TIMESTAMP""",
        (tenant_id, ident["legal_entity_id"], host, port, user, password, from_addr, int(secure)))
    mcp._audit(conn, actor, tenant_id, "smtp_saved", "smtp", {"configured": bool(host)})
    return smtp_settings(conn, tenant_id=tenant_id, actor=actor)


def send_mail(*, host: str, port: int, user: str, password: str, from_addr: str,
              to: str, subject: str, body: str, secure: bool = False) -> str:
    msg = EmailMessage()
    msg["From"] = validate_address(from_addr or user)
    msg["To"] = validate_address(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="agentos.local")
    msg.set_content(body or "")
    if secure or port == 465:
        srv = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
    else:
        srv = smtplib.SMTP(host, port, timeout=20)
    try:
        if not secure and port != 465:
            srv.starttls(context=ssl.create_default_context())
        if user:
            srv.login(user, password)
        refused = srv.send_message(msg)
        if refused:
            raise RuntimeError("SMTP recipient refused")
    finally:
        srv.quit()
    return str(msg["Message-ID"])


def test_smtp(conn, *, tenant_id: str, actor: str, to: str) -> dict:
    ident = mcp.require_admin(conn, actor, tenant_id)
    to = validate_address(to)
    cfg = _config(conn, tenant_id, ident["legal_entity_id"])
    if not cfg["host"]:
        return {"ok": False, "error": "还没有配置 SMTP 服务器"}
    try:
        mid = send_mail(**{k: cfg[k] for k in
                          ("host", "port", "user", "password", "from_addr", "secure")},
                        to=to, subject="Agent OS SMTP 测试",
                        body="这是一封由管理员手动触发的 Agent OS SMTP 连通性测试邮件。")
    except Exception as exc:
        return {"ok": False, "error": f"SMTP 测试失败（{type(exc).__name__}），请检查配置"}
    mcp._audit(conn, actor, tenant_id, "smtp_test_sent", mid, {"to": to})
    return {"ok": True, "message_id": mid, "to": to, "delivery_status": "submitted"}
