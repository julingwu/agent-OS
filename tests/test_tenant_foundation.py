"""Tenant inheritance and upgrade behavior preserve existing transaction scope."""
from __future__ import annotations

import sqlite3

import pytest

from app import db, identity, rbac, tenant, tenancy
from app.api import tasks, verbs
from app.engine import task_generator
from app.models import AttachArtifactIn, RetryIn, SpawnRunIn
from app.ui import dispatcher, store


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tenant.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    db.init_db()
    with db.get_conn() as c:
        store.ensure(c)
        c.execute("INSERT INTO tenants (id,name) VALUES ('TENANT-BETA','Beta')")
        c.execute("""INSERT INTO legal_entities
            (id,name,type,parent_entity_id,tenant_id)
            VALUES ('LE-BETA','Beta','subsidiary','LE-GROUP-HQ','TENANT-BETA')""")
        c.execute("""INSERT INTO ai_employees (id,name,model,legal_entity_id)
            VALUES ('AI-BETA','Beta employee','mock','LE-BETA')""")
        c.commit()
        yield c


def test_creation_inherits_entity_and_rejects_wrong_tenant(conn):
    who = identity.issue(conn, name="Beta", role="manager", legal_entity_id="LE-BETA")
    assert tenancy.of_identity(conn, who["id"]) == "TENANT-BETA"
    tid = task_generator.generate(conn, "写一篇项目说明", "LE-BETA")["task_id"]
    assert conn.execute("SELECT tenant_id FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "TENANT-BETA"
    with pytest.raises(ValueError, match="TenantMismatch"):
        identity.issue(conn, name="wrong", role="manager", legal_entity_id="LE-BETA",
                       tenant_id="TENANT-HQ")
    with pytest.raises(ValueError, match="TenantMismatch"):
        task_generator.generate(conn, "wrong task", "LE-BETA", tenant_id="TENANT-HQ")


def test_context_helpers_do_not_commit_and_reject_revoked_identity(conn):
    who = identity.issue(conn, name="Beta", role="manager", legal_entity_id="LE-BETA")
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO settings (key,value) VALUES ('transaction-probe','uncommitted')")
    tenancy.ensure(conn)
    assert tenancy.resolve(conn, who["id"], "TENANT-BETA") == "TENANT-BETA"
    assert tenancy.of_entity(conn, "LE-BETA") == "TENANT-BETA"
    assert tenancy.name_of(conn, "TENANT-BETA") == "Beta"
    store.ensure(conn)
    rbac.ensure(conn)
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute("SELECT 1 FROM settings WHERE key='transaction-probe'").fetchone() is None
    with pytest.raises(PermissionError, match="TenantMismatch"):
        tenancy.resolve(conn, who["id"], "TENANT-HQ")
    identity.revoke(conn, who["id"])
    with pytest.raises(PermissionError, match="UnknownIdentity"):
        tenancy.resolve(conn, who["id"])


def test_task_run_artifact_retry_and_approval_keep_tenant(conn, tmp_path):
    who = identity.issue(conn, name="Beta", role="manager", legal_entity_id="LE-BETA")
    actor = dict(identity.get_identity(conn, who["id"]))
    tid = task_generator.generate(conn, "写一篇项目说明", "LE-BETA")["task_id"]
    run = verbs.do_spawn_run(conn, SpawnRunIn(task_id=tid, run_seq=1), ident=actor)
    rid = run["run_id"]
    assert conn.execute("SELECT tenant_id FROM task_runs WHERE id=?", (rid,)).fetchone()[0] == "TENANT-BETA"
    artifact = tmp_path / "article.md"
    artifact.write_text("项目说明", encoding="utf-8")
    art = verbs.do_attach_artifact(conn, AttachArtifactIn(run_id=rid, art_seq=1,
                                                        file_path=str(artifact)), ident=actor)
    assert conn.execute("SELECT tenant_id FROM artifacts WHERE id=?", (art["artifact_id"],)).fetchone()[0] == "TENANT-BETA"
    approval = rbac.guard_tool(conn, agent_id="AI-BETA", tool="shell_exec",
                               task_id=tid, run_id=rid, entity="LE-BETA", push=False)
    assert rbac.get(conn, approval["approval_id"])["tenant_id"] == "TENANT-BETA"
    conn.execute("UPDATE tasks SET state='failed' WHERE id=?", (tid,))
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    retry = tasks.prepare_retry(conn, tid, RetryIn(runner="mock"), actor)
    assert conn.in_transaction
    assert conn.execute("SELECT tenant_id FROM task_runs WHERE id=?", (retry["run_id"],)).fetchone()[0] == "TENANT-BETA"
    conn.rollback()
    assert conn.execute("SELECT 1 FROM task_runs WHERE id=?", (retry["run_id"],)).fetchone() is None


def test_entity_hierarchy_cannot_cross_tenant(conn):
    conn.execute("UPDATE legal_entities SET governance_mode='group_direct' WHERE id='LE-GROUP-HQ'")
    conn.execute("""INSERT INTO legal_entities (id,name,type,parent_entity_id)
        VALUES ('LE-HQ-CHILD','HQ child','subsidiary','LE-GROUP-HQ')""")
    who = identity.issue(conn, name="HQ", role="ceo")
    actor = dict(identity.get_identity(conn, who["id"]))
    assert set(tenant.visible_entities(conn, actor)) == {"LE-GROUP-HQ", "LE-HQ-CHILD"}


def test_chats_and_ceo_notifications_have_distinct_tenant_ownership(conn):
    hq = dispatcher.ensure_ceo_chat(conn)
    beta = dispatcher.ensure_ceo_chat(conn, tenant_id="TENANT-BETA", entity_id="LE-BETA")
    assert hq["id"] == "C-CEO" and beta["id"] != hq["id"]
    assert dispatcher.ensure_ceo_chat(conn, "TENANT-BETA", "LE-BETA")["id"] == beta["id"]
    chat = store.get_or_create_dm(conn, "AI-BETA", "Beta DM", "LE-BETA")
    group = store.create_group(conn, "Beta group", ["AI-BETA"])
    assert chat["tenant_id"] == group["tenant_id"] == "TENANT-BETA"
    assert {r["id"] for r in store.list_chats(conn, "TENANT-BETA")} == {beta["id"], chat["id"], group["id"]}
    with pytest.raises(ValueError, match="TenantMismatch"):
        store.create_group(conn, "foreign", ["AI-BETA"], tenant_id="TENANT-HQ")


def test_upgrade_backfills_once_and_preserves_existing_tenant_rows(conn):
    tid = task_generator.generate(conn, "保留的任务", "LE-BETA")["task_id"]
    run = verbs.do_spawn_run(conn, SpawnRunIn(task_id=tid, run_seq=1, legal_entity_id="LE-BETA"),
                             ident={"role": "system", "legal_entity_id": "LE-BETA"})
    conn.commit()
    # Simulate pre-tenant tables while retaining a populated parent entity.
    conn.execute("ALTER TABLE tasks DROP COLUMN tenant_id")
    conn.execute("ALTER TABLE task_runs DROP COLUMN tenant_id")
    conn.commit()
    db.init_db()
    assert conn.execute("SELECT tenant_id FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "TENANT-BETA"
    assert conn.execute("SELECT tenant_id FROM task_runs WHERE id=?", (run["run_id"],)).fetchone()[0] == "TENANT-BETA"
    # A subsequent startup must not rewrite ownership already assigned.
    conn.execute("UPDATE task_runs SET tenant_id='TENANT-HQ' WHERE id=?", (run["run_id"],))
    conn.commit()
    db.init_db()
    assert conn.execute("SELECT title FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "保留的任务"
    assert conn.execute("SELECT tenant_id FROM task_runs WHERE id=?", (run["run_id"],)).fetchone()[0] == "TENANT-HQ"


def test_old_approval_table_inherits_task_tenant(conn):
    tid = task_generator.generate(conn, "审批升级", "LE-BETA")["task_id"]
    rbac.ensure(conn)
    conn.execute("INSERT INTO tool_approvals (id,agent_id,tool,task_id) VALUES ('AP-OLD','AI-BETA','write',?)",
                 (tid,))
    conn.execute("ALTER TABLE tool_approvals DROP COLUMN tenant_id")
    conn.commit()
    rbac.ensure(conn)
    assert rbac.get(conn, "AP-OLD")["tenant_id"] == "TENANT-BETA"


def test_minimal_rbac_schema_can_be_initialized_without_implicit_commit():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    rbac.ensure(conn)
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='tool_approvals'").fetchone() is None
    conn.close()
