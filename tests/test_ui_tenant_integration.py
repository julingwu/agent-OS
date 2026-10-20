"""UI tenant isolation across routes, request bodies, and manual delivery."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import identity, pipeline, rbac
from app.db import get_conn
from app.engine import task_generator
from app.ui import api, roles, store
from tests.authclient import SignedClient
from tests.test_ui_task_actions import console, _submission


def _as(client, who):
    client.cookies.set("oa_ui", api._make_cookie(who["id"]))


@pytest.fixture()
def tenants(tmp_path, monkeypatch):
    for key, value in {
        "DB_PATH": str(tmp_path / "ui-tenants.db"),
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "UI_CRON": "0", "WECOM_WATCHER": "0", "WECOM_APPROVAL_POLLER": "0",
        "OCTO_RUNNER": "0", "WECOM_AUTORUN": "0", "M0_PIPELINE_EXEC": "0",
        "RUNNER": "mock", "UI_RUNNER": "mock", "M0_SANDBOX": "none",
        "M0_SANDBOX_ACP": "0", "M0_HEARTBEAT_SECONDS": "0",
    }.items():
        monkeypatch.setenv(key, value)
    from app.main import app
    with TestClient(app) as raw:
        with get_conn() as conn:
            store.ensure(conn)
            roles.ensure(conn)
            pipeline.ensure(conn)
            rbac.ensure(conn)
            conn.execute("INSERT INTO tenants (id,name) VALUES ('TENANT-BETA','Beta')")
            conn.execute("""INSERT INTO legal_entities (id,name,type,parent_entity_id,tenant_id)
                VALUES ('LE-BETA','Beta','subsidiary','LE-GROUP-HQ','TENANT-BETA')""")
            conn.execute("""INSERT INTO legal_entities (id,name,type,parent_entity_id,tenant_id)
                VALUES ('LE-HQ-SUB','HQ subsidiary','subsidiary','LE-GROUP-HQ','TENANT-HQ')""")
            conn.execute("UPDATE legal_entities SET governance_mode='group_direct' WHERE id='LE-GROUP-HQ'")
            hq = identity.issue(conn, name="HQ CEO", role="ceo")
            beta = identity.issue(conn, name="Beta CEO", role="ceo", legal_entity_id="LE-BETA")
            system = identity.issue(conn, name="HQ operator", role="system")
            sub = identity.issue(conn, name="Subsidiary CEO", role="ceo", legal_entity_id="LE-HQ-SUB")
            ids = {"hq": hq, "beta": beta, "system": system, "sub": sub}
            for label, entity, tenant_id in (("hq", "LE-GROUP-HQ", "TENANT-HQ"),
                                              ("beta", "LE-BETA", "TENANT-BETA")):
                eid = "AI-" + label.upper()
                conn.execute("""INSERT INTO ai_employees
                    (id,name,model,legal_entity_id,role_id,status)
                    VALUES (?,?,'mock',?,'ROLE-CONTENT','online')""", (eid, label, entity))
                ids[label + "_employee"] = eid
                chat = store.create_group(conn, label, [eid])
                conn.execute("UPDATE chats SET entity_id=? WHERE id=?", (entity, chat["id"]))
                store.add_message(conn, chat["id"], "human", label + " confidential")
                ids[label + "_chat"] = chat["id"]
                task = task_generator.generate(conn, label + " task", entity)["task_id"]
                ids[label + "_task"] = task
                rid = "RUN-" + label.upper()
                conn.execute("""INSERT INTO task_runs
                    (id,task_id,run_seq,legal_entity_id,tenant_id,state,runner)
                    VALUES (?,?,1,?,?,'succeeded','mock')""", (rid, task, entity, tenant_id))
                ids[label + "_run"] = rid
                art = tmp_path / "workspace" / (label + ".txt")
                art.parent.mkdir(exist_ok=True)
                art.write_text(label + " confidential", encoding="utf-8")
                aid = "ART-" + label.upper()
                conn.execute("""INSERT INTO artifacts
                    (id,run_id,tenant_id,art_seq,file_path,content,checksum,bytes)
                    VALUES (?,?,?,1,?,?, 'test',17)""",
                    (aid, rid, tenant_id, str(art), label + " confidential"))
                ids[label + "_artifact"] = aid
                project = pipeline.create_project(conn, name=label, requirement=label + " private",
                                                  entity=entity, tenant_id=tenant_id)
                ids[label + "_project"] = project["project_id"]
                approval = rbac.guard_tool(conn, agent_id=eid, tool="shell_exec",
                                           task_id=task, run_id=rid, entity=entity, push=False)
                ids[label + "_approval"] = approval["approval_id"]
            ids["beta_agent"] = identity.issue(conn, name="Beta writer", role="agent",
                                               legal_entity_id="LE-BETA",
                                               employee_id=ids["beta_employee"])
            conn.commit()
        _as(raw, hq)
        yield raw, ids


@pytest.mark.parametrize("actor", ["hq", "system"])
def test_ui_lists_never_cross_actor_tenant_even_for_system(tenants, actor):
    raw, ids = tenants
    _as(raw, ids[actor])
    assert {x["id"] for x in raw.get("/ui/tasks").json()["tasks"]} == {ids["hq_task"]}
    assert {x["id"] for x in raw.get("/ui/projects").json()["projects"]} == {ids["hq_project"]}
    assert ids["beta_employee"] not in {x["id"] for x in raw.get("/ui/employees").json()["employees"]}
    assert ids["beta_employee"] not in {x["id"] for x in raw.get("/ui/org").json()["employees"]}
    assert ids["beta_chat"] not in {x["id"] for x in raw.get("/ui/chats").json()["chats"]}
    assert ids["beta_artifact"] not in {x["id"] for x in raw.get("/ui/files").json()["files"]}
    assert ids["beta_approval"] not in {x["id"] for x in raw.get("/ui/approvals").json()["pending"]}


def test_other_tenant_can_login_reload_and_read_scoped_workspace(tenants):
    raw, ids = tenants
    raw.cookies.clear()
    response = raw.post("/ui/login", json={"identity_id": ids["beta"]["id"],
                                          "secret": ids["beta"]["secret"]})
    assert response.status_code == 200
    for _ in range(2):
        me = raw.get("/ui/me").json()
        assert me["identity_id"] == ids["beta"]["id"] and me["tenant_id"] == "TENANT-BETA"
        assert me["can_manage_deployment"] is False
        assert {x["id"] for x in raw.get("/ui/departments").json()["departments"]} == {"LE-BETA"}
        assert {x["id"] for x in raw.get("/ui/org").json()["roots"]} == {"LE-BETA"}
        assert {x["id"] for x in raw.get("/ui/tasks").json()["tasks"]} == {ids["beta_task"]}
        chats = raw.get("/ui/chats")
        assert chats.status_code == 200
        assert ids["beta_chat"] in {x["id"] for x in chats.json()["chats"]}
        assert ids["hq_chat"] not in {x["id"] for x in chats.json()["chats"]}
        assert raw.get("/ui/settings").status_code == 403
    assert raw.get("/ui/me").status_code == 200


@pytest.mark.parametrize("actor", ["sub", "beta", "beta_agent"])
def test_deployment_mutations_require_explicit_deployment_admin(tenants, actor):
    raw, ids = tenants
    _as(raw, ids[actor])
    assert raw.get("/ui/me").json()["can_manage_deployment"] is False
    requests = [
        ("put", "/ui/settings", {"theme": "unauthorized"}),
        ("post", "/ui/rbac/resources", {"id": "RES-DENIED", "kind": "api", "name": "denied"}),
        ("post", "/ui/resources/seed", {}),
        ("post", "/ui/roles", {"name": "denied", "tag": "dev", "spawn": False}),
        ("post", "/ui/departments", {"name": "denied"}),
        ("patch", "/ui/departments/LE-GROUP-HQ", {"name": "denied"}),
        ("delete", "/ui/departments/LE-HQ-SUB", None),
    ]
    for method, path, body in requests:
        response = raw.request(method, path, json=body)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["detail"]["code"] == "DeploymentAdminOnly"
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM settings WHERE value='unauthorized'").fetchone() is None
        assert conn.execute("SELECT 1 FROM resources WHERE id='RES-DENIED'").fetchone() is None
        assert conn.execute("SELECT 1 FROM legal_entities WHERE name='denied'").fetchone() is None


@pytest.mark.parametrize("actor", ["hq", "system"])
def test_deployment_admin_remains_available_but_cannot_change_foreign_departments(tenants, actor):
    raw, ids = tenants
    _as(raw, ids[actor])
    assert raw.get("/ui/me").json()["can_manage_deployment"] is True
    assert raw.put("/ui/settings", json={"theme": "light"}).status_code == 200
    assert raw.post("/ui/rbac/resources", json={
        "id": "RES-ADMIN", "kind": "api", "name": "allowed"}).status_code == 200
    assert "LE-BETA" not in {x["id"] for x in raw.get("/ui/departments").json()["departments"]}
    assert raw.post("/ui/departments", json={"name": "foreign", "entity_id": "LE-BETA"}).status_code == 404
    assert raw.patch("/ui/departments/LE-BETA", json={"name": "foreign"}).status_code == 404
    assert raw.delete("/ui/departments/LE-BETA").status_code == 404


def test_ceo_dispatch_persists_verified_initiator(tenants, monkeypatch):
    from app.ui import dispatcher
    raw, ids = tenants
    _as(raw, ids["beta"])
    monkeypatch.setenv("M0_DISPATCH_SYNC", "1")
    run_owners = []

    def run_project(conn, project_id, text):
        run_owners.append(conn.execute("SELECT owner_identity FROM projects WHERE id=?",
                                      (project_id,)).fetchone()[0])

    monkeypatch.setattr(pipeline, "run_project", run_project)
    with get_conn() as conn:
        chat = dispatcher.ensure_ceo_chat(conn, "TENANT-BETA", "LE-BETA")
    result = raw.post(f"/ui/chats/{chat['id']}/messages", json={"text": "开发一个计算器"})
    assert result.status_code == 200, result.text
    with get_conn() as conn:
        project = conn.execute("SELECT owner_identity,legal_entity_id,tenant_id FROM projects WHERE id=?",
                               (result.json()["project_id"],)).fetchone()
        assert tuple(project) == (ids["beta"]["id"], "LE-BETA", "TENANT-BETA")
    assert run_owners == [ids["beta"]["id"]]


@pytest.mark.parametrize("route,key", [
    ("/ui/tasks/{}", "beta_task"), ("/ui/projects/{}", "beta_project"),
    ("/ui/chats/{}/messages", "beta_chat"), ("/ui/live/{}", "beta_run"),
    ("/ui/runs/{}/trace", "beta_run"), ("/ui/files/{}/download", "beta_artifact"),
    ("/ui/files/{}/preview", "beta_artifact"),
])
def test_foreign_direct_ids_are_hidden(tenants, route, key):
    raw, ids = tenants
    assert raw.get(route.format(ids[key])).status_code == 404
    _as(raw, ids["system"])
    assert raw.get(route.format(ids[key])).status_code == 404


def test_body_ids_are_scoped_and_invitation_batch_is_atomic(tenants):
    raw, ids = tenants
    denied = raw.post(f"/ui/groups/{ids['hq_chat']}/members",
                      json={"employee_ids": [ids["hq_employee"], ids["beta_employee"]]})
    assert denied.status_code == 404
    with get_conn() as conn:
        assert conn.execute("SELECT 1 FROM chat_members WHERE chat_id=? AND employee_id=?",
                            (ids["hq_chat"], ids["beta_employee"])).fetchone() is None
    for route in ("grant", "revoke", "check"):
        result = raw.post("/ui/rbac/" + route, json={
            "agent_id": ids["beta_employee"], "resource_id": "RES-UNKNOWN", "permission": "write"})
        assert result.status_code == 404
    assert raw.post("/ui/rbac/guard", json={"agent_id": ids["beta_employee"],
                                          "tool": "shell_exec"}).status_code == 404
    _as(raw, ids["beta"])
    assert raw.post("/ui/rbac/grant", json={"agent_id": ids["beta_employee"],
        "resource_id": "RES-UNKNOWN", "permission": "write"}).status_code == 403


def test_project_chat_and_approval_mutations_preserve_foreign_records(tenants):
    raw, ids = tenants
    checks = [
        raw.patch(f"/ui/projects/{ids['beta_project']}", json={"use_llm_docs": 1}),
        raw.post(f"/ui/chats/{ids['beta_chat']}/messages", json={"text": "foreign mutation"}),
        raw.post(f"/ui/approvals/{ids['beta_approval']}", json={"approve": True}),
        raw.post(f"/ui/rbac/approvals/{ids['beta_approval']}", json={"approve": True}),
        raw.post(f"/ui/tasks/{ids['beta_task']}/action", json={"action": "abort"}),
    ]
    assert [x.status_code for x in checks] == [404] * len(checks)
    with get_conn() as conn:
        assert rbac.get(conn, ids["beta_approval"])["state"] == "pending"
        assert conn.execute("SELECT use_llm_docs FROM projects WHERE id=?",
                            (ids["beta_project"],)).fetchone()[0] == 0


def test_mismatched_headers_and_revoked_cookies_fail_before_queries(tenants):
    raw, ids = tenants
    for path in ("/ui/tasks", "/ui/chats", "/ui/employees", "/ui/projects", "/ui/files"):
        assert raw.get(path, headers={"X-Tenant-Id": "TENANT-BETA"}).status_code == 403
    with get_conn() as conn:
        identity.revoke(conn, ids["hq"]["id"])
    for path in ("/ui/tasks", "/ui/org", "/ui/chats", "/ui/projects", "/ui/account"):
        assert raw.get(path).status_code == 401


def test_signed_creation_and_role_assignment_inherit_beta_tenant(tenants):
    raw, ids = tenants
    _as(raw, ids["beta"])
    signed = SignedClient(raw, ids["beta"]["id"], ids["beta"]["secret"])
    created = signed.post("/tasks/from_sentence", json={"text": "租户内交付说明"}).json()
    requested = raw.post(f"/ui/groups/{ids['beta_chat']}/request_role",
                         json={"role_id": "ROLE-QA"})
    assert requested.status_code == 200, requested.text
    assigned = raw.post(f"/ui/groups/{ids['beta_chat']}/role_requests/{requested.json()['message_id']}/decide",
                        json={"approve": True})
    assert assigned.status_code == 200, assigned.text
    with get_conn() as conn:
        assert conn.execute("SELECT tenant_id FROM tasks WHERE id=?",
                            (created["task_id"],)).fetchone()[0] == "TENANT-BETA"
        row = conn.execute("SELECT legal_entity_id,tenant_id FROM identities WHERE employee_id=?",
                           (assigned.json()["employee_id"],)).fetchone()
        assert tuple(row) == ("LE-BETA", "TENANT-BETA")


def test_tool_diagnostic_uses_derived_role_and_cannot_execute_sql(tenants):
    raw, ids = tenants
    _as(raw, ids["beta_agent"])
    assert raw.post("/ui/tools/call", json={"role_tag": "data", "tool": "db_query",
        "sql": "SELECT * FROM identities"}).status_code == 403
    allowed = raw.post("/ui/tools/call", json={"role_tag": "content", "tool": "file_write"})
    assert allowed.status_code == 200 and allowed.json()["executed"] is False
    assert raw.patch(f"/ui/employees/{ids['beta_employee']}",
                     json={"model": "unauthorized"}).status_code == 403


def test_cold_direct_routes_return_not_found(console):
    raw, _, _ = console
    assert raw.get("/ui/projects/DOES-NOT-EXIST").status_code == 404
    assert raw.get("/ui/traces/spans/DOES-NOT-EXIST/audit").status_code == 404


def test_org_prompt_context_does_not_include_foreign_entities(tenants):
    from app.ui import intel
    with get_conn() as conn:
        beta = intel.org_digest(conn, "LE-BETA")
        assert "Beta" in beta and "集团总部" not in beta and "hq" not in beta
        assert "Beta" not in intel.org_digest(conn, "LE-GROUP-HQ")
        assert intel.org_digest(conn, None) == "【组织架构】"


def test_dm_project_selects_only_same_entity_and_inherits_group_scope(tenants, monkeypatch):
    _, ids = tenants
    with get_conn() as conn:
        conn.execute("""INSERT INTO ai_employees (id,name,model,legal_entity_id,status)
            VALUES ('AI-BETA-DEV','Beta developer','mock','LE-BETA','online')""")
        dm = store.get_or_create_dm(conn, ids["beta_employee"], "Beta PM", "LE-BETA")
        emp = conn.execute("SELECT * FROM ai_employees WHERE id=?", (ids["beta_employee"],)).fetchone()
    dispatched = []
    monkeypatch.setattr(api, "_start_project", lambda cid, text, ident, members, title:
                        dispatched.append((cid, ident, members)))
    api._start_project_from_dm(dm["id"], "UI重构项目", ids["beta"]["id"], emp)
    assert len(dispatched) == 1
    cid, initiator, members = dispatched[0]
    assert initiator == ids["beta"]["id"]
    assert members and all(x["legal_entity_id"] == "LE-BETA" for x in members)
    with get_conn() as conn:
        group = conn.execute("SELECT tenant_id,entity_id FROM chats WHERE id=?", (cid,)).fetchone()
        assert tuple(group) == ("TENANT-BETA", "LE-BETA")


def test_manual_accept_cannot_override_current_mandatory_redline(console, monkeypatch):
    from pathlib import Path
    from app import acceptance_run, experience
    raw, _, who = console
    tid, _, _ = _submission(console)
    with get_conn() as conn:
        path = conn.execute("""SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id=a.run_id
            WHERE r.task_id=? ORDER BY r.run_seq DESC,a.art_seq DESC LIMIT 1""", (tid,)).fetchone()[0]
        artifact = Path(path)
        artifact.write_text(artifact.read_text(encoding="utf-8") + "\n秋季", encoding="utf-8")
        rule = experience.add(conn, kind="decision", title="禁止测试词", content="不发布秋季",
                               mandatory=True, redline_terms=["秋季"], actor=who["id"])
        experience.review(conn, rule["id"], "approve", reviewer=who["id"])
    original = acceptance_run.validate_mandatory
    transaction_checks = []

    def checked(conn, *args, **kwargs):
        held = conn.in_transaction
        result = original(conn, *args, **kwargs)
        transaction_checks.append((kwargs.get("commit"), held, conn.in_transaction))
        return result

    monkeypatch.setattr(acceptance_run, "validate_mandatory", checked)
    result = raw.post(f"/ui/tasks/{tid}/action", json={"action": "accept"})
    assert result.status_code == 409 and result.json()["detail"]["code"] == "MandatoryCheckFailed"
    assert transaction_checks[0] == (False, True, True)
    with get_conn() as conn:
        assert conn.execute("SELECT state FROM tasks WHERE id=?", (tid,)).fetchone()[0] == "rejected"
        assert conn.execute("SELECT state FROM acceptance_criteria WHERE task_id=?"
                            " AND check_ref='redline'", (tid,)).fetchone()[0] == "failed"
