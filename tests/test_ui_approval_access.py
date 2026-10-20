"""Approval lists and decisions cannot cross UI identity or tenant boundaries."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import identity, rbac
from app.db import get_conn, init_db


@pytest.fixture()
def approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "approvals.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    init_db()
    from app.ui import api
    app = FastAPI()
    app.include_router(api.router)
    tokens = {}
    with get_conn() as conn:
        rbac.ensure(conn)
        for entity in ("A", "B"):
            conn.execute("INSERT INTO legal_entities(id,name,type,parent_entity_id)"
                         " VALUES(?,?,'subsidiary','LE-GROUP-HQ')", ("LE-" + entity, entity))
            conn.execute("INSERT INTO tasks(id,legal_entity_id,title,state) VALUES(?,?,?,'in_progress')",
                         ("T-" + entity, "LE-" + entity, entity))
            conn.execute("INSERT INTO ai_employees(id,legal_entity_id,name,model,status)"
                         " VALUES(?,?,?,'mock','online')", ("AI-" + entity, "LE-" + entity, entity))
            conn.execute("INSERT INTO tool_approvals(id,agent_id,task_id,tool,args) VALUES(?,?,?,'write',?)",
                         ("AP-" + entity, "AI-" + entity, "T-" + entity, '{"content":"private-' + entity + '"}'))
        conn.execute("INSERT INTO tool_approvals(id,agent_id,tool) VALUES('AP-no-task','AI-B','write')")
        conn.execute("INSERT INTO tool_approvals(id,agent_id,tool) VALUES('AP-orphan','missing-agent','write')")
        for name, role, entity in (("ceo", "ceo", "LE-A"), ("manager", "manager", "LE-A"),
                                   ("agent", "agent", "LE-A"), ("other", "ceo", "LE-B"),
                                   ("group", "ceo", "LE-GROUP-HQ"), ("system", "system", "LE-GROUP-HQ"),
                                   ("revoked", "ceo", "LE-A")):
            who = identity.issue(conn, name=name, role=role, legal_entity_id=entity)
            tokens[name] = api._make_cookie(who["id"])
            if name == "revoked":
                identity.revoke(conn, who["id"])
        tokens["unknown"] = api._make_cookie("ID-missing")
        tokens["forged"] = "invalid-ticket"
        conn.commit()

    def request(who, path="/ui/approvals", *, method="GET", mode="cookie"):
        with TestClient(app) as client:
            kwargs = {}
            token = tokens.get(who)
            if token:
                if mode == "cookie":
                    client.cookies.set("oa_ui", token)
                elif mode == "bearer":
                    kwargs["headers"] = {"Authorization": "Bearer " + token}
                else:
                    kwargs["params"] = {"t": token}
            if method == "POST":
                kwargs["json"] = {"approve": False}
            return client.request(method, path, **kwargs)
    return request


@pytest.mark.parametrize("path", ["/ui/approvals", "/ui/rbac/approvals"])
@pytest.mark.parametrize("mode", ["cookie", "bearer", "query"])
def test_lists_are_filtered_before_returning_private_fields(approvals, path, mode):
    response = approvals("ceo", path, mode=mode)
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert [a["id"] for a in response.json()["pending"]] == ["AP-A"]
    assert "private-A" in response.text
    assert "private-B" not in response.text
    assert {a["id"] for a in approvals("other", path).json()["pending"]} == {"AP-B", "AP-no-task"}
    # The tenant root owns its standalone legacy approval, but cannot approve
    # subsidiary requests just because it can read their task summaries.
    assert {a["id"] for a in approvals("group", path).json()["pending"]} == {"AP-orphan"}
    assert len(approvals("system", path).json()["pending"]) == 4


@pytest.mark.parametrize("prefix", ["/ui/approvals", "/ui/rbac/approvals"])
@pytest.mark.parametrize("mode", ["cookie", "bearer", "query"])
def test_only_own_approvers_can_decide_and_no_foreign_existence_leaks(approvals, prefix, mode):
    for who, suffix, expected in [("ceo", "AP-B", 404), ("ceo", "AP-no-task", 404),
                                   ("ceo", "AP-missing", 404), ("group", "AP-A", 404),
                                   ("agent", "AP-A", 403)]:
        assert approvals(who, prefix + "/" + suffix, method="POST", mode=mode).status_code == expected
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tool_approvals WHERE state<>'pending'").fetchone()[0] == 0
    result = approvals("manager", prefix + "/AP-A", method="POST", mode=mode)
    assert result.status_code == 200 and result.json()["state"] == "rejected"
    with get_conn() as conn:
        row = conn.execute("SELECT state,decided_by FROM tool_approvals WHERE id='AP-A'").fetchone()
        assert row["state"] == "rejected" and row["decided_by"]


@pytest.mark.parametrize("who", ["revoked", "unknown", "forged", "anonymous"])
def test_inactive_or_invalid_identity_cannot_list_or_decide(approvals, who):
    for prefix in ("/ui/approvals", "/ui/rbac/approvals"):
        assert approvals(who, prefix).status_code == 401
        assert approvals(who, prefix + "/AP-A", method="POST").status_code == 401


def test_read_only_identity_sees_no_approval_details(approvals):
    assert approvals("agent").json() == {"pending": [], "history": [], "count": 0}
    assert approvals("system", "/ui/approvals/AP-orphan", method="POST").status_code == 200
    assert approvals("other", "/ui/approvals/AP-no-task", method="POST").status_code == 200
