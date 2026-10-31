"""Real runner control flow with a recording ACP transport and no model call."""
from __future__ import annotations

import json

import pytest

from app import mcp, trace
from app.db import get_conn
from app.engine import runner_acp
from app.ui import store
from tests.test_extensions_integration import setup, _server


@pytest.fixture()
def runtime(setup, monkeypatch):
    _, bosses, tmp_path = setup
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX_ACP", "0")
    connection = get_conn()
    conn = connection.__enter__()
    trace.ensure(conn)
    store.ensure(conn)
    conn.execute("""INSERT INTO tasks(id,legal_entity_id,tenant_id,title,owner_employee_id)
                   VALUES ('T-MCP','LE-GROUP-HQ','TENANT-HQ','MCP runtime','AI-A')""")
    conn.execute("""INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id)
                   VALUES ('R-MCP','T-MCP',1,'LE-GROUP-HQ','TENANT-HQ')""")
    conn.commit()
    server = _server(setup, key="runtime-private-key")
    mcp.set_bindings(conn, "AI-A", [server["id"]], tenant_id="TENANT-HQ", actor=bosses["a"]["id"])
    conn.commit()
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(runner_acp.base, "_log_path", lambda rid: logs / f"{rid}.jsonl")
    monkeypatch.setattr(runner_acp.base, "start_heartbeat", lambda *args: None)
    monkeypatch.setattr(runner_acp.base, "collect_artifact_files", lambda *args, **kwargs: [])
    monkeypatch.setattr(runner_acp.base, "attach_files", lambda *args, **kwargs: [])
    monkeypatch.setattr(runner_acp.base, "settle_budget", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner_acp.base, "finish", lambda conn, **kwargs: kwargs)
    calls, hooks = [], {}

    class RecordingClient:
        pid = None

        def __init__(self, *args, **kwargs):
            self.log, self.stderr = [], []

        def start(self):
            calls.append(("start",))

        def new_session(self, cwd=None, mcp_servers=None):
            calls.append(("new", mcp_servers))
            if hooks.get("session"):
                hooks["session"]()
            return {"sessionId": "mcp-recorded-session"}

        def resume(self, sid, cwd=None):
            calls.append(("resume", sid))
            if hooks.get("session"):
                hooks["session"]()

        def prompt(self, sid, text, on_update=None, **kwargs):
            calls.append(("prompt", sid))
            if hooks.get("echo"):
                # Model/tool diagnostics can reflect either the full header or key.
                self.log.append({"content": "Bearer runtime-private-key",
                                 "runtime-private-key": "diagnostic key"})
                self.stderr.append("runtime-private-key")
                on_update({"sessionUpdate": "agent_thought_chunk",
                           "content": {"text": "using runtime-private-key"}})
                on_update({"sessionUpdate": "agent_message_chunk",
                           "content": {"text": "Bearer runtime-private-key"}})
            return {"result": {"stopReason": "end_turn"}, "updates": 0}

        def close(self, **kwargs):
            calls.append(("close",))

    monkeypatch.setattr(runner_acp, "AcpClient", RecordingClient)
    yield conn, bosses["a"]["id"], server["id"], calls, hooks, logs
    trace._ACTIVE.pop("R-MCP", None)
    connection.__exit__(None, None, None)


def _execute(runtime, resume=None):
    conn, actor, _, _, _, _ = runtime
    return runner_acp.execute_acp(
        conn, "R-MCP", task_id="T-MCP", entity="LE-GROUP-HQ", actor=actor,
        prompt="Read the approved data", runner="acp", resume_session=resume,
        est_tokens=1000, timeout=3, hb_interval=0)


def _parked(runtime):
    conn, _, _, _, _, _ = runtime
    configs, digest = runner_acp._mcp_authority(conn, "R-MCP", "T-MCP", "AI-A", None)
    conn.execute("UPDATE task_runs SET session_id='mcp-recorded-session' WHERE id='R-MCP'")
    conn.commit()
    return configs, digest


def test_authorized_session_receives_current_mcp_config_before_prompt(runtime):
    result = _execute(runtime)
    assert result["ok"] is True
    calls = runtime[3]
    assert [call[0] for call in calls] == ["start", "new", "prompt", "close"]
    supplied = calls[1][1]
    assert supplied[0]["url"] == "http://127.0.0.1:8099/mcp"
    assert supplied[0]["headers"] == [{"name": "Authorization", "value": "Bearer runtime-private-key"}]


def test_stable_authority_resumes_existing_session(runtime):
    _, digest = _parked(runtime)
    result = _execute(runtime, "mcp-recorded-session")
    assert result["ok"] is True
    assert [call[0] for call in runtime[3]] == ["start", "resume", "prompt", "close"]
    assert runtime[0].execute(
        "SELECT mcp_fingerprint FROM task_runs WHERE id='R-MCP'").fetchone()[0] == digest


@pytest.mark.parametrize("mutation", ["disabled", "deleted", "unbound", "key_changed", "fired",
                                     "tenant_disabled", "actor_revoked", "actor_foreign_tenant",
                                     "actor_other_entity"])
def test_revoked_authority_cannot_start_or_prompt_a_resumed_session(runtime, mutation):
    conn, actor, sid, calls, _, _ = runtime
    _, before = _parked(runtime)
    if mutation == "disabled":
        mcp.update_server(conn, sid, tenant_id="TENANT-HQ", actor=actor, enabled=0)
    elif mutation == "deleted":
        mcp.delete_server(conn, sid, tenant_id="TENANT-HQ", actor=actor)
    elif mutation == "unbound":
        mcp.set_bindings(conn, "AI-A", [], tenant_id="TENANT-HQ", actor=actor)
    elif mutation == "key_changed":
        mcp.update_server(conn, sid, tenant_id="TENANT-HQ", actor=actor, api_key="replacement")
    elif mutation == "fired":
        conn.execute("UPDATE ai_employees SET status='fired' WHERE id='AI-A'")
    elif mutation == "tenant_disabled":
        conn.execute("UPDATE tenants SET state='disabled' WHERE id='TENANT-HQ'")
    else:
        conn.execute("UPDATE task_runs SET actor_identity=? WHERE id='R-MCP'", (actor,))
        if mutation == "actor_revoked":
            conn.execute("UPDATE identities SET state='revoked' WHERE id=?", (actor,))
        elif mutation == "actor_foreign_tenant":
            conn.execute("UPDATE identities SET tenant_id='TENANT-B',legal_entity_id='LE-B' WHERE id=?",
                         (actor,))
        else:
            conn.execute("UPDATE identities SET legal_entity_id='LE-C' WHERE id=?", (actor,))
    conn.commit()
    result = _execute(runtime, "mcp-recorded-session")
    assert result["ok"] is False
    assert "McpAuthority" in result["extra"]["error"]
    assert calls == [("close",)], "denial must happen before process start or prompt"
    row = conn.execute("SELECT state,session_id,mcp_fingerprint FROM task_runs WHERE id='R-MCP'").fetchone()
    assert tuple(row) == ("spawned", "mcp-recorded-session", before)


def test_authority_change_during_session_initialize_is_denied_before_prompt(runtime):
    conn, actor, sid, calls, hooks, _ = runtime
    def revoke():
        mcp.set_bindings(conn, "AI-A", [], tenant_id="TENANT-HQ", actor=actor)
        conn.commit()
    hooks["session"] = revoke
    result = _execute(runtime)
    assert result["ok"] is False and "McpAuthority" in result["extra"]["error"]
    assert [call[0] for call in calls] == ["start", "new", "close"]


def test_resume_rejects_same_config_session_owned_by_another_task(runtime):
    conn, _, _, calls, _, _ = runtime
    _, digest = _parked(runtime)
    conn.execute("""INSERT INTO tasks(id,legal_entity_id,tenant_id,title)
                   VALUES ('T-FOREIGN','LE-GROUP-HQ','TENANT-HQ','other task')""")
    conn.execute("""INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,
                   session_id,mcp_fingerprint) VALUES (
                   'R-FOREIGN','T-FOREIGN',1,'LE-GROUP-HQ','TENANT-HQ','foreign-session',?)""",
                   (digest,))
    conn.commit()
    result = _execute(runtime, "foreign-session")
    assert result["ok"] is False
    assert calls == [("close",)]


def test_unknown_configured_session_cannot_be_resumed(runtime):
    result = _execute(runtime, "unknown-session")
    assert result["ok"] is False
    assert runtime[3] == [("close",)]


def test_echoed_mcp_credentials_are_redacted_before_logs_streams_and_traces(runtime):
    conn, _, _, _, hooks, logs = runtime
    trace.start(conn, run_id="R-MCP", task_id="T-MCP", agent_id="AI-A")
    hooks["echo"] = True
    result = _execute(runtime)
    assert result["ok"] is True
    persisted = "\n".join(path.read_text(encoding="utf-8") for path in logs.iterdir())
    persisted += json.dumps([dict(r) for r in conn.execute("SELECT * FROM run_stream")])
    persisted += json.dumps([dict(r) for r in conn.execute("SELECT * FROM trace_spans")])
    persisted += json.dumps(result)
    assert "runtime-private-key" not in persisted
    assert "[redacted]" in persisted
