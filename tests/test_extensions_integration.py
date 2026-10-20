"""Regression checks for the selectively ported MCP / mail extensions."""
from __future__ import annotations

import hashlib
import json
import pathlib
import smtplib
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import identity, mailer, mcp, tenancy
from app.db import get_conn, init_db
from app.engine.acp_client import AcpClient
from app.ui import api, extensions
from tools import mcp_email_server as gateway


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "extension.db"))
    monkeypatch.setenv("UI_SESSION_SECRET", "isolated-extension-tests-secret")
    init_db()
    with get_conn() as conn:
        tenancy.ensure(conn)
        mcp.ensure(conn)
        mailer.ensure(conn)
        conn.execute("INSERT INTO tenants(id,name) VALUES ('TENANT-B','Other')")
        conn.execute("""INSERT INTO legal_entities(id,name,type,tenant_id) VALUES
                       ('LE-B','Other','subsidiary','TENANT-B'),
                       ('LE-C','Same tenant','subsidiary','TENANT-HQ')""")
        bosses = {}
        for label, entity, role in (("a", "LE-GROUP-HQ", "ceo"), ("b", "LE-B", "ceo"),
                                    ("c", "LE-C", "ceo"), ("agent", "LE-GROUP-HQ", "agent")):
            bosses[label] = identity.issue(conn, name=label, role=role, legal_entity_id=entity)
            conn.execute("UPDATE identities SET tenant_id=? WHERE id=?",
                         ("TENANT-B" if label == "b" else "TENANT-HQ", bosses[label]["id"]))
        conn.execute("""INSERT INTO ai_employees(id,legal_entity_id,name,status) VALUES
                       ('AI-A','LE-GROUP-HQ','A','online'), ('AI-B','LE-B','B','online'),
                       ('AI-C','LE-C','C','online')""")
    app = FastAPI()
    app.include_router(extensions.router)
    with TestClient(app) as client:
        client.cookies.set("oa_ui", api._make_cookie(bosses["a"]["id"]))
        yield client, bosses, tmp_path


def _server(setup, tenant="TENANT-HQ", who="a", key="private-api-key"):
    _, bosses, _ = setup
    with get_conn() as conn:
        return mcp.add_server(conn, tenant_id=tenant, actor=bosses[who]["id"],
                              name="Local tools", url="http://127.0.0.1:8099/mcp", api_key=key)


def test_foreign_mutations_probe_and_bindings_rejected_without_network(setup, monkeypatch):
    client, bosses, _ = setup
    foreign = _server(setup, "TENANT-B", "b")
    own = _server(setup)
    calls = []
    monkeypatch.setattr(mcp, "_probe_server", lambda *_: calls.append(True))
    for method, path, data in (
        ("PATCH", f"/ui/mcp/{foreign['id']}", {"url": "https://other.example/mcp"}),
        ("DELETE", f"/ui/mcp/{foreign['id']}", None),
        ("POST", f"/ui/mcp/{foreign['id']}/test", None),
        ("PUT", "/ui/mcp/bindings/AI-A", {"server_ids": [foreign["id"]]}),
        ("GET", "/ui/mcp/bindings/AI-B", None),
        ("PUT", "/ui/mcp/bindings/AI-B", {"server_ids": [own["id"]]}),
    ):
        response = client.request(method, path, json=data)
        assert response.status_code == 404, response.text
    assert not calls
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM mcp_servers WHERE id=?", (foreign["id"],)).fetchone()
        assert row["url"] == "http://127.0.0.1:8099/mcp" and row["api_key"] == "private-api-key"
        with pytest.raises(KeyError):
            mcp.update_server(conn, foreign["id"], tenant_id="TENANT-HQ",
                               actor=bosses["a"]["id"], url="https://evil.example/mcp")


def test_admin_active_identity_header_and_entity_boundaries(setup):
    client, bosses, _ = setup
    own = _server(setup)
    assert client.get("/ui/mcp", headers={"X-Tenant-Id": "TENANT-B"}).status_code == 403
    client.cookies.set("oa_ui", api._make_cookie(bosses["agent"]["id"]))
    assert client.get("/ui/mcp").status_code == 403
    assert client.put("/ui/smtp", json={}).status_code == 403
    client.cookies.set("oa_ui", api._make_cookie(bosses["c"]["id"]))
    assert client.put("/ui/mcp/bindings/AI-A", json={"server_ids": [own["id"]]}).status_code == 404
    assert client.get("/ui/mcp").json()["servers"] == []
    assert client.patch(f"/ui/mcp/{own['id']}", json={"enabled": False}).status_code == 404
    assert client.post(f"/ui/mcp/{own['id']}/test").status_code == 404
    with get_conn() as conn:
        identity.revoke(conn, bosses["a"]["id"])
    client.cookies.set("oa_ui", api._make_cookie(bosses["a"]["id"]))
    assert client.get("/ui/mcp").status_code == 401


def test_binding_replacement_is_atomic_and_runtime_entity_scoped(setup):
    client, bosses, _ = setup
    own, foreign = _server(setup), _server(setup, "TENANT-B", "b")
    assert client.put("/ui/mcp/bindings/AI-A", json={"server_ids": [own["id"]]}).status_code == 200
    assert client.put("/ui/mcp/bindings/AI-A",
                      json={"server_ids": [foreign["id"]]}).status_code == 404
    assert client.get("/ui/mcp/bindings/AI-A").json()["server_ids"] == [own["id"]]
    with get_conn() as conn:
        cfg = mcp.session_configs(conn, "AI-A", tenant_id="TENANT-HQ", entity_id="LE-GROUP-HQ")
        assert cfg[0]["headers"][0]["value"] == "Bearer private-api-key"
        with pytest.raises(KeyError):
            mcp.session_configs(conn, "AI-A", tenant_id="TENANT-B")
        with pytest.raises(KeyError):
            mcp.session_configs(conn, "AI-A", tenant_id="TENANT-HQ", entity_id="LE-C")
        mcp.update_server(conn, own["id"], tenant_id="TENANT-HQ", actor=bosses["a"]["id"], enabled=0)
        assert mcp.session_configs(conn, "AI-A", tenant_id="TENANT-HQ") == []


def test_url_change_clears_credential_and_responses_are_whitelisted(setup):
    client, _, _ = setup
    row = _server(setup)
    assert "api_key" not in row and "auth_header" not in row
    response = client.patch(f"/ui/mcp/{row['id']}", json={"url": "https://new.example/mcp"})
    assert response.status_code == 200
    assert response.json()["server"]["has_key"] is False
    assert "private-api-key" not in client.get("/ui/mcp").text
    with get_conn() as conn:
        assert conn.execute("SELECT api_key FROM mcp_servers WHERE id=?", (row["id"],)).fetchone()[0] == ""


@pytest.mark.parametrize("field,value", [
    ("url", "https://user:password@example.com/mcp"),
    ("url", "https://example.com/mcp?api_key=secret"),
    ("url", "file:///etc/passwd"),
    ("auth_header", "Authorization\r\nX-Evil"),
    ("auth_header", "Host"),
    ("auth_scheme", "Bearer\n"),
    ("api_key", "secret\r\ninjected"),
    ("transport", "stdio"),
])
def test_mcp_config_validation(setup, field, value):
    client, _, _ = setup
    data = {"name": "test", "url": "https://example.com/mcp", field: value}
    assert client.post("/ui/mcp", json=data).status_code == 400


def test_probe_failure_does_not_reflect_secret_or_remote_body(setup, monkeypatch):
    client, _, _ = setup
    row = _server(setup)
    def failed(*_):
        raise RuntimeError("reflected private-api-key and response body")
    monkeypatch.setattr(mcp, "_probe_server", failed)
    response = client.post(f"/ui/mcp/{row['id']}/test")
    assert response.status_code == 200 and response.json()["ok"] is False
    assert "private-api-key" not in response.text
    assert "response body" not in client.get("/ui/mcp").text


def test_probe_never_follows_redirect_and_sanitizes_tools(monkeypatch):
    server = {"url": "https://example.test/mcp", "api_key": "a-private-secret",
              "auth_header": "Authorization", "auth_scheme": "Bearer"}
    options, methods = [], []
    client_type = httpx.Client
    def fake_client(**kwargs):
        options.append(kwargs)
        def reply(req):
            body = json.loads(req.content)
            methods.append(body["method"])
            if body["method"] == "initialize":
                return httpx.Response(200, json={"id": 1, "result": {"serverInfo": {"name": "demo"}}})
            if body["method"] == "notifications/initialized":
                return httpx.Response(202)
            return httpx.Response(200, json={"id": 2, "result": {"tools": [
                {"name": "read", "description": "a-private-secret"}]}})
        return client_type(transport=httpx.MockTransport(reply), **kwargs)
    monkeypatch.setattr(mcp.httpx, "Client", fake_client)
    result = mcp._probe_server(server, 1)
    assert options[0]["follow_redirects"] is False
    assert methods == ["initialize", "notifications/initialized", "tools/list"]
    assert result["tools"][0]["description"] == "[redacted]"
    with client_type(transport=httpx.MockTransport(lambda _: httpx.Response(
            307, headers={"location": "https://other.test/mcp"}))) as cli:
        with pytest.raises(RuntimeError, match="307"):
            mcp._rpc(cli, server["url"], {"id": 1}, {})


def test_smtp_scoped_secrets_and_endpoint_changes_clear_password(setup, monkeypatch):
    client, bosses, _ = setup
    payload = dict(host="smtp.example.test", port=587, user="a@example.test", password="smtp-secret",
                   from_addr="a@example.test", secure=False)
    assert client.put("/ui/smtp", json=payload).json()["pass_set"] is True
    assert "smtp-secret" not in client.get("/ui/smtp").text
    client.cookies.set("oa_ui", api._make_cookie(bosses["c"]["id"]))
    assert client.get("/ui/smtp").json()["pass_set"] is False
    assert client.get("/ui/smtp").json()["host"] == ""
    client.cookies.set("oa_ui", api._make_cookie(bosses["b"]["id"]))
    assert client.get("/ui/smtp").json()["pass_set"] is False
    assert client.get("/ui/smtp").json()["host"] == ""
    client.cookies.set("oa_ui", api._make_cookie(bosses["a"]["id"]))
    payload.pop("password")
    payload["host"] = "smtp.changed.test"
    assert client.put("/ui/smtp", json=payload).json()["pass_set"] is False
    def failure(**_):
        raise smtplib.SMTPException("smtp-secret reflected")
    monkeypatch.setattr(mailer, "send_mail", failure)
    response = client.post("/ui/smtp/test", json={"to": "recipient@example.test"})
    assert response.json()["ok"] is False and "smtp-secret" not in response.text
    monkeypatch.setattr(mailer, "send_mail", lambda **_: "<fake-id>")
    assert client.post("/ui/smtp/test", json={"to": "recipient@example.test"}).json()["ok"] is True


def test_schema_and_service_reads_do_not_commit_transactions(setup):
    _, bosses, _ = setup
    with get_conn() as conn:
        conn.execute("UPDATE identities SET name='uncommitted' WHERE id=?", (bosses["a"]["id"],))
        mcp.ensure(conn)
        mailer.ensure(conn)
        mcp.list_servers(conn, tenant_id="TENANT-HQ", actor=bosses["a"]["id"])
        conn.rollback()
        assert conn.execute("SELECT name FROM identities WHERE id=?", (bosses["a"]["id"],)).fetchone()[0] == "a"


def test_legacy_mcp_migration_preserves_known_owner_and_disables_mismatch(setup):
    _, bosses, _ = setup
    with get_conn() as conn:
        conn.execute("DROP TABLE mcp_servers")
        conn.execute(mcp.DDL[0].replace("entity_id TEXT NOT NULL,", ""))
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN created_by TEXT")
        for sid, tid, actor in (("legacy-child", "TENANT-HQ", bosses["c"]["id"]),
                                ("mismatch", "TENANT-HQ", bosses["b"]["id"]),
                                ("known-root", "TENANT-B", None)):
            conn.execute("""INSERT INTO mcp_servers(id,name,url,tenant_id,created_by)
                VALUES (?,'Legacy','http://localhost/mcp',?,?)""", (sid, tid, actor))
        conn.commit()
        conn.execute("BEGIN")
        mcp.ensure(conn)
        actual = {row["id"]: (row["entity_id"], row["enabled"]) for row in
                  conn.execute("SELECT id,entity_id,enabled FROM mcp_servers")}
        assert actual == {"legacy-child": ("LE-C", 1), "mismatch": (None, 0),
                          "known-root": ("LE-B", 1)}
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM pragma_table_info('mcp_servers')"
                            " WHERE name='entity_id'").fetchone()[0] == 0


def test_legacy_public_fields_cannot_leak_query_credentials_or_probe_body():
    data = mcp._public({"url": "https://example.test/mcp?token=legacy-secret",
                        "api_key": "", "last_error": "server returned legacy-secret"})
    assert "legacy-secret" not in json.dumps(data)
    assert data["url"] == "" and data["last_status"] == "configuration_invalid"


def test_parent_binding_stays_usable_but_child_cannot_replace_it(setup):
    client, bosses, _ = setup
    own = _server(setup)
    with get_conn() as conn:
        conn.execute("UPDATE legal_entities SET parent_entity_id='LE-GROUP-HQ' WHERE id='LE-C'")
    assert client.put("/ui/mcp/bindings/AI-C", json={"server_ids": [own["id"]]}).status_code == 200
    client.cookies.set("oa_ui", api._make_cookie(bosses["c"]["id"]))
    assert client.put("/ui/mcp/bindings/AI-C", json={"server_ids": [own["id"]]}).status_code == 404
    assert client.put("/ui/mcp/bindings/AI-C", json={"server_ids": []}).status_code == 200
    with get_conn() as conn:
        assert mcp.session_configs(conn, "AI-C", tenant_id="TENANT-HQ", entity_id="LE-C")[0]["name"]


def test_acp_session_extension_preserves_lifecycle_hook():
    client = AcpClient(["fake"], "workspace")
    client.on_started = lambda _: None
    calls = []
    client.request = lambda method, params, **kw: calls.append((method, params, kw))
    cfg = [{"type": "http", "name": "mail drafts", "url": "http://127.0.0.1/mcp", "headers": []}]
    client.new_session(mcp_servers=cfg)
    assert calls[0][1]["mcpServers"] == cfg and callable(client.on_started)


def test_gateway_outbox_is_idempotent_and_never_uses_smtp(tmp_path, monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("gateway must never connect to SMTP")
    monkeypatch.setattr(smtplib, "SMTP", forbidden)
    monkeypatch.setattr(smtplib, "SMTP_SSL", forbidden)
    kwargs = dict(tenant_id="TENANT-HQ", outbox=str(tmp_path / "outbox"))
    first = gateway.prepare_email("r@example.test", "Subject", "Body", **kwargs)
    second = gateway.prepare_email("r@example.test", "Subject", "Body", **kwargs)
    assert first["record"]["delivery_status"] == "saved"
    assert first["record"]["delivered"] is False
    assert second["record"]["duplicate"] is True
    assert first["record"]["dispatch_id"] == second["record"]["dispatch_id"]
    assert len(list((tmp_path / "outbox").rglob("*.eml"))) == 1
    assert list((tmp_path / "outbox").rglob("*.json"))
    assert "尚未发送" in first["text"]


@pytest.mark.parametrize("damage", ["empty", "truncated", "missing_manifest", "invalid_manifest"])
def test_gateway_repairs_unverified_draft_instead_of_reporting_duplicate(tmp_path, damage):
    kwargs = dict(tenant_id="TENANT-HQ", outbox=str(tmp_path / "outbox"))
    first = gateway.prepare_email("r@example.test", "Subject", "Body", **kwargs)
    eml = next((tmp_path / "outbox").rglob("*.eml"))
    manifest = next((tmp_path / "outbox").rglob("*.json"))
    if damage == "empty":
        eml.write_bytes(b"")
    elif damage == "truncated":
        eml.write_bytes(eml.read_bytes()[:50])
    elif damage == "missing_manifest":
        manifest.unlink()
    else:
        manifest.write_text("partial {", encoding="utf-8")
    repaired = gateway.prepare_email("r@example.test", "Subject", "Body", **kwargs)
    assert repaired["record"]["dispatch_id"] == first["record"]["dispatch_id"]
    assert repaired["record"]["duplicate"] is False
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert hashlib.sha256(eml.read_bytes()).hexdigest() == saved["eml_sha256"]
    assert b"Body" in eml.read_bytes()
    assert gateway.prepare_email("r@example.test", "Subject", "Body", **kwargs)["record"]["duplicate"]


def test_gateway_recovers_failure_between_message_and_manifest(tmp_path, monkeypatch):
    original = gateway._atomic_write
    failed = False
    def once(path, data):
        nonlocal failed
        if path.suffix == ".json" and not failed:
            failed = True
            raise OSError("simulated process interruption")
        return original(path, data)
    monkeypatch.setattr(gateway, "_atomic_write", once)
    kwargs = dict(tenant_id="TENANT-HQ", outbox=str(tmp_path / "outbox"))
    with pytest.raises(OSError):
        gateway.prepare_email("r@example.test", "Subject", "Body", **kwargs)
    result = gateway.prepare_email("r@example.test", "Subject", "Body", **kwargs)
    assert result["record"]["duplicate"] is False
    assert result["record"]["delivery_status"] == "saved"
    assert not list((tmp_path / "outbox").rglob("*.tmp"))


def test_gateway_rejects_repo_secrets_siblings_and_symlink_escape(tmp_path):
    allowed = tmp_path / "artifacts"
    sibling = tmp_path / "artifacts-evil"
    allowed.mkdir()
    sibling.mkdir()
    good = allowed / "report.txt"
    good.write_text("safe")
    secret = allowed / ".env"
    secret.write_text("SECRET")
    bad = sibling / "secret.txt"
    bad.write_text("SECRET")
    kwargs = dict(tenant_id="TENANT-HQ", artifact_dir=str(allowed))
    assert gateway._attachments([str(good)], **kwargs)[0][1] == b"safe"
    for path in (secret, bad):
        with pytest.raises(ValueError):
            gateway._attachments([str(path)], **kwargs)
    with pytest.raises(ValueError):
        gateway._attachments([str(good)], tenant_id="TENANT-HQ")
    with pytest.raises(ValueError):
        gateway._attachments([str(good)], tenant_id="TENANT-HQ", artifact_dir=str(gateway.ROOT))
    try:
        (allowed / "escape.txt").symlink_to(bad)
    except OSError:
        return  # Windows without symlink privilege; containment still tested with sibling.
    with pytest.raises(ValueError):
        gateway._attachments([str(allowed / "escape.txt")], **kwargs)


def test_gateway_registered_artifact_requires_fixed_task_tenant_and_checksum(setup):
    _, _, tmp_path = setup
    path = tmp_path / "report.md"
    path.write_text("report")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    with get_conn() as conn:
        conn.execute("""INSERT INTO tasks(id,legal_entity_id,title,tenant_id)
                       VALUES ('T-MAIL','LE-GROUP-HQ','Mail','TENANT-HQ')""")
        conn.execute("""INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id)
                       VALUES ('R-MAIL','T-MAIL',1,'LE-GROUP-HQ','TENANT-HQ')""")
        conn.execute("""INSERT INTO artifacts(id,run_id,art_seq,file_path,checksum,state,tenant_id)
                       VALUES ('A-MAIL','R-MAIL',1,?,?,'active','TENANT-HQ')""", (str(path), checksum))
    cfg = dict(tenant_id="TENANT-HQ", task_id="T-MAIL", db_path=str(tmp_path / "extension.db"))
    assert gateway._attachments([str(path)], **cfg)[0][1] == b"report"
    with pytest.raises(ValueError):
        gateway._attachments([str(path)], **{**cfg, "tenant_id": "TENANT-B"})
    path.write_text("changed")
    with pytest.raises(ValueError, match="变化"):
        gateway._attachments([str(path)], **cfg)


def test_gateway_http_auth_and_no_send_tool(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), gateway.Handler)
    server.mail_config = {"token": "isolated-test-token", "tenant_id": "TENANT-HQ",
                          "outbox": str(tmp_path), "task_id": "", "artifact_dir": "", "db_path": ""}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/mcp"
    try:
        with httpx.Client(trust_env=False) as client:
            assert client.post(url, json={"id": 1, "method": "tools/list"}).status_code == 401
            headers = {"authorization": "Bearer isolated-test-token"}
            tools = client.post(url, headers=headers, json={"id": 1, "method": "tools/list"}).json()
            assert [t["name"] for t in tools["result"]["tools"]] == ["prepare_email"]
            rejected = client.post(url, headers=headers, json={
                "id": 2, "method": "tools/call", "params": {"name": "send_email"}}).json()
            assert "error" in rejected
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
