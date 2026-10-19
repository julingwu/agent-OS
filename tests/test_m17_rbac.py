"""M17：RBAC 资源权限 + 高危工具审批（v2.3）。"""
from __future__ import annotations

import pytest

from app import rbac
from app.db import get_conn, init_db


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "rbac.db"))
    init_db()
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type) VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
        c.commit()
        yield c


def _setup(conn):
    rbac.upsert_resource(conn, rid="RES-DEV", kind="department", name="研发部", path="dev/")
    rbac.upsert_resource(conn, rid="RES-FIN", kind="department", name="财务部", path="fin/")
    rbac.grant(conn, "AI-DEV", "RES-DEV", "write")
    rbac.grant(conn, "AI-FIN", "RES-FIN", "write")


def test_dev_can_write_own_resource(conn):
    _setup(conn)
    rbac.require(conn, "AI-DEV", "RES-DEV", "write")     # 不抛错即可


def test_dev_cannot_read_finance(conn):
    _setup(conn)
    with pytest.raises(PermissionError):
        rbac.require(conn, "AI-DEV", "RES-FIN", "read")
    row = conn.execute("SELECT action, target FROM ai_audit_logs"
                       " WHERE action='permission_denied' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row is not None and row["target"] == "RES-FIN"


def test_read_cannot_write(conn):
    _setup(conn)
    rbac.grant(conn, "AI-READ", "RES-DEV", "read")
    with pytest.raises(PermissionError):
        rbac.require(conn, "AI-READ", "RES-DEV", "write")


def test_high_risk_tool_needs_approval(conn):
    _setup(conn)
    out = rbac.guard_tool(conn, agent_id="AI-DEV", tool="db_exec",
                          args={"sql": "DELETE FROM budgets"})
    assert out["needs_approval"] is True
    assert out["approval_id"].startswith("AP-")
    assert len(rbac.pending(conn)) == 1


def test_approval_unblocks_and_is_audited(conn):
    _setup(conn)
    out = rbac.guard_tool(conn, agent_id="AI-DEV", tool="file_delete", args={"p": "/tmp/x"})
    rbac.decide(conn, out["approval_id"], True, by="ID-boss")
    assert rbac.pending(conn) == []
    row = conn.execute("SELECT action FROM ai_audit_logs"
                       " WHERE action='approval_approved' ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row is not None


def test_normal_tool_passes_without_approval(conn):
    _setup(conn)
    assert rbac.guard_tool(conn, agent_id="AI-DEV", tool="file_write")["needs_approval"] is False
