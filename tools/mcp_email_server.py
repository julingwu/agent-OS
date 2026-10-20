"""Authenticated local MCP mail preparation gateway.

This release saves .eml drafts only; it never dispatches customer mail. Automatic
SMTP dispatch is deliberately unavailable until durable exact-payload approval
and delivery reconciliation are implemented. SMTP connection tests remain an
explicit administrator action in the application.

Set MCP_EMAIL_TOKEN (at least 16 characters), MCP_MAIL_TENANT and optionally
MCP_MAIL_TASK_ID or MCP_MAIL_ARTIFACT_DIR. Run python tools/mcp_email_server.py.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.mailer import validate_address

LOCK = threading.Lock()
MAX_BYTES = 10 * 1024 * 1024
TOOLS = [{
    "name": "prepare_email",
    "description": "生成可审阅的邮件草稿并保存到本地 outbox；不会向收件人发送。",
    "inputSchema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
            "attachments": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
        }, "required": ["to", "subject", "body"],
    },
}]


def _sensitive(path: pathlib.Path) -> bool:
    return (any(part.startswith(".") or part.lower() in {"node_modules", "__pycache__"}
                for part in path.parts)
            or path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".pfx"}
            or path.name.lower().startswith(("credentials", "secrets")))


def _allowed_artifacts(db_path: str, tenant_id: str, task_id: str) -> dict[pathlib.Path, str]:
    # URI mode=ro prevents a typo from silently creating an empty database.
    uri = pathlib.Path(db_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""SELECT a.file_path,a.checksum FROM artifacts a
            JOIN task_runs r ON r.id=a.run_id JOIN tasks t ON t.id=r.task_id
            JOIN legal_entities e ON e.id=t.legal_entity_id
            WHERE t.id=? AND t.tenant_id=? AND e.tenant_id=?
              AND r.tenant_id=t.tenant_id AND a.tenant_id=t.tenant_id
              AND a.state='active' AND r.id=(SELECT id FROM task_runs
                WHERE task_id=t.id ORDER BY run_seq DESC LIMIT 1)""",
            (task_id, tenant_id, tenant_id)).fetchall()
    return {pathlib.Path(row["file_path"]).resolve(): row["checksum"] for row in rows}


def _attachments(paths: list[str], *, tenant_id: str, task_id: str = "",
                 artifact_dir: str = "", db_path: str = "") -> list[tuple[pathlib.Path, bytes]]:
    if not isinstance(paths, list) or len(paths) > 10 or any(not isinstance(p, str) for p in paths):
        raise ValueError("附件列表无效")
    if not paths:
        return []
    allowed = _allowed_artifacts(db_path, tenant_id, task_id) if task_id else {}
    root = pathlib.Path(artifact_dir).resolve(strict=True) if artifact_dir else None
    if root and (not root.is_dir() or root == ROOT or root in ROOT.parents):
        raise ValueError("附件目录必须是专用产物目录")
    result, total = [], 0
    for raw in paths:
        path = pathlib.Path(raw).resolve(strict=True)
        within_dir = root is not None and path.is_relative_to(root)
        if _sensitive(path) or not path.is_file() or not (path in allowed or within_dir):
            raise ValueError("附件不在获准的任务产物范围")
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("附件超过 10 MB")
        data = path.read_bytes()
        total += len(data)
        if total > MAX_BYTES:
            raise ValueError("附件总量超过 10 MB")
        checksum = hashlib.sha256(data).hexdigest()
        if path in allowed and allowed[path] != checksum:
            raise ValueError("附件已变化，需要重新登记产物")
        result.append((path, data))
    return result


@contextmanager
def _draft_transaction(folder: pathlib.Path):
    # SQLite's OS-backed lock is released on process death. It serializes two
    # gateway processes sharing an outbox as well as normal in-process callers.
    conn = sqlite3.connect(str(folder / ".draft-lock.sqlite"), timeout=10)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _atomic_write(path: pathlib.Path, data: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".draft-", suffix=".tmp", dir=path.parent)
    temporary = pathlib.Path(name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _verified_draft(eml: pathlib.Path, manifest: pathlib.Path, digest: str) -> bool:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return (data.get("payload_sha256") == digest and data.get("delivery_status") == "saved"
                and data.get("dispatch_id") == "DRAFT-" + digest
                and hashlib.sha256(eml.read_bytes()).hexdigest() == data.get("eml_sha256"))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def prepare_email(to: str, subject: str, body: str, attachments: list[str] | None = None,
                  *, tenant_id: str, outbox: str, task_id: str = "",
                  artifact_dir: str = "", db_path: str = "") -> dict:
    if not tenant_id or any(c in tenant_id for c in "/\\\r\n"):
        raise ValueError("需要固定租户")
    to = validate_address(to)
    if not isinstance(subject, str) or not subject.strip() or "\r" in subject or "\n" in subject:
        raise ValueError("邮件主题无效")
    if not isinstance(body, str) or len(body.encode("utf-8")) > 1024 * 1024 or len(subject) > 500:
        raise ValueError("邮件内容过大")
    attached = _attachments(attachments or [], tenant_id=tenant_id, task_id=task_id,
                            artifact_dir=artifact_dir, db_path=db_path)
    payload = {"tenant_id": tenant_id, "task_id": task_id, "to": to,
               "subject": subject.strip(), "body": body,
               "attachments": [{"name": p.name, "sha256": hashlib.sha256(data).hexdigest()}
                               for p, data in attached]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":")).encode()).hexdigest()
    dispatch_id = "DRAFT-" + digest
    folder = pathlib.Path(outbox).resolve() / hashlib.sha256(tenant_id.encode()).hexdigest()[:16]
    folder.mkdir(parents=True, exist_ok=True)
    msg = EmailMessage()
    msg["From"] = "agentos@localhost"
    msg["To"] = to
    msg["Subject"] = subject.strip()
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = f"<{digest}@agentos.local>"
    msg.set_content(body)
    for path, data in attached:
        main, sub = (mimetypes.guess_type(path.name)[0] or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=main, subtype=sub, filename=path.name)
    eml = folder / f"{dispatch_id}.eml"
    manifest = folder / f"{dispatch_id}.json"
    message_bytes = bytes(msg)
    with LOCK, _draft_transaction(folder):
        duplicate = _verified_draft(eml, manifest, digest)
        if not duplicate:
            _atomic_write(eml, message_bytes)
            _atomic_write(manifest, json.dumps({
                "dispatch_id": dispatch_id, "payload_sha256": digest,
                "eml_sha256": hashlib.sha256(message_bytes).hexdigest(),
                "delivery_status": "saved", **payload}, ensure_ascii=False).encode("utf-8"))
    return {"text": "邮件草稿已保存到本地，尚未发送。", "record": {
        "dispatch_id": dispatch_id, "delivery_status": "saved", "transport": "outbox",
        "delivered": False, "duplicate": duplicate, "to": to,
        "attachments": [p.name for p, _ in attached]}}


def _reply(handler, rid, *, result=None, error=None, status=200):
    payload = {"jsonrpc": "2.0", "id": rid}
    payload["error" if error is not None else "result"] = error if error is not None else result
    raw = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # HTTP paths and raw requests do not belong in credential-bearing logs.

    def do_GET(self):
        if self.path == "/health":
            _reply(self, None, result={"ok": True, "delivery": "outbox_only"})
        else:
            _reply(self, None, error={"code": -32601, "message": "not found"}, status=404)

    def do_POST(self):
        cfg = self.server.mail_config
        try:
            size = int(self.headers.get("content-length", "0"))
            if not 0 < size <= 2 * 1024 * 1024:
                raise ValueError("size")
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
        except (ValueError, OSError):
            _reply(self, None, error={"code": -32700, "message": "invalid request"}, status=400)
            return
        if self.path != "/mcp":
            _reply(self, None, error={"code": -32601, "message": "not found"}, status=404)
            return
        if not hmac.compare_digest(self.headers.get("authorization", ""),
                                   "Bearer " + cfg["token"]):
            _reply(self, None, error={"code": -32001, "message": "unauthorized"}, status=401)
            return
        try:
            req = json.loads(raw)
            if not isinstance(req, dict):
                raise ValueError("request")
        except (ValueError, TypeError):
            _reply(self, None, error={"code": -32700, "message": "invalid request"}, status=400)
            return
        rid, method = req.get("id"), req.get("method")
        if rid is None:
            self.send_response(202)
            self.send_header("content-length", "0")
            self.end_headers()
        elif method == "initialize":
            _reply(self, rid, result={"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                                     "serverInfo": {"name": "agentos-mail-drafts", "version": "3.2"}})
        elif method == "tools/list":
            _reply(self, rid, result={"tools": TOOLS})
        elif method == "ping":
            _reply(self, rid, result={})
        elif method == "tools/call":
            params = req.get("params") or {}
            args = params.get("arguments") or {}
            if params.get("name") != "prepare_email" or not isinstance(args, dict):
                _reply(self, rid, error={"code": -32602, "message": "unsupported tool"})
                return
            try:
                if set(args) - {"to", "subject", "body", "attachments"}:
                    raise ValueError("unsupported arguments")
                out = prepare_email(args.get("to", ""), args.get("subject", ""),
                                    args.get("body", ""), args.get("attachments", []),
                                    **{k: v for k, v in cfg.items() if k != "token"})
                _reply(self, rid, result={"content": [{"type": "text", "text": out["text"]}],
                                         "isError": False, "structuredContent": out["record"]})
            except Exception:
                _reply(self, rid, result={"content": [{"type": "text",
                    "text": "草稿未保存，请检查邮件字段、附件范围及登记的校验值。"}], "isError": True})
        else:
            _reply(self, rid, error={"code": -32601, "message": "method not found"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    token = os.environ.get("MCP_EMAIL_TOKEN", "")
    tenant_id = os.environ.get("MCP_MAIL_TENANT", "")
    if len(token) < 16 or not tenant_id:
        parser.error("set MCP_EMAIL_TOKEN (at least 16 characters) and MCP_MAIL_TENANT")
    if os.environ.get("MCP_MAIL_DELIVERY", "outbox") != "outbox":
        parser.error("this release supports outbox drafts only")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.mail_config = {"token": token, "tenant_id": tenant_id,
        "outbox": os.environ.get("MCP_MAIL_DIR", str(ROOT / "data" / "mail_outbox")),
        "task_id": os.environ.get("MCP_MAIL_TASK_ID", ""),
        "artifact_dir": os.environ.get("MCP_MAIL_ARTIFACT_DIR", ""),
        "db_path": os.environ.get("DB_PATH", str(ROOT / "data" / "oa.db"))}
    print(f"mail draft MCP listening on http://{args.host}:{args.port}/mcp", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
