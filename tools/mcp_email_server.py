"""Minimal HTTP MCP server that exposes a real `send_email` tool.

Run:  python tools/mcp_email_server.py --port 8099 --token dev-token

Transport: Streamable HTTP (JSON responses), endpoint POST /mcp.
Delivery: SMTP when SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS are set; otherwise
a standards-shaped .eml file is written to data/mail_outbox (so the demo works
without credentials). Every call is appended to outbox/sent.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import smtplib
import sqlite3
import ssl
import sys
import threading
import time
import uuid
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTBOX = pathlib.Path(os.environ.get("MCP_MAIL_DIR", ROOT / "data" / "mail_outbox"))
TOKEN = os.environ.get("MCP_EMAIL_TOKEN", "dev-token")
LOCK = threading.Lock()

TOOLS = [{
    "name": "send_email",
    "description": "发送一封真实邮件。参数 to/subject/body；未配置 SMTP 时会落到本地 outbox。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "收件人邮箱"},
            "subject": {"type": "string", "description": "主题"},
            "body": {"type": "string", "description": "正文（纯文本）"},
            "attachments": {"type": "array", "items": {"type": "string"},
                            "description": "可选：要附带的本地文件绝对路径"},
        },
        "required": ["to", "subject", "body"],
    },
}]


def _smtp_config() -> dict | None:
    """Env wins (standalone dev); otherwise read the same settings rows as the UI."""
    host = os.environ.get("SMTP_HOST", "").strip()
    if host:
        return {"host": host,
                "port": int(os.environ.get("SMTP_PORT", "465" if os.environ.get("SMTP_SSL") else "587")),
                "user": os.environ.get("SMTP_USER", ""),
                "password": os.environ.get("SMTP_PASS", ""),
                "from_addr": os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", ""),
                "secure": os.environ.get("SMTP_SSL") == "1" or os.environ.get("SMTP_PORT") == "465"}
    db_path = os.environ.get("DB_PATH") or str(ROOT / "data" / "oa.db")
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        vals = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key,value FROM settings WHERE key LIKE 'smtp_%'")}
        conn.close()
    except Exception:                              # noqa: BLE001
        return None
    if not vals.get("smtp_host"):
        return None
    return {"host": vals.get("smtp_host", ""),
            "port": int(vals.get("smtp_port") or 587),
            "user": vals.get("smtp_user", ""),
            "password": vals.get("smtp_pass", ""),
            "from_addr": vals.get("smtp_from") or vals.get("smtp_user", ""),
            "secure": str(vals.get("smtp_secure", "0")) == "1"}


def _send_smtp(msg: EmailMessage, cfg: dict) -> str:
    port = int(cfg["port"])
    if cfg.get("secure") or port == 465:
        srv = smtplib.SMTP_SSL(cfg["host"], port, timeout=20,
                               context=ssl.create_default_context())
    else:
        srv = smtplib.SMTP(cfg["host"], port, timeout=20)
        srv.starttls(context=ssl.create_default_context())
    try:
        if cfg.get("user"):
            srv.login(cfg["user"], cfg.get("password", ""))
        srv.send_message(msg)
    finally:
        srv.quit()
    return "smtp"


def _attach(msg: EmailMessage, paths: list[str]) -> list[str]:
    import mimetypes
    roots = [ROOT.resolve()]
    ws = os.environ.get("M0_WORKSPACE")
    if ws:
        roots.append(pathlib.Path(ws).resolve())
    if os.environ.get("LOCALAPPDATA"):
        roots.append((pathlib.Path(os.environ["LOCALAPPDATA"]) /
                      "oaagent-m0" / "workspace").resolve())
    names = []
    for raw in paths or []:
        p = pathlib.Path(raw).resolve()
        if not any(str(p).lower().startswith(str(r).lower()) for r in roots):
            raise ValueError(f"附件路径不在允许目录：{p}")
        if not p.is_file():
            raise ValueError(f"附件不存在：{p}")
        ctype, _ = mimetypes.guess_type(p.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype,
                           filename=p.name)
        names.append(p.name)
    return names


def send_email(to: str, subject: str, body: str, attachments: list[str] | None = None) -> dict:
    to = (to or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", to):
        raise ValueError("收件人邮箱格式不对")
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("主题不能为空")
    cfg = _smtp_config()
    sender = (cfg or {}).get("from_addr") or "agentos@local"
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="agentos.local")
    msg.set_content(body or "")
    attached = _attach(msg, attachments or [])

    transport = "outbox"
    path = None
    if cfg:
        try:
            transport = _send_smtp(msg, cfg)
        except Exception as exc:                   # noqa: BLE001
            # 配了 SMTP 却发失败：明确报错，不能悄悄退回 outbox 冒充已发。
            raise RuntimeError(f"SMTP 发送失败：{exc}") from exc
    else:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        fname = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6] + ".eml"
        path = OUTBOX / fname
        path.write_bytes(bytes(msg))
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "to": to, "subject": subject,
              "transport": transport, "path": str(path) if path else "",
              "message_id": msg["Message-ID"], "bytes": len(bytes(msg)),
              "attachments": attached}
    OUTBOX.mkdir(parents=True, exist_ok=True)
    with LOCK, (OUTBOX / "sent.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    where = f"，已落盘 {path.name}" if path else ""
    att = f"，附件 {len(attached)} 个（{'、'.join(attached)}）" if attached else ""
    return {"text": f"邮件已通过 {transport} 投递给 {to}{where}{att}",
            "record": record}


def _reply(handler, req_id, result=None, error=None, status=200) -> None:
    payload = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(raw)))
    handler.send_header("mcp-protocol-version", "2025-03-26")
    handler.end_headers()
    handler.wfile.write(raw)


class Handler(BaseHTTPRequestHandler):
    server_version = "oaagent-email-mcp/3.0"

    def log_message(self, fmt, *args):             # keep stdout clean for the app
        sys.stderr.write("[mcp-email] " + (fmt % args) + "\n")

    def _authorized(self) -> bool:
        if not TOKEN:
            return True
        got = self.headers.get("authorization", "")
        return got == f"Bearer {TOKEN}"

    def do_GET(self):                              # noqa: N802
        if self.path.startswith("/health"):
            raw = b'{"ok":true,"service":"mcp-email"}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        _reply(self, None, error={"code": -32601, "message": "SSE stream not supported"}, status=405)

    def do_POST(self):                             # noqa: N802
        if not self.path.rstrip("/").endswith("/mcp"):
            _reply(self, None, error={"code": -32601, "message": "not found"}, status=404)
            return
        if not self._authorized():
            _reply(self, None, error={"code": -32001, "message": "unauthorized"}, status=401)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            req = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            _reply(self, None, error={"code": -32700, "message": "parse error"}, status=400)
            return
        method = req.get("method")
        rid = req.get("id")
        if rid is None:                            # notification
            self.send_response(202)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        if method == "initialize":
            _reply(self, rid, {
                "protocolVersion": req.get("params", {}).get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "oaagent-email", "version": "3.0.0"},
            })
        elif method == "ping":
            _reply(self, rid, {})
        elif method == "tools/list":
            _reply(self, rid, {"tools": TOOLS})
        elif method == "tools/call":
            params = req.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name != "send_email":
                _reply(self, rid, error={"code": -32602, "message": f"unknown tool {name}"})
                return
            try:
                out = send_email(args.get("to", ""), args.get("subject", ""),
                                 args.get("body", ""), args.get("attachments") or [])
                _reply(self, rid, {"content": [{"type": "text", "text": out["text"]}],
                                   "isError": False, "structuredContent": out["record"]})
            except Exception as exc:               # noqa: BLE001
                _reply(self, rid, {"content": [{"type": "text", "text": f"发送失败：{exc}"}],
                                   "isError": True})
        else:
            _reply(self, rid, error={"code": -32601, "message": f"method not found: {method}"})


def main() -> int:
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--token", default=TOKEN)
    a = ap.parse_args()
    TOKEN = a.token
    OUTBOX.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"mcp-email listening on http://{a.host}:{a.port}/mcp  outbox={OUTBOX}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
