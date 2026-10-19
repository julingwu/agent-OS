"""M3 acceptance: task list/scheduling, run locks, two-agent relay."""
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
    monkeypatch.setenv("M0_BUDGET_LIMIT_AMOUNT", "100000")
    monkeypatch.setenv("RUNNER", "mock")
    (tmp_path / "workspace").mkdir()

    from app import identity
    from app.main import app

    with TestClient(app) as raw:
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.row_factory = sqlite3.Row
        for le in ("LE-A", "LE-B"):
            conn.execute("INSERT OR IGNORE INTO legal_entities (id, name) VALUES (?,?)", (le, le))
        for pid, le, pack in (("P-C", "LE-A", "code-v1"), ("P-R", "LE-A", "review-v1"),
                              ("P-B", "LE-B", "code-v1")):
            conn.execute("INSERT OR IGNORE INTO positions (id, legal_entity_id, title, pack_id)"
                         " VALUES (?,?,?,?)", (pid, le, pid, pack))
        for eid, le, pid in (("E-CODER", "LE-A", "P-C"), ("E-REVIEWER", "LE-A", "P-R"),
                             ("E-B", "LE-B", "P-B")):
            conn.execute("INSERT OR IGNORE INTO ai_employees (id, legal_entity_id, name,"
                         " position_id) VALUES (?,?,?,?)", (eid, le, eid, pid))
        conn.commit()

        def mk(role, entity, employee):
            ident = identity.issue(conn, name=f"{role}-{employee}", role=role,
                                   legal_entity_id=entity, employee_id=employee)
            conn.commit()
            return SignedClient(raw, ident["id"], ident["secret"], role=role)

        yield {"raw": raw, "mk": mk, "conn": conn, "db": os.environ["DB_PATH"]}
        conn.close()


def make_task(client, text="写一个 Python 脚本抓取 https://example.com") -> str:
    r = client.post("/tasks/from_sentence", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


# --------------------------------------------------------------- M3-1 list
def test_m3_1_task_list_is_scoped_and_filterable(env):
    a = env["mk"]("agent", "LE-A", "E-CODER")
    b = env["mk"]("agent", "LE-B", "E-B")

    ids = [make_task(a) for _ in range(3)]
    make_task(b)

    listed = a.get("/tasks").json()
    assert listed["total"] == 3
    assert set(listed["counts"]) == {"todo"}
    assert b.get("/tasks").json()["total"] == 1

    tid = ids[0]
    run = a.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()
    a.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock"})

    mine = a.get("/tasks", params={"actor": a.ident_id}).json()
    assert mine["total"] == 1 and mine["tasks"][0]["id"] == tid
    assert a.get("/tasks", params={"state": "pending_review"}).json()["total"] == 1
    assert a.get("/tasks", params={"state": "todo"}).json()["total"] == 2


# --------------------------------------------------------------- M3-2 locks
def test_m3_2_a_held_lock_blocks_the_second_run(env):
    a = env["mk"]("agent", "LE-A", "E-CODER")
    tid = make_task(a)
    run = a.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()

    con = sqlite3.connect(env["db"])
    con.execute("INSERT INTO run_locks (resource, run_id, acquired_at, expires_at)"
                " VALUES (?,?,datetime('now'),datetime('now','+30 minutes'))",
                (f"task:{tid}", "RUN-someone-else"))
    con.commit()

    blocked = a.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock"})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["detail"]["code"] == "TaskLocked"
    assert blocked.json()["detail"]["holder"] == "RUN-someone-else"

    con.execute("DELETE FROM run_locks WHERE resource = ?", (f"task:{tid}",))
    con.commit()

    ok = a.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock"}).json()
    assert ok["spawned"] is True
    left = con.execute("SELECT COUNT(*) FROM run_locks").fetchone()[0]
    assert left == 0, "locks must be released when the run ends"


# --------------------------------------------------------------- M3-3 relay
def test_m3_3_two_agent_relay(env):
    manager = env["mk"]("manager", "LE-A", "E-CODER")
    coder = env["mk"]("agent", "LE-A", "E-CODER")
    reviewer = env["mk"]("agent", "LE-A", "E-REVIEWER")

    out = manager.post("/relay/run", json={
        "text": "写一个 Python 脚本抓取 https://example.com 并保存为 output.txt",
        "coder_identity_id": coder.ident_id,
        "reviewer_identity_id": reviewer.ident_id,
        "runner": "mock"}).json()

    assert out["ok"] is True, out
    assert out["coder"]["task_id"] != out["reviewer"]["task_id"]
    assert out["coder"]["artifact"] and out["reviewer"]["artifact"]
    assert out["review_conclusion"] == "通过", out

    # the artifact was copied into the reviewer's own directory, not read across
    copied = out["copied_to_reviewer"].replace("\\", "/")
    assert reviewer.ident_id in copied and "input.py" in copied
    assert os.path.exists(out["copied_to_reviewer"])

    # two different workspaces, two different sessions
    assert out["coder"]["session"] != out["reviewer"]["session"]

    con = sqlite3.connect(env["db"])
    assert con.execute("SELECT COUNT(*) FROM run_locks").fetchone()[0] == 0

    # both runs are attributed to their own identity
    actors = [r[0] for r in con.execute(
        "SELECT actor_identity FROM task_runs ORDER BY rowid")]
    assert actors == [coder.ident_id, reviewer.ident_id]


# --------------------------------------------------------------- M3-4 relay guards
def test_m3_4_relay_rejects_bad_pairing(env):
    manager = env["mk"]("manager", "LE-A", "E-CODER")
    coder = env["mk"]("agent", "LE-A", "E-CODER")
    outsider = env["mk"]("agent", "LE-B", "E-B")

    same = manager.post("/relay/run", json={
        "text": "写脚本", "coder_identity_id": coder.ident_id,
        "reviewer_identity_id": coder.ident_id}).json()
    assert "different identities" in same["detail"]["message"]

    cross = manager.post("/relay/run", json={
        "text": "写脚本", "coder_identity_id": coder.ident_id,
        "reviewer_identity_id": outsider.ident_id}).json()
    assert "not an active member" in cross["detail"]["message"]
