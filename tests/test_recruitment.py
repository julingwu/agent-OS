"""Unit and integration tests for HQ recruitment and staffing workflow."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import identity, recruitment
from app.db import get_conn
from app.main import app
from app.ui import api as ui_api, roles as roles_mod


@pytest.fixture
def auth_client():
    with TestClient(app) as client:
        with get_conn() as conn:
            roles_mod.ensure(conn)
            who = identity.issue(conn, name="test_ceo_rec", role="ceo", legal_entity_id="LE-GROUP-HQ")
        client.cookies.set("oa_ui", ui_api._make_cookie(who["id"]))
        yield client


def test_inspect_department_staffing():
    with get_conn() as conn:
        roles_mod.ensure(conn)
        staffing = recruitment.inspect_department_staffing(conn, "LE-GROUP-HQ")
        assert len(staffing) == 8
        for d in staffing:
            assert "dept_idx" in d
            assert "name" in d
            assert "headcount" in d
            assert "understaffed" in d
            assert "recommended_role" in d
            assert "recommended_title" in d
            assert 0 <= d["dept_idx"] <= 7


def test_recruitment_closed_loop_for_ops_department():
    with get_conn() as conn:
        roles_mod.ensure(conn)
        # 1. 模拟运营部（Dept 3）缺编状态：将运营部既有员工状态设为 fired
        conn.execute("""
            UPDATE ai_employees
            SET status = 'fired'
            WHERE legal_entity_id = 'LE-GROUP-HQ'
              AND (role_id = 'ROLE-OPS' OR name LIKE '%运维%' OR name LIKE '%运营%' OR name LIKE '%SRE%')
        """)
        conn.commit()

        # 巡检确认缺编
        staffing_before = recruitment.inspect_department_staffing(conn, "LE-GROUP-HQ")
        ops_dept_before = next(d for d in staffing_before if d["dept_idx"] == 3)
        assert ops_dept_before["understaffed"] is True
        assert ops_dept_before["headcount"] == 0

        # 2. 触发自动招聘调度闭环
        recruited = recruitment.trigger_auto_recruitment(conn, "LE-GROUP-HQ", "TENANT-HQ")
        assert len(recruited) >= 1
        ops_rec = next(r for r in recruited if r["dept_idx"] == 3)
        assert ops_rec["role_id"] == "ROLE-OPS"
        assert ops_rec["role_name"] == "运维/SRE"
        assert ops_rec["task_id"].startswith("T-RECRUIT-")
        assert "工位已点亮" in ops_rec["broadcast"]

        task_id = ops_rec["task_id"]
        new_emp_id = ops_rec["employee_id"]

        # 3. 验证 tasks 表：招聘任务真实存在且为 completed
        task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        assert task_row is not None
        assert task_row["state"] == "completed"
        assert task_row["stage"] == "recruitment"
        assert "运维/SRE" in task_row["title"]
        assert task_row["owner_employee_id"] is not None

        # 4. 验证 acceptance_criteria 表：3 项判据且全部通过
        criteria_rows = conn.execute(
            "SELECT * FROM acceptance_criteria WHERE task_id = ? ORDER BY seq", (task_id,)
        ).fetchall()
        assert len(criteria_rows) == 3
        for c in criteria_rows:
            assert c["state"] == "passed"
            assert c["checked_by"] == task_row["owner_employee_id"]
        assert "岗位需求画像与能力模型已确立" in criteria_rows[0]["criterion"]
        assert "候选人角色模板匹配与专业能力考评通过" in criteria_rows[1]["criterion"]
        assert "完成组织定岗与工位入驻" in criteria_rows[2]["criterion"]

        # 5. 验证 artifacts 表与产物文件：归档招聘评估报告
        art_rows = conn.execute(
            """SELECT a.* FROM artifacts a
               JOIN task_runs r ON r.id = a.run_id
               WHERE r.task_id = ?""", (task_id,)
        ).fetchall()
        assert len(art_rows) >= 1
        art = art_rows[0]
        assert "recruitment_d3_role_ops.md" in art["file_path"].lower()
        assert "岗位编制论证" in art["content"]
        assert "专业能力考评雷达" in art["content"]
        assert "录用批复与定岗决定" in art["content"]

        # 6. 验证 ai_employees：运营部在岗人数由 0 变为 1，工位点亮
        staffing_after = recruitment.inspect_department_staffing(conn, "LE-GROUP-HQ")
        ops_dept_after = next(d for d in staffing_after if d["dept_idx"] == 3)
        assert ops_dept_after["understaffed"] is False
        assert ops_dept_after["headcount"] >= 1
        emp_ids = [e["id"] for e in ops_dept_after["employees"]]
        assert new_emp_id in emp_ids

        # 7. 验证 ai_audit_logs 审计留痕
        audit_row = conn.execute(
            "SELECT * FROM ai_audit_logs WHERE verb = 'recruitment' AND task_id = ?", (task_id,)
        ).fetchone()
        assert audit_row is not None
        assert audit_row["action"] == "employee_hired"
        assert audit_row["target"] == new_emp_id


def test_hq_snapshot_endpoint_with_recruitment(auth_client):
    # 模拟运营部 0 人
    with get_conn() as conn:
        conn.execute("""
            UPDATE ai_employees
            SET status = 'fired'
            WHERE legal_entity_id = 'LE-GROUP-HQ'
              AND (role_id = 'ROLE-OPS' OR name LIKE '%运维%' OR name LIKE '%运营%' OR name LIKE '%SRE%')
        """)
        conn.commit()

    res = auth_client.get("/ui/hq/snapshot")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True

    # 验证获取员工前触发自动招聘：8 大部门均有员工在岗，运营部不为 0 人
    employees = data["employees"]
    dept_indices = {e["dept_idx"] for e in employees}
    assert 3 in dept_indices  # 运营部必须有人！
    for i in range(8):
        assert i in dept_indices, f"Dept {i} should have at least 1 employee"

    # 验证 feed 与 logs 中注入了人事部招聘入职动态广播
    feed = data["feed"]
    logs = data.get("logs") or feed
    assert len(feed) > 0
    rec_feed_items = [f for f in logs if f.get("kind") == "recruitment" or "【人事部】" in str(f.get("text", ""))]
    assert len(rec_feed_items) >= 1
    assert any("工位已点亮" in str(f.get("text", "")) for f in rec_feed_items)


def test_hq_auto_staff_api(auth_client):
    res = auth_client.post("/ui/hq/auto-staff", json={"entity_id": "LE-GROUP-HQ", "tenant_id": "TENANT-HQ"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True
    assert "staffing_before" in data
    assert "staffing_after" in data
    assert "recruited" in data


def test_recruitment_task_in_task_center(auth_client):
    # 触发招聘生成任务
    with get_conn() as conn:
        recruited = recruitment.trigger_auto_recruitment(conn, "LE-GROUP-HQ", "TENANT-HQ")

    # 任务中心列表查询
    list_res = auth_client.get("/ui/tasks?limit=50")
    assert list_res.status_code == 200, list_res.text
    tasks_data = list_res.json()
    rec_tasks = [t for t in tasks_data["tasks"] if t.get("stage") == "recruitment" or t["id"].startswith("T-RECRUIT-")]
    assert len(rec_tasks) >= 1

    sample_task_id = rec_tasks[0]["id"]

    # 任务详情视图查询
    detail_res = auth_client.get(f"/ui/tasks/{sample_task_id}")
    assert detail_res.status_code == 200, detail_res.text
    detail = detail_res.json()
    assert detail["task"]["id"] == sample_task_id
    assert detail["task"]["state"] == "completed"
    assert len(detail["criteria"]) == 3
    for crit in detail["criteria"]:
        assert crit["state"] == "passed"
    assert len(detail["artifacts"]) >= 1
