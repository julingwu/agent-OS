"""MCP tool extensions (v3.0).

One row per external MCP server (tenant-scoped) + a binding table per employee.
`session_config()` produces exactly the ACP `McpServer` shape that
`session/new.mcpServers` expects; DSH's ACP profile advertises HTTP transport.

Secrets are stored in SQLite (local single-user install). The API never returns
the raw key, only `has_key`.
"""
from __future__ import annotations

import json
import sqlite3

import httpx

from .audit import new_id, write_audit
from .tenancy import DEFAULT_TENANT

DDL = """
CREATE TABLE IF NOT EXISTS mcp_servers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  transport TEXT NOT NULL DEFAULT 'http',      -- http | stdio
  url TEXT,
  api_key TEXT,
  auth_header TEXT DEFAULT 'Authorization',
  auth_scheme TEXT DEFAULT 'Bearer',
  command TEXT,
  args_json TEXT,
  env_json TEXT,
  enabled INTEGER DEFAULT 1,
  tools_json TEXT,
  last_status TEXT,
  last_error TEXT,
  last_checked_at TIMESTAMP,
  tenant_id TEXT DEFAULT 'TENANT-HQ',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS agent_mcp_tools (
  agent_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  enabled INTEGER DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (agent_id, server_id)
);
CREATE INDEX IF NOT EXISTS idx_mcp_tenant ON mcp_servers(tenant_id);
"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def _row(conn, sid: str):
    return conn.execute("SELECT * FROM mcp_servers WHERE id=?", (sid,)).fetchone()


def list_servers(conn, tenant_id: str = DEFAULT_TENANT) -> list[dict]:
    ensure(conn)
    rows = conn.execute(
        """SELECT m.*, (SELECT COUNT(*) FROM agent_mcp_tools b
                        WHERE b.server_id=m.id AND b.enabled=1) AS bound_agents
           FROM mcp_servers m WHERE COALESCE(m.tenant_id,?)=?
           ORDER BY m.created_at DESC""", (DEFAULT_TENANT, tenant_id)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["api_key"] = ""                 # never leave the server
        d["has_key"] = bool(r["api_key"])
        try:
            d["tools"] = json.loads(r["tools_json"] or "[]")
        except json.JSONDecodeError:
            d["tools"] = []
        d.pop("tools_json", None)
        out.append(d)
    return out


def add_server(conn, *, name: str, url: str, api_key: str = "",
               auth_header: str = "Authorization", auth_scheme: str = "Bearer",
               transport: str = "http", command: str = "", args: list | None = None,
               env: dict | None = None, tenant_id: str = DEFAULT_TENANT,
               actor: str = "") -> dict:
    ensure(conn)
    sid = new_id("MCP")
    conn.execute(
        """INSERT INTO mcp_servers
             (id, name, transport, url, api_key, auth_header, auth_scheme,
              command, args_json, env_json, tenant_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (sid, name.strip()[:80], transport, (url or "").strip(),
         (api_key or "").strip(), auth_header or "Authorization",
         auth_scheme if auth_scheme is not None else "Bearer",
         command or "", json.dumps(args or [], ensure_ascii=False),
         json.dumps(env or {}, ensure_ascii=False), tenant_id))
    write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="settings", task_id=None,
                layer="system", action="mcp_server_added", target=sid,
                payload={"name": name[:80], "transport": transport, "url": url,
                         "has_key": bool(api_key), "actor": actor},
                response={"ok": True})
    conn.commit()
    return dict(_row(conn, sid))


def update_server(conn, sid: str, **fields) -> dict:
    ensure(conn)
    allowed = {"name", "url", "api_key", "auth_header", "auth_scheme", "enabled",
               "transport", "command"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=?")
            args.append(v)
    if sets:
        args.append(sid)
        conn.execute(f"UPDATE mcp_servers SET {', '.join(sets)} WHERE id=?", args)
        if any(k in fields for k in ("url", "api_key", "auth_header", "auth_scheme", "transport")):
            conn.execute("""UPDATE mcp_servers SET tools_json=NULL, last_status=NULL,
                            last_error=NULL, last_checked_at=NULL WHERE id=?""", (sid,))
        conn.commit()
    row = _row(conn, sid)
    if row is None:
        raise KeyError(sid)
    return dict(row)


def delete_server(conn, sid: str) -> None:
    ensure(conn)
    conn.execute("DELETE FROM agent_mcp_tools WHERE server_id=?", (sid,))
    conn.execute("DELETE FROM mcp_servers WHERE id=?", (sid,))
    conn.commit()


def bindings(conn, agent_id: str) -> list[str]:
    ensure(conn)
    return [r["server_id"] for r in conn.execute(
        "SELECT server_id FROM agent_mcp_tools WHERE agent_id=? AND enabled=1",
        (agent_id,))]


def set_bindings(conn, agent_id: str, server_ids: list[str]) -> list[str]:
    ensure(conn)
    conn.execute("DELETE FROM agent_mcp_tools WHERE agent_id=?", (agent_id,))
    for sid in dict.fromkeys(server_ids):
        conn.execute("INSERT OR IGNORE INTO agent_mcp_tools (agent_id, server_id)"
                     " VALUES (?,?)", (agent_id, sid))
    conn.commit()
    return bindings(conn, agent_id)


def servers_for_agent(conn, agent_id: str | None) -> list[dict]:
    if not agent_id:
        return []
    ensure(conn)
    rows = conn.execute(
        """SELECT m.* FROM mcp_servers m
           JOIN agent_mcp_tools b ON b.server_id=m.id
           WHERE b.agent_id=? AND b.enabled=1 AND m.enabled=1
           ORDER BY m.created_at""", (agent_id,)).fetchall()
    return [dict(r) for r in rows]


def auth_headers(server: dict) -> list[dict]:
    key = (server.get("api_key") or "").strip()
    if not key:
        return []
    name = (server.get("auth_header") or "Authorization").strip()
    scheme = (server.get("auth_scheme") or "").strip()
    return [{"name": name, "value": f"{scheme} {key}".strip() if scheme else key}]


def session_config(server: dict) -> dict:
    """ACP McpServer entry. HTTP is what DSH advertises; stdio kept for other hosts."""
    if (server.get("transport") or "http") == "stdio":
        return {
            "name": server["name"],
            "command": server.get("command") or "",
            "args": json.loads(server.get("args_json") or "[]"),
            "env": [{"name": k, "value": v} for k, v in
                    json.loads(server.get("env_json") or "{}").items()],
        }
    return {
        "type": "http",
        "name": server["name"],
        "url": server.get("url") or "",
        "headers": auth_headers(server),
    }


def session_configs(conn, agent_id: str | None) -> list[dict]:
    return [session_config(s) for s in servers_for_agent(conn, agent_id)]


def _rpc(cli: httpx.Client, url: str, body: dict, headers: dict) -> tuple[dict, str | None]:
    r = cli.post(url, json=body, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    sid = r.headers.get("mcp-session-id")
    ctype = r.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in r.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                msg = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if msg.get("id") == body.get("id") and ("result" in msg or "error" in msg):
                return msg, sid
        raise RuntimeError("SSE response without a matching JSON-RPC result")
    return r.json(), sid


def probe(server: dict, timeout: float = 20.0) -> dict:
    """initialize + tools/list. Raises with a human-readable reason on failure."""
    if (server.get("transport") or "http") != "http":
        raise RuntimeError("当前只探测 HTTP MCP；stdio 由 ACP 进程自行拉起")
    url = (server.get("url") or "").strip()
    if not url:
        raise RuntimeError("缺少 MCP Server URL")
    headers = {"content-type": "application/json",
               "accept": "application/json, text/event-stream"}
    for h in auth_headers(server):
        headers[h["name"]] = h["value"]

    with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=True) as cli:
        init, sid = _rpc(cli, url, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "agentos", "version": "3.0.0"}}}, headers)
        if "error" in init:
            raise RuntimeError(f"initialize: {init['error']}")
        info = (init.get("result") or {}).get("serverInfo") or {}
        # Streamable HTTP: echo the session id on later calls when the server issued one.
        if sid:
            headers["mcp-session-id"] = sid
        cli.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                 headers=headers)
        listed, _ = _rpc(cli, url, {"jsonrpc": "2.0", "id": 2,
                                    "method": "tools/list", "params": {}}, headers)
    if "error" in listed:
        raise RuntimeError(f"tools/list: {listed['error']}")
    tools = (listed.get("result") or {}).get("tools") or []
    return {"server": info, "tools": tools,
            "tool_names": [t.get("name") for t in tools if t.get("name")]}


def record_probe(conn, sid: str, result: dict | None, error: str | None) -> dict:
    ensure(conn)
    if error:
        conn.execute("""UPDATE mcp_servers SET last_status='error', last_error=?,
                        last_checked_at=CURRENT_TIMESTAMP WHERE id=?""", (error[:500], sid))
    else:
        conn.execute("""UPDATE mcp_servers SET last_status='ok', last_error=NULL,
                        tools_json=?, last_checked_at=CURRENT_TIMESTAMP WHERE id=?""",
                     (json.dumps(result["tools"], ensure_ascii=False), sid))
    conn.commit()
    return dict(_row(conn, sid))


def seed_demo(conn) -> None:
    """Idempotent dev seed: a local email MCP on :8099 (server must be running)."""
    ensure(conn)
    if conn.execute("SELECT 1 FROM mcp_servers WHERE name='本地邮件网关'").fetchone():
        return
    add_server(conn, name="本地邮件网关", url="http://127.0.0.1:8099/mcp",
               api_key="dev-token", actor="seed")
