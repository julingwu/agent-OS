"""SMTP settings + test send (v3.1).

The MCP email gateway is a separate process, so it reads the same `settings`
rows from SQLite on every tools/call. Password is never returned by the API.
"""
from __future__ import annotations

import smtplib
import ssl
import sqlite3
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

KEYS = ("smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_from", "smtp_secure")


def _rows(conn: sqlite3.Connection) -> dict:
    try:
        return {r["key"]: r["value"] for r in conn.execute(
            "SELECT key,value FROM settings WHERE key LIKE 'smtp_%'")}
    except sqlite3.OperationalError:
        return {}


def smtp_settings(conn: sqlite3.Connection) -> dict:
    vals = _rows(conn)
    return {
        "host": vals.get("smtp_host", ""),
        "port": int(vals.get("smtp_port") or 587),
        "user": vals.get("smtp_user", ""),
        "from_addr": vals.get("smtp_from", ""),
        "secure": str(vals.get("smtp_secure", "0")) == "1",
        "pass_set": bool(vals.get("smtp_pass")),
        "enabled": bool(vals.get("smtp_host")),
    }


def save_smtp(conn: sqlite3.Connection, *, host: str, port: int, user: str,
              password: str | None, from_addr: str, secure: bool) -> dict:
    pairs = {
        "smtp_host": (host or "").strip(),
        "smtp_port": str(int(port or 587)),
        "smtp_user": (user or "").strip(),
        "smtp_from": (from_addr or "").strip() or (user or "").strip(),
        "smtp_secure": "1" if secure else "0",
    }
    if password:
        pairs["smtp_pass"] = password
    for k, v in pairs.items():
        conn.execute("INSERT INTO settings (key,value) VALUES (?,?)"
                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    conn.commit()
    return smtp_settings(conn)


def send_mail(*, host: str, port: int, user: str, password: str, from_addr: str,
              to: str, subject: str, body: str, secure: bool = False) -> str:
    msg = EmailMessage()
    msg["From"] = from_addr or user
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="agentos.local")
    msg.set_content(body or "")
    if secure or int(port) == 465:
        srv = smtplib.SMTP_SSL(host, int(port), timeout=20,
                               context=ssl.create_default_context())
    else:
        srv = smtplib.SMTP(host, int(port), timeout=20)
        srv.starttls(context=ssl.create_default_context())
    try:
        if user:
            srv.login(user, password)
        srv.send_message(msg)
    finally:
        srv.quit()
    return str(msg["Message-ID"])
