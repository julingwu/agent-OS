"""Unit and integration tests for AI Architect Adaptive Workflow Synthesis.

验证核心：
1. 彻底取代死板固定的 4 大预设模板，根据用户需求深入分析核心，动态输出结构化工序节点流水线；
2. 节点数量、工序名称、责任角色完全自适应（如 TikTok 出海、量化交易策略、全栈项目开发等）；
3. 最后一个工序节点强制为 deliver (role='platform', kind='deliver')；
4. 动态工序模板自动持久化到 flow_templates 表，支持 flows.template(conn, flow_id) 查取；
5. 协同与招聘无缝闭环：工序中的所有角色与 recruitment.ensure_workflow_staff 顺畅联动；
6. CEO 调度台与总经理办公室端到端立项、建群、分派与作战计划无缝协同。
"""
from __future__ import annotations

import json
import pytest

from app import flows, identity, pipeline, recruitment
from app.db import connect, init_db
from app.ui import dispatcher, roles, store


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "adaptive_flows.db")
    for key, value in {
        "DB_PATH": db_path,
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "M0_INTENT_LLM": "0",  # 默认离线测试自适应启发式引擎
        "UI_SESSION_SECRET": "test-adaptive-secret",
        "WECOM_MOCK": "1",
    }.items():
        monkeypatch.setenv(key, value)
    init_db()
    conn = connect(db_path)
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    roles.ensure(conn)
    store.ensure(conn)
    pipeline.ensure(conn)
    flows.ensure(conn)
    ceo = identity.issue(conn, name="CEO", role="ceo", legal_entity_id="LE-GROUP-HQ")
    conn.commit()
    yield conn, ceo
    conn.close()


def test_tiktok_marketing_adaptive_workflow_synthesis(env):
    """验证 TikTok 美妆出海营销需求动态拆解出的专属工序流水线。"""
    conn, _ = env
    demand = "TikTok美妆出海营销全流程投放与落地页变现，抓取竞品情报并测算ROI"
    plan = flows.plan(conn, demand)

    assert plan["kind"] == "adaptive"
    assert plan["flow_id"].startswith("FLOW-ADAPTIVE-")
    assert plan["how"] == "adaptive_architect"
    assert "出海" in plan["name"] or "营销" in plan["name"]

    # 验证节点数量自适应（3到6步）
    nodes = plan["nodes"]
    assert 3 <= len(nodes) <= 6

    # 验证关键阶段与角色覆盖
    keys = [n["key"] for n in nodes]
    assert "market_intel" in keys
    assert "script_write" in keys
    assert "roi_plan" in keys
    assert keys[-1] == "deliver"

    # 验证最后一个节点强制为 deliver (role='platform', kind='deliver')
    last_node = nodes[-1]
    assert last_node["key"] == "deliver"
    assert last_node["role"] == "platform"
    assert last_node["kind"] == "deliver"

    # 验证角色列表自适应提取
    roles_needed = plan["roles"]
    assert "research" in roles_needed
    assert "content" in roles_needed
    assert "data" in roles_needed
    assert "platform" not in roles_needed

    # 验证模板持久化落盘并可回查
    saved = flows.template(conn, plan["flow_id"])
    assert saved is not None
    assert saved["id"] == plan["flow_id"]
    assert len(saved["nodes"]) == len(nodes)


def test_quant_trading_strategy_adaptive_synthesis(env):
    """验证量化交易策略需求动态拆解出专属量化Alpha回测与风控工序。"""
    conn, _ = env
    demand = "构建高频量化Alpha多因子选股与回测执行系统，含穿透式风控"
    plan = flows.plan(conn, demand)

    assert plan["kind"] == "adaptive"
    assert "量化" in plan["name"]

    keys = [n["key"] for n in plan["nodes"]]
    assert "market_data" in keys
    assert "strategy_model" in keys
    assert "backtest_verify" in keys
    assert keys[-1] == "deliver"

    # 角色需包含量化专家 quant
    assert "quant" in plan["roles"]
    assert "data" in plan["roles"]
    assert "audit" in plan["roles"]

    # 最后一个节点为总装交付
    assert plan["nodes"][-1]["role"] == "platform"
    assert plan["nodes"][-1]["kind"] == "deliver"


def test_software_engineering_adaptive_synthesis_and_trim(env):
    """验证软件研发需求动态生成架构、研发、质检全流程，且支持裁切发布节点。"""
    conn, _ = env
    demand = "设计并开发一个支持高并发的分布式分布式订单计算器服务系统"
    plan = flows.plan(conn, demand, ["dev"])

    assert plan["kind"] == "adaptive"
    assert "研发" in plan["name"] or "开发" in plan["name"]
    keys = [n["key"] for n in plan["nodes"]]
    assert "arch_design" in keys
    assert "build" in keys
    assert "test" in keys
    assert "release" in keys
    assert keys[-1] == "deliver"

    # 内部使用无需发布时，自适应裁切 release
    trimmed_plan = flows.plan(conn, "开发一个内部算账小工具，仅内部使用，不用发布", ["dev"])
    trimmed_keys = [n["key"] for n in trimmed_plan["nodes"]]
    assert "build" in trimmed_keys
    assert "test" in trimmed_keys
    assert "release" not in trimmed_keys
    assert trimmed_keys[-1] == "deliver"


def test_recruitment_seamless_cooperation_with_adaptive_roles(env):
    """验证工序中的所有自适应角色均能与 recruitment 顺畅协作，实现即时招募入驻。"""
    conn, _ = env
    demand = "TikTok海外美妆短视频矩阵搭建与独立站ROI投流测算"
    plan = flows.plan(conn, demand)
    roles_needed = plan["roles"]

    # 遍历工序中的所有角色，调用 ensure_workflow_staff
    for r_tag in roles_needed:
        emp = recruitment.ensure_workflow_staff(
            conn,
            role_tag=r_tag,
            requirement=demand,
            entity_id="LE-GROUP-HQ",
            tenant_id="TENANT-HQ",
        )
        assert emp["id"].startswith("AI-")
        assert emp["tag"] == r_tag or emp["role_name"]

    # 验证挑人接口能挑到所有岗位专家
    picked = roles.pick(conn, roles_needed, entity_id="LE-GROUP-HQ")
    picked_tags = {p["tag"] for p in picked}
    for r_tag in roles_needed:
        assert r_tag in picked_tags, f"角色 {r_tag} 应在在岗员工列表中"


def test_dispatcher_end_to_end_project_and_battle_plan(env):
    """验证 CEO 调度台端到端立项、建群、下达作战计划及阶段初始化的完整闭环。"""
    conn, ceo = env
    demand = "TikTok出海美妆短视频投放并搭建前端落地页与投流ROI测算"
    res = dispatcher.route(conn, demand, actor_id=ceo["id"])

    assert res["project_id"] is not None
    pid = res["project_id"]
    chat_id = res["chat_id"]

    # 1. 验证 stages 与 flows.nodes 完全匹配自适应工序
    stages = flows.nodes(conn, pid)
    assert len(stages) >= 4
    stage_keys = [s["key"] for s in stages]
    assert "market_intel" in stage_keys
    assert "deliver" in stage_keys
    assert stages[-1]["key"] == "deliver"

    # 2. 验证协作群成员包含自适应入驻的专家
    group_members = conn.execute(
        """SELECT r.tag, e.name FROM chat_members m
           JOIN ai_employees e ON e.id=m.employee_id
           JOIN role_templates r ON r.id=e.role_id WHERE m.chat_id=?""",
        (chat_id,)
    ).fetchall()
    member_tags = {r["tag"] for r in group_members}
    assert "research" in member_tags
    assert "content" in member_tags
    assert "data" in member_tags

    # 3. 验证群内下发的作战计划内容包含自适应步骤与判据闸门
    plan_msg = conn.execute(
        "SELECT content FROM chat_messages WHERE chat_id=? AND kind='plan'",
        (chat_id,)
    ).fetchone()
    assert plan_msg is not None
    assert "作战计划" in plan_msg["content"]
    assert "行业情报" in plan_msg["content"]
    assert "全套物料归档" in plan_msg["content"]


def test_llm_architect_synthesis_mode(env, monkeypatch):
    """验证在开启大模型时，AI 架构师输出结构化 JSON 能够被正确解析与总装。"""
    conn, _ = env
    monkeypatch.setenv("M0_INTENT_LLM", "1")

    llm_output = {
        "name": "SaaS全自动化客户成功运营流",
        "kind": "adaptive",
        "summary": "AI架构师深度推导的客户生命周期自适应流水线",
        "nodes": [
            {"key": "health_check", "title": "客户健康度诊断", "role": "data", "gate": "指标异动可归因", "kind": "doc"},
            {"key": "nurture_strategy", "title": "促活与培育策略制定", "role": "content", "gate": "触达话术完备", "kind": "doc"},
            {"key": "exec_outreach", "title": "自动化营销触达执行", "role": "ops", "gate": "触达渠道通畅", "kind": "code"},
            {"key": "churn_audit", "title": "流失风险与合规复盘", "role": "audit", "gate": "红线核验通过", "kind": "doc"},
            {"key": "deliver", "title": "客户交付总装", "role": "platform", "gate": "归档完成", "kind": "deliver"},
        ]
    }

    from app import llm
    monkeypatch.setattr(llm, "has_key", lambda: True)
    monkeypatch.setattr(llm, "complete", lambda *args, **kwargs: json.dumps(llm_output, ensure_ascii=False))

    plan = flows.plan(conn, "帮我设计一套SaaS自动化客户运营方案")
    assert plan["kind"] == "adaptive"
    assert plan["name"] == "SaaS全自动化客户成功运营流"
    assert plan["how"] == "adaptive_architect"
    assert len(plan["nodes"]) == 5
    assert plan["nodes"][0]["key"] == "health_check"
    assert plan["nodes"][-1]["key"] == "deliver"
    assert "data" in plan["roles"]
    assert "ops" in plan["roles"]
    assert "audit" in plan["roles"]
