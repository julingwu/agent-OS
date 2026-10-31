"""Approval text and side effects retain the task's cumulative tenant/entity scope."""
from __future__ import annotations

import pytest

from app import rbac, trace
from app.db import get_conn, init_db
from app.ui import approval_flow, store
from app.ui import approval_access
from fastapi import HTTPException


@pytest.fixture()
def approval_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "approval-scope.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    # Even a deployment configured with a global recipient must remain inbox-only.
    monkeypatch.setenv("WECOM_SECRET", "synthetic-global-secret")
    monkeypatch.setenv("WECOM_APPROVAL_APPROVER", "global-foreign-recipient")
    from app.wecom.client import WeComClient
    monkeypatch.setattr(WeComClient, "send_text",
                        lambda *a, **k: pytest.fail("No external notification is authorized"))
    init_db()
    with get_conn() as conn:
        store.ensure(conn)
        rbac.ensure(conn)
        trace.ensure(conn)
        conn.execute("INSERT INTO tenants(id,name) VALUES('TENANT-B','B workspace')")
        conn.execute("INSERT INTO legal_entities(id,name,tenant_id) VALUES('LE-B','B','TENANT-B')")
        conn.execute("""INSERT INTO legal_entities(id,name,tenant_id,parent_entity_id)
                        VALUES('LE-B-CHILD','B child','TENANT-B','LE-B')""")
        for suffix, tenant, entity in (("A", "TENANT-HQ", "LE-GROUP-HQ"),
                                        ("B", "TENANT-B", "LE-B"),
                                        ("C", "TENANT-B", "LE-B-CHILD")):
            conn.execute("""INSERT INTO ai_employees(id,name,legal_entity_id,status)
                            VALUES(?,?,?,'online')""", ("AI-" + suffix, suffix, entity))
            conn.execute("""INSERT INTO tasks(id,title,legal_entity_id,tenant_id,state,owner_employee_id)
                            VALUES(?,?,?,?,'in_progress',?)""",
                         ("T-" + suffix, "private-task-" + suffix, entity, tenant, "AI-" + suffix))
            conn.execute("""INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,
                            state,runner,session_id) VALUES(?,?,1,?,?,'awaiting_approval','acp',?)""",
                         ("R-" + suffix, "T-" + suffix, entity, tenant, "session-" + suffix))
        conn.execute("""INSERT INTO chats(id,title,kind,entity_id,tenant_id)
                        VALUES('C-HQ','Headquarters','ceo','LE-GROUP-HQ','TENANT-HQ')""")
        resource = tmp_path / "resource"
        resource.mkdir()
        conn.execute("INSERT INTO resources(id,name,kind,path) VALUES('RES-B','B resource','file',?)",
                     (str(resource),))
    return tmp_path, resource


def _approval(conn, resource, *, suffix="B", chat_id=None, session_id="session-B"):
    return rbac.guard_tool(
        conn, agent_id="AI-" + suffix, tool="write", force=True, push=False,
        args={"file_path": str(resource / "approved.txt"), "content": "private-payload-" + suffix},
        target=str(resource / "approved.txt"), task_id="T-" + suffix, run_id="R-" + suffix,
        resource_id="RES-B", chat_id=chat_id, session_id=session_id)["approval_id"]


def _counts(conn):
    return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                 for table in ("tool_approvals", "chat_messages", "ai_audit_logs"))


def _own_chat(conn):
    conn.execute("""INSERT INTO chats(id,title,kind,entity_id,tenant_id)
                    VALUES('C-B','B CEO','ceo','LE-B','TENANT-B')""")
    conn.commit()


def test_no_chat_keeps_approval_in_scoped_center_without_hq_fallback(approval_scope):
    _, resource = approval_scope
    with get_conn() as conn:
        approval_id = _approval(conn, resource)
        # A historical cross-linked message must not dictate notification routing.
        store.add_message(conn, "C-HQ", "human", "unrelated HQ text", task_id="T-B")
        before = _counts(conn)[1]
        approval_flow.post_card(conn, rbac.get(conn, approval_id))
        assert _counts(conn)[1] == before
    result = approval_flow.decide(approval_id, False, by="reviewer-B")
    assert result["state"] == "rejected"
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == before
        row = rbac.get(conn, approval_id)
        assert (row["tenant_id"], row["legal_entity_id"]) == ("TENANT-B", "LE-B")
        audit = conn.execute("SELECT legal_entity_id,action FROM ai_audit_logs WHERE target=?",
                             (approval_id,)).fetchall()
        assert {row["legal_entity_id"] for row in audit} == {"LE-B"}
        assert {"approval_requested", "approval_rejected", "approval_decided"} <= {
            row["action"] for row in audit}


def test_only_same_entity_fallback_receives_card_and_decision(approval_scope):
    _, resource = approval_scope
    with get_conn() as conn:
        _own_chat(conn)
        approval_id = _approval(conn, resource, session_id=None)
        row = rbac.get(conn, approval_id)
        approval_flow.post_card(conn, row)
        approval_flow.post_card(conn, row)
        assert conn.execute("SELECT COUNT(*) FROM chat_messages WHERE kind='approval'").fetchone()[0] == 1
        assert conn.execute("SELECT chat_id FROM chat_messages").fetchone()[0] == "C-B"
        assert conn.execute("SELECT COUNT(*) FROM chat_messages WHERE chat_id='C-HQ'").fetchone()[0] == 0
        child = _approval(conn, resource, suffix="C", session_id=None)
        approval_flow.post_card(conn, rbac.get(conn, child))
        # Parent CEO is not implicitly authorized to decide for a child entity.
        assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 1
    result = approval_flow.decide(approval_id, True, by="reviewer-B")
    assert result["state"] == "approved" and result["resumable"] is False
    with get_conn() as conn:
        assert {row[0] for row in conn.execute("SELECT chat_id FROM chat_messages")} == {"C-B"}
        assert conn.execute("SELECT COUNT(*) FROM chat_messages WHERE content LIKE '%审批通过%'").fetchone()[0] == 1


@pytest.mark.parametrize("chat_id", ["C-HQ", "C-missing", "C-mixed"])
def test_supplied_foreign_or_mixed_chat_rejected_before_approval_creation(approval_scope, chat_id):
    _, resource = approval_scope
    with get_conn() as conn:
        conn.execute("""INSERT INTO chats(id,title,kind,entity_id,tenant_id)
                        VALUES('C-mixed','Mixed','group','LE-B','TENANT-B')""")
        conn.execute("INSERT INTO chat_members(chat_id,employee_id) VALUES('C-mixed','AI-A')")
        conn.commit()
        before = _counts(conn)
        with pytest.raises(PermissionError, match="ApprovalScopeMismatch"):
            _approval(conn, resource, chat_id=chat_id)
        assert _counts(conn) == before
        assert not (resource / "approved.txt").exists()


@pytest.mark.parametrize("tamper", [
    "UPDATE tool_approvals SET chat_id='C-HQ'",
    "UPDATE tool_approvals SET tenant_id='TENANT-HQ'",
    "UPDATE tool_approvals SET legal_entity_id='LE-GROUP-HQ'",
    "UPDATE tool_approvals SET agent_id='AI-A'",
    "UPDATE tool_approvals SET run_id='R-A'",
    "UPDATE task_runs SET task_id='T-A',run_seq=2 WHERE id='R-B'",
    "UPDATE task_runs SET tenant_id='TENANT-HQ' WHERE id='R-B'",
    "UPDATE tasks SET tenant_id='TENANT-HQ' WHERE id='T-B'",
    "UPDATE ai_employees SET legal_entity_id='LE-B-CHILD' WHERE id='AI-B'",
    "UPDATE tasks SET owner_employee_id='missing-employee' WHERE id='T-B'",
])
def test_inconsistent_approval_links_block_notifications_decisions_and_execution(
        approval_scope, monkeypatch, tamper):
    _, resource = approval_scope
    with get_conn() as conn:
        approval_id = _approval(conn, resource)
        conn.execute(tamper)
        conn.commit()
        before = _counts(conn)
        with pytest.raises(PermissionError, match="ApprovalScopeMismatch"):
            approval_flow.post_card(conn, rbac.get(conn, approval_id))
        with pytest.raises(PermissionError, match="ApprovalScopeMismatch"):
            rbac.decide(conn, approval_id, True)
        assert _counts(conn) == before
        assert rbac.get(conn, approval_id)["state"] == "pending"
    with pytest.raises(PermissionError, match="ApprovalScopeMismatch"):
        approval_flow.decide(approval_id, True, by="reviewer-B")
    with get_conn() as conn:
        conn.execute("UPDATE tool_approvals SET state='approved'")
        conn.commit()
        with pytest.raises(PermissionError, match="ApprovalScopeMismatch"):
            rbac.apply_approval(conn, approval_id)
        with pytest.raises(PermissionError, match="ApprovalScopeMismatch"):
            rbac.consume(conn, approval_id)
        run_id = rbac.get(conn, approval_id)["run_id"]
        with pytest.raises(PermissionError, match="ApprovalScopeMismatch"):
            rbac.approved_for(conn, run_id, "RES-B", "write")
        assert _counts(conn) == before
    monkeypatch.setattr(approval_flow.dsh_adapter, "execute_run",
                        lambda *a, **k: pytest.fail("Inconsistent approval must never resume"))
    with pytest.raises(PermissionError, match="ApprovalScopeMismatch"):
        approval_flow.resume(approval_id)
    assert not (resource / "approved.txt").exists()


def test_scoped_apply_resume_and_notification_audits_remain_with_owner(approval_scope, monkeypatch):
    _, resource = approval_scope
    with get_conn() as conn:
        _own_chat(conn)
        approval_id = _approval(conn, resource, chat_id="C-B")
        rbac.decide(conn, approval_id, True, by="reviewer-B")
    calls = []

    def execute(conn, run_id, **kwargs):
        calls.append((run_id, kwargs))
        return {"exit_code": 0, "state": "done", "usage_tokens": 0}

    monkeypatch.setattr(approval_flow.dsh_adapter, "execute_run", execute)
    result = approval_flow.resume(approval_id)
    assert result["ok"] is True
    assert calls[0][0] == "R-B"
    assert calls[0][1]["resume_session"] == "session-B"
    assert (resource / "approved.txt").read_text(encoding="utf-8") == "private-payload-B"
    assert rbac.push_approval(approval_id)["channel"] == "inbox"
    with get_conn() as conn:
        audit = conn.execute("SELECT legal_entity_id,action FROM ai_audit_logs WHERE target=?",
                             (approval_id,)).fetchall()
        assert {row["legal_entity_id"] for row in audit} == {"LE-B"}
        assert {"approval_requested", "approval_approved", "approval_applied",
                "resume_started", "approval_pushed"} <= {row["action"] for row in audit}
        assert {row[0] for row in conn.execute("SELECT chat_id FROM chat_messages")} == {"C-B"}
        assert rbac.get(conn, approval_id)["push_result"] == "inbox-only"


def test_valid_scope_without_chat_can_resume_without_null_message(approval_scope, monkeypatch):
    _, resource = approval_scope
    with get_conn() as conn:
        approval_id = _approval(conn, resource)
        rbac.decide(conn, approval_id, True)
    monkeypatch.setattr(approval_flow.dsh_adapter, "execute_run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    assert approval_flow.resume(approval_id)["ok"] is False
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
        assert {row[0] for row in conn.execute(
            "SELECT legal_entity_id FROM ai_audit_logs WHERE target=?", (approval_id,))} == {"LE-B"}


def test_persisted_standalone_owner_visible_and_decidable_only_in_own_center(approval_scope):
    with get_conn() as conn:
        approval_id = rbac.guard_tool(conn, agent_id="legacy-agent-without-task",
                                     entity="LE-B", tool="file_delete", push=False)["approval_id"]
        own = {"role": "ceo", "tenant_id": "TENANT-B", "legal_entity_id": "LE-B"}
        foreign = {"role": "system", "tenant_id": "TENANT-HQ", "legal_entity_id": "LE-GROUP-HQ"}
        child = {"role": "ceo", "tenant_id": "TENANT-B", "legal_entity_id": "LE-B-CHILD"}
        result = approval_access.listing(conn, own)
        assert result["count"] == 1 and result["pending"][0]["id"] == approval_id
        approval_access.require_owned(conn, approval_id, own)
        for actor in (foreign, child):
            assert approval_access.listing(conn, actor) == {"pending": [], "history": [], "count": 0}
            with pytest.raises(HTTPException) as denied:
                approval_access.require_owned(conn, approval_id, actor)
            assert denied.value.status_code == 404
    assert approval_flow.decide(approval_id, False, by="reviewer-B")["state"] == "rejected"
    with get_conn() as conn:
        assert approval_access.listing(conn, own)["history"][0]["id"] == approval_id


def test_corrupt_approval_neither_listed_counted_nor_decidable(approval_scope):
    _, resource = approval_scope
    with get_conn() as conn:
        approval_id = _approval(conn, resource)
        conn.execute("UPDATE tool_approvals SET chat_id='C-HQ' WHERE id=?", (approval_id,))
        conn.commit()
        for role in ("ceo", "system"):
            actor = {"role": role, "tenant_id": "TENANT-B", "legal_entity_id": "LE-B"}
            assert approval_access.listing(conn, actor) == {"pending": [], "history": [], "count": 0}
            with pytest.raises(HTTPException) as denied:
                approval_access.require_owned(conn, approval_id, actor)
            assert denied.value.status_code == 404
