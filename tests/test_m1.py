"""M1 acceptance: signatures, identities, role binding, tool policy."""
from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from tests.authclient import SignedClient, body_of, signed_headers


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "oa.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_BUDGET_LIMIT_AMOUNT", "100000")
    (tmp_path / "workspace").mkdir()

    from app import identity
    from app.main import app

    with TestClient(app) as raw:
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.row_factory = sqlite3.Row

        def mk(role: str) -> SignedClient:
            ident = identity.issue(conn, name=f"{role}-01", role=role)
            conn.commit()
            return SignedClient(raw, ident["id"], ident["secret"], role=role)

        yield {"raw": raw, "mk": mk, "conn": conn, "db": os.environ["DB_PATH"]}
        conn.close()


def make_task(client) -> str:
    r = client.post("/tasks/from_sentence", json={"text": "写一篇 500 字秋季护肤短文"})
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


# --------------------------------------------------------------- M1-1 no signature
def test_m1_1_unsigned_request_is_rejected(env):
    r = env["raw"].post("/tasks/from_sentence", json={"text": "x"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "MissingSignature"


# --------------------------------------------------------------- M1-2 bad signature
def test_m1_2_forged_signature_is_rejected(env):
    agent = env["mk"]("agent")
    forger = SignedClient(env["raw"], agent.ident_id, "00" * 32, role="agent")
    r = forger.post("/tasks/from_sentence", json={"text": "x"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "BadSignature"


# --------------------------------------------------------------- M1-3 replay
def test_m1_3_replayed_nonce_is_rejected(env):
    agent = env["mk"]("agent")
    path = "/tasks/from_sentence"
    body = body_of({"text": "写一篇 500 字秋季护肤短文"})
    headers = signed_headers(agent.ident_id, agent.secret, "POST", path, None, body)

    first = env["raw"].post(path, content=body, headers=headers)
    second = env["raw"].post(path, content=body, headers=headers)
    assert first.status_code == 200, first.text
    assert second.status_code == 401
    assert second.json()["detail"]["code"] == "ReplayDetected"


# --------------------------------------------------------------- M1-4 expiry
def test_m1_4_stale_timestamp_is_rejected(env):
    agent = env["mk"]("agent")
    body = body_of({"text": "x"})
    headers = signed_headers(agent.ident_id, agent.secret, "POST", "/tasks/from_sentence",
                             None, body, ts=str(int(time.time()) - 1000))
    r = env["raw"].post("/tasks/from_sentence", content=body, headers=headers)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "SignatureExpired"


# --------------------------------------------------------------- M1-5 revocation
def test_m1_5_revoked_identity_cannot_sign(env):
    from app import identity

    agent = env["mk"]("agent")
    identity.revoke(env["conn"], agent.ident_id)
    env["conn"].commit()
    r = agent.post("/tasks/from_sentence", json={"text": "x"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "UnknownIdentity"


# --------------------------------------------------------------- M1-6 scopes
def test_m1_6_agent_cannot_judge_acceptance(env):
    agent = env["mk"]("agent")
    tid = make_task(agent)
    criteria = agent.get(f"/tasks/{tid}").json()["criteria"]
    r = agent.post(f"/criteria/{criteria[4]['id']}/check",
                   json={"passed": True, "evidence_snippet": "x"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "Forbidden"


# --------------------------------------------------------------- M1-7/8 CEO binding
def test_m1_7_ceo_packet_requires_ceo_and_binds_identity(env):
    system, manager, ceo = env["mk"]("system"), env["mk"]("manager"), env["mk"]("ceo")

    d = system.post("/decisions", json={"title": "大额采购", "amount": 60000.0}).json()
    routed = system.post(f"/decisions/{d['id']}/evaluate").json()
    assert routed["state"] == "escalated_ceo"

    denied = manager.post(f"/decisions/{d['id']}/decide", json={"approved": True})
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "NeedsCEO"

    ok = ceo.post(f"/decisions/{d['id']}/decide",
                  json={"approved": True, "decided_by": "someone-else", "note": "批"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["decided_by"] == ceo.ident_id, "decided_by must come from the signature"

    audit = [a for a in ok.json()["audit"] if a["verb"] == "decision_decided"][0]
    assert json.loads(audit["payload"])["role"] == "ceo"


# --------------------------------------------------------------- M1-9 manager scope
def test_m1_9_manager_handles_manager_packets(env):
    system, manager = env["mk"]("system"), env["mk"]("manager")
    d = system.post("/decisions", json={"title": "中额试验", "amount": 8000.0,
                                        "risk_level": "medium"}).json()
    assert system.post(f"/decisions/{d['id']}/evaluate").json()["state"] == "escalated_manager"
    r = manager.post(f"/decisions/{d['id']}/decide", json={"approved": True})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "approved"


# --------------------------------------------------------------- M1-10 policy unit
def test_m1_10_tool_policy_inside_vs_outside(tmp_path):
    from app import policy

    ws = tmp_path / "task"
    ws.mkdir()
    inside = {"title": "write", "rawInput": {"file_path": str(ws / "a.txt")}}
    outside = {"title": "write", "rawInput": {"file_path": str(tmp_path / "escape.txt")}}
    assert policy.evaluate(inside, ws)["action"] == "allow"
    assert policy.evaluate(outside, ws)["action"] == "deny"

    os.environ["M0_DENY_TOOLS"] = "bash"
    try:
        assert policy.evaluate({"title": "bash", "rawInput": {}}, ws)["action"] == "deny"
    finally:
        os.environ.pop("M0_DENY_TOOLS", None)


# --------------------------------------------------------------- M1-11 policy at runtime
def test_m1_11_acp_tool_call_outside_workspace_is_killed(env, monkeypatch):
    monkeypatch.setenv("MOCK_ACP_ESCAPE_TOOL", "1")
    agent = env["mk"]("agent")

    tid = make_task(agent)
    run = agent.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()
    out = agent.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock_acp"}).json()

    assert out["exit_code"] == 4, out
    assert out["policy_violation"]["action"] == "deny"
    assert "outside the task workspace" in out["policy_violation"]["reason"]

    con = sqlite3.connect(env["db"])
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM ai_audit_logs WHERE verb='tool_policy'").fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]["payload"])["action"] == "deny"

    actor_root = os.path.join(os.environ["M0_WORKSPACE"], agent.ident_id)
    assert not os.path.exists(os.path.join(os.environ["M0_WORKSPACE"], "escaped.txt"))
    assert not os.path.exists(os.path.join(actor_root, "escaped.txt"))
    assert os.path.isdir(os.path.join(actor_root, tid))


def test_policy_allows_relative_writes_but_not_escapes(tmp_path):
    from app import policy

    ws = tmp_path / "ws"
    ws.mkdir()
    def check(target: str) -> str:
        return policy.evaluate({"title": "write file", "rawInput": {"file_path": target}}, ws)["action"]

    assert check("article.md") == "allow"
    assert check("sub/a.md") == "allow"
    assert check("../escape.md") == "deny"
    assert check("C:/Windows/x.txt") == "deny"
