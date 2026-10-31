"""Tenant-admin managed HTTP MCP connections.

Inspired by sync/v3.2. Configured servers are trusted capabilities: ACP does not
provide a reliable pre-dispatch authorization hook for external MCP tools.
Secrets stay in the local database and are exposed only to the ACP transport.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx

from . import tenancy, tenant
from .audit import new_id, write_audit

DDL = (
    """CREATE TABLE IF NOT EXISTS mcp_servers (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, transport TEXT NOT NULL DEFAULT 'http',
        url TEXT NOT NULL, api_key TEXT, auth_header TEXT DEFAULT 'Authorization',
        auth_scheme TEXT DEFAULT 'Bearer', enabled INTEGER DEFAULT 1,
        tools_json TEXT, last_status TEXT, last_error TEXT, last_checked_at TIMESTAMP,
        tenant_id TEXT NOT NULL, entity_id TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS agent_mcp_tools (
        agent_id TEXT NOT NULL, server_id TEXT NOT NULL, enabled INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(agent_id,server_id))""",
    "CREATE INDEX IF NOT EXISTS idx_mcp_tenant ON mcp_servers(tenant_id)",
)


def ensure(conn) -> None:
    """Schema initialization; never commit an existing caller transaction."""
    for statement in DDL:
        conn.execute(statement)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(mcp_servers)")}
    if "entity_id" not in columns:
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN entity_id TEXT")
        for row in conn.execute("SELECT * FROM mcp_servers").fetchall():
            owner = None
            actor = row["created_by"] if "created_by" in columns else None
            if not actor and conn.execute("SELECT 1 FROM sqlite_master"
                                          " WHERE type='table' AND name='ai_audit_logs'").fetchone():
                audit = conn.execute("SELECT payload FROM ai_audit_logs WHERE target=?"
                                     " AND action='mcp_server_added' ORDER BY rowid LIMIT 1",
                                     (row["id"],)).fetchone()
                if audit:
                    try:
                        actor = json.loads(audit["payload"] or "{}").get("actor")
                    except (ValueError, TypeError):
                        pass
            if actor:
                owner = conn.execute("""SELECT e.id FROM identities i JOIN legal_entities e
                    ON e.id=i.legal_entity_id WHERE i.id=? AND i.tenant_id=? AND e.tenant_id=?""",
                    (actor, row["tenant_id"], row["tenant_id"])).fetchone()
            if owner is None and not actor:
                roots = conn.execute("""SELECT e.id FROM legal_entities e
                    LEFT JOIN legal_entities p ON p.id=e.parent_entity_id
                    WHERE e.tenant_id=? AND e.state='active'
                      AND (p.id IS NULL OR p.tenant_id<>e.tenant_id)""",
                    (row["tenant_id"],)).fetchall()
                owner = roots[0] if len(roots) == 1 else None
            if owner:
                conn.execute("UPDATE mcp_servers SET entity_id=? WHERE id=?", (owner["id"], row["id"]))
            else:
                conn.execute("UPDATE mcp_servers SET enabled=0 WHERE id=?", (row["id"],))


def require_admin(conn, actor: str, tenant_id: str) -> dict:
    row = conn.execute("SELECT * FROM identities WHERE id=? AND state='active'", (actor,)).fetchone()
    if row is None:
        raise PermissionError("InactiveIdentity")
    if tenancy.of_identity(conn, actor) != tenant_id:
        raise PermissionError("TenantMismatch")
    entity = conn.execute("SELECT state FROM legal_entities WHERE id=?",
                          (row["legal_entity_id"],)).fetchone()
    if entity is None or entity["state"] != "active":
        raise PermissionError("InactiveIdentity")
    if tenancy.of_entity(conn, row["legal_entity_id"]) != tenant_id:
        raise PermissionError("TenantMismatch")
    if row["role"] not in {"ceo", "system"}:
        raise PermissionError("AdminRequired")
    return dict(row)


def _audit(conn, actor: str, tenant_id: str, action: str, target: str, payload=None):
    ident = require_admin(conn, actor, tenant_id)
    write_audit(conn, legal_entity_id=ident["legal_entity_id"], verb="settings",
                actor=actor, action=action, target=target,
                payload={"tenant_id": tenant_id, **(payload or {})}, response={"ok": True})


def _row(conn, sid: str, tenant_id: str, actor: str | None = None) -> dict:
    row = conn.execute("SELECT * FROM mcp_servers WHERE id=? AND tenant_id=?",
                       (sid, tenant_id)).fetchone()
    if row is None:
        raise KeyError("McpServerNotFound")
    if actor is not None:
        ident = require_admin(conn, actor, tenant_id)
        if ident["role"] != "system" and row["entity_id"] != ident["legal_entity_id"]:
            raise KeyError("McpServerNotFound")
    return dict(row)


def _public(row: dict) -> dict:
    # Whitelist; legacy command/env/header configuration must never escape here.
    keys = ("id", "name", "transport", "url", "enabled", "last_status",
            "last_error", "last_checked_at", "created_at")
    out = {key: row.get(key) for key in keys}
    out["has_key"] = bool(row.get("api_key"))
    if out.get("last_error"):
        # Legacy probe errors could contain arbitrary server response text.
        out["last_error"] = "MCP 连接失败，请检查配置后重新测试"
    try:
        _validated({"url": row.get("url") or ""})
    except ValueError:
        out["url"] = ""
        out["last_status"] = "configuration_invalid"
    try:
        out["tools"] = json.loads(row.get("tools_json") or "[]")
    except (ValueError, TypeError):
        out["tools"] = []
    return _redact(out, row.get("api_key") or "")


def _validated(fields: dict) -> dict:
    out = dict(fields)
    if out.get("transport", "http") != "http":
        raise ValueError("OnlyHttpMcpSupported")
    if "name" in out:
        name = str(out["name"]).strip()
        if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError("InvalidMcpName")
        out["name"] = name
    if "url" in out:
        url = str(out["url"]).strip()
        try:
            p = urlsplit(url)
            valid = (p.scheme in {"http", "https"} and bool(p.hostname)
                     and not p.username and not p.password and not p.query and not p.fragment
                     and (p.port is None or 0 < p.port < 65536))
        except ValueError:
            valid = False
        if not valid or any(c.isspace() or ord(c) < 32 for c in url) or len(url) > 2048:
            raise ValueError("InvalidMcpUrl")
        out["url"] = url
    if "auth_header" in out:
        raw_header = str(out["auth_header"])
        header = raw_header.strip()
        if (not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,80}", header)
                or any(ord(c) < 32 for c in raw_header)
                or header.lower() in {"host", "content-length", "content-type", "accept",
                                      "connection", "transfer-encoding", "cookie"}):
            raise ValueError("InvalidAuthHeader")
        out["auth_header"] = header
    if "auth_scheme" in out:
        raw_scheme = str(out["auth_scheme"])
        scheme = raw_scheme.strip()
        if any(ord(c) < 32 for c in raw_scheme) or (
                scheme and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", scheme)):
            raise ValueError("InvalidAuthScheme")
        out["auth_scheme"] = scheme
    if "api_key" in out:
        key = str(out["api_key"])
        if len(key) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in key):
            raise ValueError("InvalidApiKey")
        out["api_key"] = key.strip()
    if "enabled" in out:
        if out["enabled"] not in (0, 1, False, True):
            raise ValueError("InvalidEnabled")
        out["enabled"] = int(out["enabled"])
    return out


def list_servers(conn, *, tenant_id: str, actor: str) -> list[dict]:
    ident = require_admin(conn, actor, tenant_id)
    return [_public(dict(r)) for r in conn.execute(
        """SELECT * FROM mcp_servers WHERE tenant_id=? AND (?='system' OR entity_id=?)
           ORDER BY created_at,id""", (tenant_id, ident["role"], ident["legal_entity_id"]))]


def add_server(conn, *, tenant_id: str, actor: str, name: str, url: str,
               api_key: str = "", auth_header: str = "Authorization",
               auth_scheme: str = "Bearer", transport: str = "http") -> dict:
    ident = require_admin(conn, actor, tenant_id)
    vals = _validated(dict(name=name, url=url, api_key=api_key, auth_header=auth_header,
                           auth_scheme=auth_scheme, transport=transport))
    sid = new_id("MCP")
    conn.execute("""INSERT INTO mcp_servers
        (id,tenant_id,name,url,api_key,auth_header,auth_scheme,transport,entity_id)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (sid, tenant_id, vals["name"], vals["url"], vals["api_key"],
         vals["auth_header"], vals["auth_scheme"], "http", ident["legal_entity_id"]))
    _audit(conn, actor, tenant_id, "mcp_server_added", sid, {"has_key": bool(api_key)})
    return _public(_row(conn, sid, tenant_id, actor))


def update_server(conn, sid: str, *, tenant_id: str, actor: str, **fields) -> dict:
    require_admin(conn, actor, tenant_id)
    before = _row(conn, sid, tenant_id, actor)
    allowed = {"name", "url", "api_key", "auth_header", "auth_scheme", "enabled"}
    if set(fields) - allowed:
        raise ValueError("UnknownMcpField")
    vals = _validated(fields)
    if vals.get("url", before["url"]) != before["url"] and "api_key" not in vals:
        vals["api_key"] = ""  # Never forward the old credential to a new endpoint.
    if vals:
        conn.execute(f"UPDATE mcp_servers SET {','.join(k+'=?' for k in vals)}"
                     " WHERE id=? AND tenant_id=?", (*vals.values(), sid, tenant_id))
        if set(vals) & {"url", "api_key", "auth_header", "auth_scheme"}:
            conn.execute("""UPDATE mcp_servers SET tools_json=NULL,last_status=NULL,
                         last_error=NULL,last_checked_at=NULL WHERE id=? AND tenant_id=?""",
                         (sid, tenant_id))
        _audit(conn, actor, tenant_id, "mcp_server_updated", sid, {"fields": sorted(vals)})
    return _public(_row(conn, sid, tenant_id, actor))


def delete_server(conn, sid: str, *, tenant_id: str, actor: str) -> None:
    require_admin(conn, actor, tenant_id)
    _row(conn, sid, tenant_id, actor)
    conn.execute("DELETE FROM agent_mcp_tools WHERE server_id=?", (sid,))
    conn.execute("DELETE FROM mcp_servers WHERE id=? AND tenant_id=?", (sid, tenant_id))
    _audit(conn, actor, tenant_id, "mcp_server_deleted", sid)


def _employee(conn, agent_id: str, tenant_id: str, actor: str | None = None,
              entity_id: str | None = None) -> dict:
    row = conn.execute("""SELECT e.* FROM ai_employees e
        JOIN legal_entities le ON le.id=e.legal_entity_id
        WHERE e.id=? AND COALESCE(e.status,'online')<>'fired' AND le.state='active'""",
        (agent_id,)).fetchone()
    if row is None or tenancy.of_entity(conn, row["legal_entity_id"]) != tenant_id:
        raise KeyError("EmployeeNotFound")
    if entity_id is not None and row["legal_entity_id"] != entity_id:
        raise KeyError("EmployeeNotFound")
    if actor is not None:
        ident = require_admin(conn, actor, tenant_id)
        if row["legal_entity_id"] not in tenant.visible_entities(conn, ident):
            raise KeyError("EmployeeNotFound")
    return dict(row)


def _server_permits_employee(conn, server: dict, employee: dict, tenant_id: str) -> bool:
    owner = conn.execute("SELECT id FROM legal_entities WHERE id=? AND tenant_id=? AND state='active'",
                         (server["entity_id"], tenant_id)).fetchone()
    if owner is None:
        return False
    scope = tenant.visible_entities(conn, {"role": "ceo", "legal_entity_id": owner["id"],
                                           "tenant_id": tenant_id})
    return employee["legal_entity_id"] in scope


def bindings(conn, agent_id: str, *, tenant_id: str, actor: str) -> list[str]:
    _employee(conn, agent_id, tenant_id, actor)
    ident = require_admin(conn, actor, tenant_id)
    return [r["server_id"] for r in conn.execute(
        """SELECT b.server_id FROM agent_mcp_tools b JOIN mcp_servers m ON m.id=b.server_id
        WHERE b.agent_id=? AND b.enabled=1 AND m.tenant_id=?
          AND (?='system' OR m.entity_id=?) ORDER BY b.server_id""",
        (agent_id, tenant_id, ident["role"], ident["legal_entity_id"]))]


def set_bindings(conn, agent_id: str, server_ids: list[str], *, tenant_id: str,
                 actor: str) -> list[str]:
    employee = _employee(conn, agent_id, tenant_id, actor)
    ident = require_admin(conn, actor, tenant_id)
    ids = list(dict.fromkeys(server_ids))
    if len(ids) > 50:
        raise ValueError("TooManyMcpServers")
    for sid in ids:  # Validate all before replacing existing bindings.
        server = _row(conn, sid, tenant_id, actor)
        if not _server_permits_employee(conn, server, employee, tenant_id):
            raise KeyError("McpServerNotFound")
    conn.execute("""DELETE FROM agent_mcp_tools WHERE agent_id=? AND server_id IN (
        SELECT id FROM mcp_servers WHERE tenant_id=? AND (?='system' OR entity_id=?))""",
        (agent_id, tenant_id, ident["role"], ident["legal_entity_id"]))
    conn.executemany("INSERT INTO agent_mcp_tools(agent_id,server_id) VALUES (?,?)",
                     [(agent_id, sid) for sid in ids])
    _audit(conn, actor, tenant_id, "mcp_bound", agent_id, {"servers": ids})
    return bindings(conn, agent_id, tenant_id=tenant_id, actor=actor)


def auth_headers(server: dict) -> list[dict]:
    key = server.get("api_key") or ""
    if not key:
        return []
    return [{"name": server["auth_header"],
             "value": f"{server.get('auth_scheme') or ''} {key}".strip()}]


def session_configs(conn, agent_id: str | None, *, tenant_id: str,
                    entity_id: str | None = None) -> list[dict]:
    active = conn.execute("SELECT state FROM tenants WHERE id=?", (tenant_id,)).fetchone()
    if active is None or active["state"] != "active":
        raise PermissionError("InactiveTenant")
    if not agent_id:
        return []
    employee = _employee(conn, agent_id, tenant_id, entity_id=entity_id)
    rows = conn.execute(
        """SELECT m.* FROM mcp_servers m JOIN agent_mcp_tools b ON b.server_id=m.id
        WHERE b.agent_id=? AND b.enabled=1 AND m.enabled=1 AND m.tenant_id=?
        ORDER BY m.id""", (agent_id, tenant_id))
    result = []
    for row in rows:
        server = dict(row)
        if not _server_permits_employee(conn, server, employee, tenant_id):
            continue
        _validated({k: server[k] for k in
                    ("url", "name", "api_key", "auth_header", "auth_scheme", "transport")})
        result.append({"type": "http", "name": server["name"], "url": server["url"],
                       "headers": auth_headers(server)})
    return result


def _redact(value, key: str):
    if isinstance(value, dict):
        return {_redact(k, key): _redact(v, key) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value]
    return value.replace(key, "[redacted]") if key and isinstance(value, str) else value


def _rpc(cli, url: str, body: dict, headers: dict) -> tuple[dict, str | None]:
    response = cli.post(url, json=body, headers=headers)
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"MCP HTTP {response.status_code}")
    if len(response.content) > 1024 * 1024:
        raise RuntimeError("MCP response too large")
    if "text/event-stream" in response.headers.get("content-type", ""):
        messages = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    messages.append(json.loads(line[5:].strip()))
                except ValueError:
                    continue
        result = next((m for m in messages if isinstance(m, dict) and
                       m.get("id") == body["id"]), None)
    else:
        result = response.json()
    if not isinstance(result, dict) or result.get("id") != body["id"] or "result" not in result:
        raise RuntimeError("Invalid MCP response")
    return result["result"], response.headers.get("mcp-session-id")


def _probe_server(server: dict, timeout: float) -> dict:
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    headers.update({h["name"]: h["value"] for h in auth_headers(server)})
    with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as cli:
        init, sid = _rpc(cli, server["url"], {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "agentos", "version": "3.2"}}}, headers)
        headers["mcp-protocol-version"] = "2025-03-26"
        if sid:
            headers["mcp-session-id"] = sid
        initialized = cli.post(server["url"], headers=headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized"})
        if not 200 <= initialized.status_code < 300:
            raise RuntimeError(f"MCP HTTP {initialized.status_code}")
        listed, _ = _rpc(cli, server["url"], {"jsonrpc": "2.0", "id": 2,
                        "method": "tools/list", "params": {}}, headers)
    tools = listed.get("tools", [])
    if not isinstance(tools, list) or len(tools) > 100 or any(not isinstance(t, dict) for t in tools):
        raise RuntimeError("Invalid MCP tools")
    return _redact({"server": init.get("serverInfo", {}), "tools": tools,
                    "tool_names": [t["name"] for t in tools if isinstance(t.get("name"), str)]},
                   server.get("api_key") or "")


def probe(conn, sid: str, *, tenant_id: str, actor: str, timeout: float = 20) -> dict:
    require_admin(conn, actor, tenant_id)
    server = _row(conn, sid, tenant_id, actor)
    try:
        _validated({k: server[k] for k in
                    ("url", "name", "api_key", "auth_header", "auth_scheme", "transport")})
        result = _probe_server(server, timeout)
    except Exception as exc:
        # External exception text / server bodies may echo Authorization.
        error = f"MCP 连接失败（{type(exc).__name__}），请检查地址、凭据和服务状态"
        conn.execute("""UPDATE mcp_servers SET last_status='error',last_error=?,
            last_checked_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?""", (error, sid, tenant_id))
        return {"ok": False, "error": error}
    conn.execute("""UPDATE mcp_servers SET last_status='ok',last_error=NULL,tools_json=?,
        last_checked_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?""",
        (json.dumps(result["tools"], ensure_ascii=False), sid, tenant_id))
    return {"ok": True, **result}
