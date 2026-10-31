"""Unit and integration tests for workflow-adaptive staffing and role synthesis."""
from __future__ import annotations

import json
import pathlib
import pytest

from app import artifacts as art_mod, identity, pipeline, recruitment
from app.db import get_conn
from app.engine import dsh_adapter
from app.ui import dispatcher, roles as roles_mod, store


@pytest.fixture
def env():
    with get_conn() as conn:
        store.ensure(conn)
        roles_mod.ensure(conn)
        who = identity.issue(conn, name="test_ceo_adaptive", role="ceo", legal_entity_id="LE-GROUP-HQ")
        yield conn, who


def test_synthesize_role_template_presets_and_custom(env):
    conn, _ = env

    # 1. 预设角色量化分析师（quant）
    role_quant = recruitment.synthesize_role_template(
        conn, role_tag="quant", requirement="构建高频量化Alpha多因子回测与执行系统"
    )
    assert role_quant["name"] == "量化分析师"
    assert role_quant["tag"] == "quant"
    assert "【角色定位】" in role_quant["prompt"]
    assert "【职责边界】" in role_quant["prompt"]
    assert "【质量要求】" in role_quant["prompt"]
    assert role_quant["tools"] == "file_write,web_fetch,db_query"
    assert role_quant["deliverable"] == "quant_deliverable.md"
    assert role_quant["state"] == "active"

    # 持久化确认
    db_quant = conn.execute("SELECT * FROM role_templates WHERE id=?", (role_quant["id"],)).fetchone()
    assert db_quant is not None
    assert db_quant["name"] == "量化分析师"

    # 2. 预设角色TikTok运营专家（tiktok）
    role_tiktok = recruitment.synthesize_role_template(
        conn, role_tag="tiktok", requirement="打造TikTok海外短视频流量变现矩阵"
    )
    assert role_tiktok["name"] == "TikTok运营专家"
    assert role_tiktok["tag"] == "tiktok"
    assert role_tiktok["deliverable"] == "tiktok_deliverable.md"

    # 3. 未知自定义英文角色标签（aerospace -> AEROSPACE专家）
    role_aero = recruitment.synthesize_role_template(
        conn, role_tag="aerospace", requirement="航天级姿态控制系统架构"
    )
    assert role_aero["name"] == "AEROSPACE专家"
    assert role_aero["tag"] == "aerospace"
    assert role_aero["deliverable"] == "aerospace_deliverable.md"
    assert role_aero["tools"] == "file_write,web_fetch,db_query"

    # 4. 未知自定义中文角色标签（芯片测试 -> 芯片测试专家）
    role_chip = recruitment.synthesize_role_template(
        conn, role_tag="芯片测试", requirement="车规级SoC回流焊热应力测试"
    )
    assert role_chip["name"] == "芯片测试专家"
    assert role_chip["tag"] == "芯片测试"
    assert role_chip["deliverable"] == "芯片测试_deliverable.md"


def test_ensure_workflow_staff_hiring_and_idempotence(env):
    conn, _ = env

    # 清理可能既有的 quant 员工
    conn.execute("""
        UPDATE ai_employees SET status='fired'
        WHERE role_id IN (SELECT id FROM role_templates WHERE tag='quant')
    """)
    conn.commit()

    # 1. 首次触发定向即时招聘
    emp = recruitment.ensure_workflow_staff(
        conn,
        role_tag="quant",
        requirement="多因子量化选股策略研发",
        entity_id="LE-GROUP-HQ",
        tenant_id="TENANT-HQ",
    )
    assert emp["id"].startswith("AI-")
    assert emp["name"] == "量化分析师"
    assert emp["tag"] == "quant"

    # 验证 tasks 表与 acceptance_criteria
    task_id = emp["task_id"]
    trow = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert trow is not None
    assert trow["state"] == "completed"
    assert trow["stage"] == "recruitment"
    assert "量化分析师" in trow["title"]

    crits = conn.execute("SELECT * FROM acceptance_criteria WHERE task_id=? ORDER BY seq", (task_id,)).fetchall()
    assert len(crits) == 3
    for c in crits:
        assert c["state"] == "passed"
        assert c["checked_by"] == trow["owner_employee_id"]

    # 验证 artifacts 归档
    art_row = conn.execute("SELECT * FROM artifacts WHERE id=?", (emp["artifact_id"],)).fetchone()
    assert art_row is not None
    assert "workflow_recruitment_quant_" in art_row["file_path"].lower()
    assert "业务工作流工序画像与能力对齐" in art_row["content"]
    assert "候选人角色测评与能力雷达" in art_row["content"]

    # 验证审计日志
    audit_row = conn.execute(
        "SELECT * FROM ai_audit_logs WHERE verb='recruitment' AND action='workflow_adaptive_hired' AND task_id=?",
        (task_id,),
    ).fetchone()
    assert audit_row is not None
    assert audit_row["target"] == emp["id"]

    # 2. 幂等性测试：再次调用直接返回已有在岗员工，不再重复招募
    emp_second = recruitment.ensure_workflow_staff(
        conn, role_tag="quant", entity_id="LE-GROUP-HQ"
    )
    assert emp_second["id"] == emp["id"]
    assert emp_second["name"] == emp["name"]


def test_pipeline_adaptive_staffing_closed_loop(env, monkeypatch):
    conn, who = env
    pid = "P-ADAPTIVE-" + pathlib.Path().stem

    # 创建项目与阶段
    proj = pipeline.create_project(
        conn,
        name="TikTok海外投放闭环工程",
        requirement="搭建TikTok海外短视频流量引流到独立站并完成量化转化漏斗分析",
        entity="LE-GROUP-HQ",
        owner=who["id"],
        tenant_id="TENANT-HQ",
    )
    pid = proj["project_id"]

    # 创建项目专属协作群
    hr_emp_id, _ = recruitment._get_or_create_hr_recruiter(conn, "LE-GROUP-HQ", "TENANT-HQ")
    p_chat = store.create_group(conn, "TikTok海外投放闭环工程", [hr_emp_id], tenant_id="TENANT-HQ")
    store.add_message(
        conn,
        p_chat["id"],
        "system",
        "立项协同群",
        meta=json.dumps({"project_id": pid}),
    )

    # 确保当前无 tiktok 员工在岗
    conn.execute("""
        UPDATE ai_employees SET status='fired'
        WHERE role_id IN (SELECT id FROM role_templates WHERE tag='tiktok')
    """)
    conn.commit()

    # 确认在岗员工中无 tiktok
    assert len(roles_mod.pick(conn, ["tiktok"], entity_id="LE-GROUP-HQ")) == 0

    # 模拟 ACP 交付产物
    art_dir = art_mod.root()
    deliverable_file = art_dir / "tiktok_campaign_plan.md"
    deliverable_file.write_text("# TikTok海外运营全案\n\n已完成受众画像分析与视频创意规划。", encoding="utf-8")

    def mock_execute_run(c, run_id, *args, **kwargs):
        # 写入交付物记录使 _flow_verdict 通过
        c.execute("""
            INSERT INTO artifacts (id, run_id, tenant_id, art_seq, file_path, content, checksum, bytes, state)
            VALUES (?, ?, 'TENANT-HQ', 1, ?, 'mock-content', 'mock-sha', 128, 'active')
        """, (f"ART-RUN-{run_id[-6:]}", run_id, str(deliverable_file)))
        c.commit()
        return {"task_state": "completed", "usage_tokens": 500, "runner": "native"}

    monkeypatch.setattr(dsh_adapter, "execute_run", mock_execute_run)

    # 执行流程节点：角色为未实例化的 tiktok
    node = {
        "key": "tiktok_op",
        "title": "TikTok社媒全案运营",
        "role": "tiktok",
        "gate": "按要求输出运营全案及脚本",
    }
    out = pipeline._flow_execute(conn, pid, node, "搭建TikTok海外短视频流量引流")

    # 验证：零报错推进完成，且员工已被即时招聘并分配给任务
    assert out["state"] == "completed"
    assert out["task_id"].startswith("T-")

    # 验证员工库已被动态入职
    tiktok_people = roles_mod.pick(conn, ["tiktok"], entity_id="LE-GROUP-HQ")
    assert len(tiktok_people) >= 1
    assert tiktok_people[0]["name"] == "TikTok运营专家"
    assert out["employee"] == tiktok_people[0]["id"]

    # 验证项目协作群收到入驻接棒消息
    project_chats = pipeline._find_project_chats(conn, pid)
    assert len(project_chats) >= 1
    recent_msgs = conn.execute(
        "SELECT * FROM chat_messages WHERE chat_id IN ({}) ORDER BY id DESC LIMIT 5".format(
            ",".join(f"'{cid}'" for cid in project_chats)
        )
    ).fetchall()
    arrival_notices = [m for m in recent_msgs if "【人事部定向调配】" in m["content"]]
    assert len(arrival_notices) >= 1
    assert "TikTok运营专家" in arrival_notices[0]["content"]


def test_dispatcher_open_group_adaptive_staffing(env):
    conn, _ = env

    # 确保 auto (车规架构师) 和 sec (安全专家) 均无在岗员工
    conn.execute("""
        UPDATE ai_employees SET status='fired'
        WHERE role_id IN (SELECT id FROM role_templates WHERE tag IN ('auto', 'sec'))
    """)
    conn.commit()

    assert len(roles_mod.pick(conn, ["auto", "sec"], entity_id="LE-GROUP-HQ")) == 0

    plan = {
        "name": "智能车载安全审计战役",
        "roles": ["auto", "sec"],
        "how": "keyword",
        "reason": "汽车与安全专精",
        "nodes": [
            {"key": "arch", "title": "车规系统架构设计", "role": "auto", "gate": "架构设计文档"},
            {"key": "sec_eval", "title": "安全攻防与渗透合规", "role": "sec", "gate": "安全评估报告"},
        ],
    }

    group = dispatcher.open_group(
        conn,
        "智能车载网联系统车规架构与安全防护专项",
        plan,
        entity_id="LE-GROUP-HQ",
        tenant_id="TENANT-HQ",
    )

    assert group is not None
    assert "chat_id" in group
    chat_id = group["chat_id"]

    # 验证两位专家均被即时招募入组
    members = group["members"]
    tags = {m["tag"] for m in members}
    assert "auto" in tags
    assert "sec" in tags
    names = {m["name"] for m in members}
    assert "车规架构师" in names
    assert "安全专家" in names

    # 验证 chat_members 表
    db_members = store.members(conn, chat_id)
    assert len(db_members) == 2

    # 验证群内发出了人事调配系统通知
    msgs = conn.execute("SELECT * FROM chat_messages WHERE chat_id=?", (chat_id,)).fetchall()
    notice_msgs = [m for m in msgs if "【人事调配】根据作战需求，已定向调配在岗专家进驻本项目组" in m["content"]]
    assert len(notice_msgs) >= 1
    assert "车规架构师" in notice_msgs[0]["content"]
    assert "安全专家" in notice_msgs[0]["content"]
