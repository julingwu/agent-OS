"""M20 (v3.0): 经验人工审核 / MCP 工具扩展 / 多租户隔离。"""
from __future__ import annotations

import pathlib
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from app import experience, identity, mcp, pipeline, tenancy
from app.db import connect, get_conn, init_db
from app.ui import store

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "v30.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("MCP_MAIL_DIR", str(tmp_path / "outbox"))
    init_db()
    conn = connect()
    store.ensure(conn)
    conn.execute("INSERT OR IGNORE INTO legal_entities (id,name,type)"
                 " VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
    conn.execute("INSERT OR IGNORE INTO legal_entities (id,name,type,parent_entity_id,tenant_id)"
                 " VALUES ('LE-BETA','贝塔事业部','department','LE-GROUP-HQ','TENANT-BETA')")
    conn.execute("INSERT OR IGNORE INTO tenants (id,name,plan) VALUES ('TENANT-BETA','贝塔租户','team')")
    hq = identity.issue(conn, name="HQ老板", role="ceo", legal_entity_id="LE-GROUP-HQ")
    beta = identity.issue(conn, name="Beta老板", role="ceo", legal_entity_id="LE-BETA",
                          tenant_id="TENANT-BETA")
    conn.execute("""INSERT OR IGNORE INTO ai_employees
                    (id, legal_entity_id, name, model, status, preset)
                    VALUES ('AI-BETA-01','LE-BETA','小贝','deepseek-chat','online','standard')""")
    hq_chat = store.get_or_create_dm(conn, "AI-BETA-01", "HQ 会话", "LE-BETA")
    beta_chat = store.get_or_create_dm(conn, "AI-BETA-01", "Beta 会话", "LE-BETA",
                                       tenant_id="TENANT-BETA")
    beta_chat_id = beta_chat["id"]
    hq_proj = pipeline.create_project(conn, name="HQ 项目", requirement="HQ only")
    beta_proj = pipeline.create_project(conn, name="Beta 项目", requirement="Beta only",
                                        entity="LE-BETA", tenant_id="TENANT-BETA")
    conn.commit()
    ids = {"hq": hq, "beta": beta, "hq_chat": hq_chat["id"], "beta_chat": beta_chat_id,
           "hq_proj": hq_proj["project_id"], "beta_proj": beta_proj["project_id"]}
    conn.close()
    from app.main import app
    with TestClient(app) as c:
        yield c, ids


def _login(c, ident: dict):
    r = c.post("/ui/login", json={"identity_id": ident["id"], "secret": ident["secret"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_tenant_scoped_chats_and_projects(env):
    c, ids = env
    me = _login(c, ids["hq"])
    assert me["tenant_id"] == "TENANT-HQ"

    r = c.get("/ui/chats", headers={"x-tenant-id": "TENANT-HQ"})
    assert r.status_code == 200
    hq_ids = {x["id"] for x in r.json()["chats"]}
    assert ids["hq_chat"] in hq_ids and ids["beta_chat"] not in hq_ids
    assert all(x["tenant_id"] == "TENANT-HQ" for x in r.json()["chats"])
    r = c.get("/ui/chats", headers={"x-tenant-id": "TENANT-BETA"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "TenantMismatch"

    r = c.get("/ui/projects", headers={"x-tenant-id": "TENANT-HQ"})
    assert [p["id"] for p in r.json()["projects"]] == [ids["hq_proj"]]
    assert c.get(f"/ui/projects/{ids['beta_proj']}").status_code == 404

    _login(c, ids["beta"])
    r = c.get("/ui/chats", headers={"x-tenant-id": "TENANT-BETA"})
    beta_ids = {x["id"] for x in r.json()["chats"]}
    assert beta_ids == {ids["beta_chat"]}
    assert c.get(f"/ui/chats/{ids['hq_chat']}/messages").status_code == 404
    assert c.get(f"/ui/projects/{ids['beta_proj']}").status_code == 200
    assert c.get(f"/ui/projects/{ids['hq_proj']}").status_code == 404


def test_experience_needs_human_approval(env):
    c, ids = env
    _login(c, ids["hq"])
    with get_conn() as conn:
        e = experience.add(conn, kind="decision", title="外部工具默认不注入",
                           content="MCP 工具必须按员工显式绑定，禁止全局默认开启。",
                           tags=["mcp", "安全"], source_type="manual", source_id="V3-1")
    assert e["state"] == "pending"
    r = c.get("/ui/experiences?state=pending")
    assert any(x["id"] == e["id"] for x in r.json()["experiences"])
    assert r.json()["stats"]["by_state"]["pending"] >= 1

    assert c.post(f"/ui/experiences/{e['id']}/review",
                  json={"action": "approve"}).status_code == 200
    r = c.get("/ui/experiences?state=approved")
    row = next(x for x in r.json()["experiences"] if x["id"] == e["id"])
    assert row["state"] == "approved" and row["reviewed_by"]

    with get_conn() as conn:
        ctx = experience.context_for(conn, "接入 MCP 外部工具", record=False)
        assert e["id"] in ctx["ids"]
        e2 = experience.add(conn, kind="rejection", title="错误经验", content="不应注入的错误结论",
                            tags=["mcp"], source_type="manual", source_id="V3-2")
    assert c.post(f"/ui/experiences/{e2['id']}/review",
                  json={"action": "reject", "note": "结论错误"}).status_code == 200
    with get_conn() as conn:
        ctx2 = experience.context_for(conn, "接入 MCP 外部工具", record=False)
        assert e2["id"] not in ctx2["ids"]
        assert experience.list_all(conn, state="rejected")[0]["state"] == "rejected"
        audit = conn.execute("SELECT 1 FROM ai_audit_logs WHERE action='experience_reviewed'"
                             " AND target=?", (e["id"],)).fetchone()
        assert audit


def test_mcp_probe_bind_and_session_config(env, tmp_path):
    c, ids = env
    _login(c, ids["hq"])

    # Real MCP HTTP server in-process, ephemeral port.
    from tools import mcp_email_server as mail
    mail.OUTBOX = pathlib.Path(tmp_path / "outbox")
    mail.TOKEN = "test-token"
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mail.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/mcp"
    try:
        r = c.post("/ui/mcp", json={"name": "测试邮件", "url": url, "api_key": "test-token"})
        assert r.status_code == 200, r.text
        sid = r.json()["server"]["id"]
        assert r.json()["server"]["api_key"] == ""

        t = c.post(f"/ui/mcp/{sid}/test").json()
        assert t["ok"] and t["tool_names"] == ["send_email"]

        with get_conn() as conn:
            assert mcp.session_configs(conn, "AI-BETA-01") == []   # 未绑定 = 不注入
        r = c.put("/ui/mcp/bindings/AI-BETA-01", json={"server_ids": [sid]})
        assert r.status_code == 200, r.text
        with get_conn() as conn:
            cfgs = mcp.session_configs(conn, "AI-BETA-01")
        assert cfgs and cfgs[0]["type"] == "http"
        assert cfgs[0]["url"] == url
        assert cfgs[0]["headers"] == [{"name": "Authorization", "value": "Bearer test-token"}]

        # Direct tools/call proves the tool really executes (outbox, since no SMTP here).
        import httpx
        call = httpx.post(url, headers={"authorization": "Bearer test-token"}, json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "send_email", "arguments": {
                "to": "qa@example.com", "subject": "M20", "body": "hello"}}},
            timeout=10, trust_env=False).json()
        assert call["result"]["isError"] is False
        assert list(mail.OUTBOX.glob("*.eml"))
    finally:
        srv.shutdown()


def test_tenancy_resolve_rejects_foreign_header(env):
    conn = connect()
    try:
        assert tenancy.resolve(conn, "missing", None) == "TENANT-HQ"
        with pytest.raises(PermissionError):
            tenancy.resolve(conn, "missing", "TENANT-BETA")
    finally:
        conn.close()
