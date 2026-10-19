"""M5: group-level CEO reads down the hierarchy; writes stay pinned."""
from __future__ import annotations

import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.authclient import SignedClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "oa.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("RUNNER", "mock")
    (tmp_path / "workspace").mkdir()

    from app import identity
    from app.main import app

    with TestClient(app) as raw:
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.row_factory = sqlite3.Row
        conn.execute("""INSERT INTO legal_entities (id, name, type, parent_entity_id)
                        VALUES ('LE-SUB', '子公司', 'subsidiary', 'LE-GROUP-HQ')""")
        ceo = identity.issue(conn, name="ceo-m5", role="ceo", legal_entity_id="LE-GROUP-HQ")
        sub = identity.issue(conn, name="sub-m5", role="manager", legal_entity_id="LE-SUB")
        conn.commit()
        yield {"raw": raw,
               "ceo": SignedClient(raw, ceo["id"], ceo["secret"]),
               "sub": SignedClient(raw, sub["id"], sub["secret"])}
        conn.close()


def test_m5_1_ceo_reads_down_but_cannot_write_into_a_subsidiary(env):
    sub, ceo = env["sub"], env["ceo"]
    t = sub.post("/tasks/from_sentence", json={"text": "写一篇 400 字短文"}).json()

    assert sub.get(f"/tasks/{t['task_id']}").status_code == 200
    assert ceo.get(f"/tasks/{t['task_id']}").status_code == 200        # reads down
    assert ceo.get("/tasks").json()["total"] == 1

    # but the CEO cannot create inside the subsidiary
    assert ceo.post("/tasks/from_sentence",
                    json={"text": "写一篇 300 字短文", "legal_entity_id": "LE-SUB"}
                    ).status_code == 403


def test_m5_2_report_covers_the_whole_group_for_the_ceo(env):
    sub, ceo = env["sub"], env["ceo"]
    t = sub.post("/tasks/from_sentence", json={"text": "写一篇 500 字短文"}).json()
    run = sub.post("/verbs/spawn_run", json={"task_id": t["task_id"], "run_seq": 1}).json()
    sub.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock"})

    rep = ceo.get("/reports/usage").json()
    assert "LE-SUB" in rep["entities"]
    assert rep["totals"]["tasks"] == 1
    assert rep["totals"]["tokens"] > 0


def test_m5_3_agent_still_cannot_see_its_sibling(env):
    from app.main import app  # noqa: F401  (ensures app import path is set)

    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.row_factory = sqlite3.Row
    from app import identity
    other = identity.issue(conn, name="hq-m5", role="manager", legal_entity_id="LE-GROUP-HQ")
    conn.commit()
    hq = SignedClient(env["raw"], other["id"], other["secret"])

    t = env["sub"].post("/tasks/from_sentence", json={"text": "写一篇 400 字短文"}).json()
    assert hq.get(f"/tasks/{t['task_id']}").status_code == 404
    assert hq.get("/tasks").json()["total"] == 0
    conn.close()


def test_m5_4_money_budget_is_not_a_token_pool(env):
    """A budget row with limit_amount but limit_tokens=0 must not zero the task budget."""
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.execute("""INSERT INTO budgets (id, legal_entity_id, identity_id, task_id,
                                         limit_tokens, limit_amount, state)
                    VALUES ('B-money', 'LE-GROUP-HQ', NULL, NULL, 0, 100000, 'active')""")
    conn.commit()

    sub = env["sub"]
    t = sub.post("/tasks/from_sentence", json={"text": "写一篇 400 字短文"}).json()
    run = sub.post("/verbs/spawn_run", json={"task_id": t["task_id"], "run_seq": 1}).json()
    out = sub.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock"}).json()
    assert out.get("task_state") != "failed", out
    conn.close()


def test_m5_5_spawn_on_a_finished_task_is_a_400(env):
    sub = env["sub"]
    t = sub.post("/tasks/from_sentence", json={"text": "写一篇 400 字短文"}).json()
    assert sub.post("/verbs/spawn_run", json={"task_id": t["task_id"], "run_seq": 1}).status_code == 200
    # same run_seq is idempotent; a *new* run on a completed task must be refused cleanly
    from app.state_machine import transition
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.row_factory = sqlite3.Row
    run = sub.post("/verbs/spawn_run", json={"task_id": t["task_id"], "run_seq": 1}).json()
    sub.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock"})
    transition(conn, t["task_id"], "review_passed", actor="test")
    conn.commit()
    r = sub.post("/verbs/spawn_run", json={"task_id": t["task_id"], "run_seq": 2})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "IllegalStateTransition"
    conn.close()
