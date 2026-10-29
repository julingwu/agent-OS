"""Tests for HQ 3D Engine & Telemetry Snapshot."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import identity
from app.db import get_conn
from app.main import app
from app.ui import api as ui_api, roles as roles_mod


@pytest.fixture
def auth_client():
    with TestClient(app) as client:
        with get_conn() as conn:
            roles_mod.ensure(conn)
            # Create CEO identity
            who = identity.issue(conn, name="test_ceo", role="ceo", legal_entity_id="LE-GROUP-HQ")
            # Instantiate a few roles across departments
            role_dev = conn.execute("SELECT id FROM role_templates WHERE tag='dev' LIMIT 1").fetchone()
            role_qa = conn.execute("SELECT id FROM role_templates WHERE tag='qa' LIMIT 1").fetchone()
            role_pm = conn.execute("SELECT id FROM role_templates WHERE tag='pm' OR name LIKE '%产品%' LIMIT 1").fetchone()
            role_design = conn.execute("SELECT id FROM role_templates WHERE tag='design' LIMIT 1").fetchone()

            for r in (role_dev, role_qa, role_pm, role_design):
                if r:
                    roles_mod.instantiate(conn, r["id"], "LE-GROUP-HQ")

        client.cookies.set("oa_ui", ui_api._make_cookie(who["id"]))
        yield client


def test_hq_snapshot_endpoint(auth_client):
    res = auth_client.get("/ui/hq/snapshot")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True

    # 1. Employees check
    employees = data["employees"]
    assert isinstance(employees, list)
    assert len(employees) >= 4
    dept_indices = {e["dept_idx"] for e in employees}
    assert len(dept_indices) >= 2
    for emp in employees:
        assert "id" in emp
        assert "name" in emp
        assert "title" in emp
        assert "dept_idx" in emp
        assert 0 <= emp["dept_idx"] <= 7
        assert "busy" in emp
        assert "done_count" in emp
        assert "tokens" in emp
        assert "skills" in emp

    # 2. CEO Chat
    ceo_chat = data["ceo_chat"]
    assert "id" in ceo_chat
    assert "title" in ceo_chat

    # 3. Roles Library
    roles = data["roles"]
    assert isinstance(roles, list)
    assert len(roles) >= 10
    first_role = roles[0]
    assert "id" in first_role
    assert "name" in first_role
    assert "role_tag" in first_role
    assert 0 <= first_role["dept_idx"] <= 7

    # 4. Projects
    projects = data["projects"]
    assert isinstance(projects, list)

    # 5. Standalone Tasks
    standalone_tasks = data["standalone_tasks"]
    assert isinstance(standalone_tasks, list)

    # 6. KPIs
    kpis = data["kpis"]
    assert "progress_pct" in kpis
    assert "active_projects_count" in kpis
    assert "total_projects_count" in kpis
    assert "done_count" in kpis
    assert "wip_count" in kpis
    assert "pending_approvals" in kpis
    assert "avg_lead_min" in kpis
    assert "rework_rate_pct" in kpis
    assert "today_tokens" in kpis
    assert "total_rmb" in kpis
    assert "artifacts_count" in kpis
    assert "crystals_count" in kpis
    assert "eval_sets_count" in kpis
    assert "mcp_count" in kpis

    # 7. Feed
    feed = data["feed"]
    assert isinstance(feed, list)


def test_hq_hire_and_fire_linkage(auth_client):
    # 1. Snapshot before hire
    snap1 = auth_client.get("/ui/hq/snapshot").json()
    emp_count1 = len(snap1["employees"])

    # 2. Hire a role via /ui/hq/hire (pick a role that is not yet instantiated)
    instantiated_names = {e["name"] for e in snap1["employees"]}
    available_roles = [r for r in snap1["roles"] if r["name"] not in instantiated_names]
    assert len(available_roles) > 0
    role_to_hire = available_roles[0]

    hire_res = auth_client.post("/ui/hq/hire", json={"role_id": role_to_hire["id"]})
    assert hire_res.status_code == 200, hire_res.text
    hired = hire_res.json()
    assert hired["ok"] is True
    emp_id = hired["employee_id"]

    # 3. Snapshot after hire
    snap2 = auth_client.get("/ui/hq/snapshot").json()
    hired_emp = next((e for e in snap2["employees"] if e["id"] == emp_id), None)
    assert hired_emp is not None
    assert hired_emp["name"] == hired["name"]
    assert len(snap2["employees"]) == emp_count1 + 1

    # 4. Fire the employee via /ui/employees/{id}/fire
    fire_res = auth_client.post(f"/ui/employees/{emp_id}/fire")
    assert fire_res.status_code == 200, fire_res.text

    # 5. Snapshot after fire: employee should not be active
    snap3 = auth_client.get("/ui/hq/snapshot").json()
    fired_emp = next((e for e in snap3["employees"] if e["id"] == emp_id), None)
    assert fired_emp is None
    assert len(snap3["employees"]) == emp_count1


def test_hq_project_creation_linkage(auth_client):
    # 1. Create a project via /ui/projects
    proj_res = auth_client.post("/ui/projects", json={
        "name": "高并发计数器项目",
        "requirement": "开发一个高并发带单元测试的计数器服务",
        "flow": "FLOW-SOFTWARE"
    })
    assert proj_res.status_code == 200, proj_res.text
    proj_id = proj_res.json()["project_id"]

    # 2. Verify it shows up in /ui/hq/snapshot with 9 stages
    snap = auth_client.get("/ui/hq/snapshot").json()
    p = next((x for x in snap["projects"] if x["id"] == proj_id), None)
    assert p is not None
    assert p["name"] == "高并发计数器项目"
    assert len(p["stages"]) == 9
    assert p["stages"][0]["seq"] == 0
    assert p["stages"][0]["key"] == "intake"
