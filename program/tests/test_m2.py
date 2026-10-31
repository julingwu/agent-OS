"""M2 acceptance: tenant isolation, per-agent resources, F2 memory walls."""
from __future__ import annotations

import os
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "oa.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("M0_BUDGET_LIMIT_AMOUNT", "100000")
    (tmp_path / "workspace").mkdir()

    from app import identity
    from app.main import app

    with TestClient(app) as raw:
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.row_factory = sqlite3.Row

        for le in ("LE-A", "LE-B"):
            conn.execute("INSERT OR IGNORE INTO legal_entities (id, name) VALUES (?,?)",
                         (le, le))
        for pid, le, pack in (("P-A-CODE", "LE-A", "code-v1"),
                              ("P-A-SALES", "LE-A", "sales-v1"),
                              ("P-B-CODE", "LE-B", "code-v1")):
            conn.execute("INSERT OR IGNORE INTO positions (id, legal_entity_id, title, pack_id)"
                         " VALUES (?,?,?,?)", (pid, le, pid, pack))
        for eid, le, pid in (("E-A-CODE", "LE-A", "P-A-CODE"),
                             ("E-A-SALES", "LE-A", "P-A-SALES"),
                             ("E-B-CODE", "LE-B", "P-B-CODE")):
            conn.execute("INSERT OR IGNORE INTO ai_employees (id, legal_entity_id, name,"
                         " position_id) VALUES (?,?,?,?)", (eid, le, eid, pid))
        conn.commit()

        from tests.authclient import SignedClient

        def mk(role: str, entity: str, employee: str | None = None) -> SignedClient:
            ident = identity.issue(conn, name=f"{role}-{entity}-{employee}",
                                   role=role, legal_entity_id=entity,
                                   employee_id=employee)
            conn.commit()
            return SignedClient(raw, ident["id"], ident["secret"], role=role)

        yield {"raw": raw, "mk": mk, "conn": conn, "db": os.environ["DB_PATH"]}
        conn.close()


def make_task(client, text="写一个 Python 脚本抓取 https://example.com") -> str:
    r = client.post("/tasks/from_sentence", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


# --------------------------------------------------------------- M2-1 tasks
def test_m2_1_tasks_are_invisible_across_entities(env):
    a = env["mk"]("agent", "LE-A", "E-A-CODE")
    b = env["mk"]("agent", "LE-B", "E-B-CODE")
    tid = make_task(a)

    assert a.get(f"/tasks/{tid}").status_code == 200
    assert b.get(f"/tasks/{tid}").status_code == 404
    assert b.get(f"/tasks/{tid}/timeline").status_code == 404

    spawn = b.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1})
    assert spawn.status_code == 404, spawn.text

    # system may cross over to run ops, but only explicitly
    sysadmin = env["mk"]("system", "LE-A")
    assert sysadmin.get(f"/tasks/{tid}").status_code == 200


# --------------------------------------------------------------- M2-2 decisions
def test_m2_2_decisions_and_audit_are_entity_scoped(env):
    a = env["mk"]("agent", "LE-A", "E-A-CODE")
    b = env["mk"]("agent", "LE-B", "E-B-CODE")

    d = a.post("/decisions", json={"title": "投放", "amount": 1200.0}).json()
    assert b.get(f"/decisions/{d['id']}").status_code == 404
    assert b.post(f"/decisions/{d['id']}/evaluate").status_code == 404
    assert b.get("/decisions").json()["count"] == 0

    # a non-system identity cannot ask for another entity
    assert b.get("/decisions", params={"legal_entity_id": "LE-A"}).status_code == 403

    sysadmin = env["mk"]("system", "LE-A")
    listed = sysadmin.get("/decisions", params={"legal_entity_id": "LE-A"}).json()
    assert listed["count"] == 1


# --------------------------------------------------------------- M2-3 workspaces
def test_m2_3_each_agent_gets_its_own_workspace(env):
    a1 = env["mk"]("agent", "LE-A", "E-A-CODE")
    a2 = env["mk"]("agent", "LE-A", "E-A-SALES")

    dirs = []
    for agent in (a1, a2):
        tid = make_task(agent)
        run = agent.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()
        out = agent.post(f"/runs/{run['run_id']}/execute", json={"runner": "mock"}).json()
        path = out["artifacts"][0]["file_path"]
        dirs.append((agent.ident_id, tid, path))

    assert dirs[0][2] != dirs[1][2]
    for ident, tid, path in dirs:
        parts = path.replace("\\", "/").split("/")
        assert parts[-2] == tid, parts
        assert parts[-3] == ident, parts   # workspace/<agent>/<task>/file


# --------------------------------------------------------------- M2-4 budgets
def test_m2_4_budget_headroom_is_not_shared_between_agents(env):
    a1 = env["mk"]("agent", "LE-A", "E-A-CODE")
    a2 = env["mk"]("agent", "LE-A", "E-A-SALES")
    t1, t2 = make_task(a1), make_task(a2)

    r1 = a1.post("/verbs/spawn_run", json={"task_id": t1, "run_seq": 1}).json()
    r2 = a2.post("/verbs/spawn_run", json={"task_id": t2, "run_seq": 1}).json()

    con = sqlite3.connect(env["db"])
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT identity_id FROM budgets WHERE task_id IN (?,?)",
                       (t1, t2)).fetchall()
    assert {r["identity_id"] for r in rows} == {a1.ident_id, a2.ident_id}, [dict(r) for r in rows]

    # drain a1's headroom: a2 must still be able to run
    con.execute("UPDATE budgets SET limit_tokens=0 WHERE identity_id=?", (a1.ident_id,))
    con.commit()
    denied = a1.post(f"/runs/{r1['run_id']}/execute", json={"runner": "mock"}).json()
    assert denied["spawned"] is False
    allowed = a2.post(f"/runs/{r2['run_id']}/execute", json={"runner": "mock"}).json()
    assert allowed["spawned"] is True, allowed


# --------------------------------------------------------------- M2-5 memory
def test_m2_5_memory_walls(env):
    code_a = env["mk"]("agent", "LE-A", "E-A-CODE")
    sales_a = env["mk"]("agent", "LE-A", "E-A-SALES")
    code_b = env["mk"]("agent", "LE-B", "E-B-CODE")

    r = code_a.post("/memory", json={"scope": "entity:LE-A", "content": "A 线私有：报价底线"})
    assert r.status_code == 200, r.text
    private_id = r.json()["id"]
    code_a.post("/memory", json={"scope": "pack:code-v1", "content": "代码包私有：用 black"})
    code_a.post("/memory", json={"scope": "global", "content": "全局：所有产出要留证据"})

    # entity wall
    assert code_b.get(f"/memory/{private_id}").status_code == 404
    b_entries = code_b.get("/memory").json()["entries"]
    assert all("A 线私有" not in e["content"] for e in b_entries)
    assert any("全局" in e["content"] for e in b_entries)      # global is shared

    # pack wall inside the same entity
    sales_entries = sales_a.get("/memory").json()["entries"]
    assert any("A 线私有" in e["content"] for e in sales_entries)   # same entity: visible
    assert all("代码包私有" not in e["content"] for e in sales_entries)

    code_entries = code_a.get("/memory").json()["entries"]
    assert any("代码包私有" in e["content"] for e in code_entries)

    # writing into another entity's scope is refused
    bad = code_a.post("/memory", json={"scope": "entity:LE-B", "content": "越界"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "BadScope"


# --------------------------------------------------------------- M2-6 binding
def test_m2_6_memory_requires_an_employee(env):
    unbound = env["mk"]("agent", "LE-A")
    r = unbound.post("/memory", json={"scope": "global", "content": "x"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "NoEmployeeBound"
