"""Tests for pipeline execution kernel tool dispatch, role task generation, and blackboard context propagation."""
from __future__ import annotations

import pathlib
import pytest

from app import flows, identity, pipeline
from app.db import connect, init_db
from app.engine import runner_native, task_generator


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "test_kernel.db"
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("M0_WORKSPACE", str(ws))
    init_db()
    conn = connect(str(db_file))
    conn.execute(
        "INSERT OR IGNORE INTO legal_entities (id, name, type, tenant_id) "
        "VALUES ('LE-GROUP-HQ', '集团总部', 'group_hq', 'TENANT-HQ')"
    )
    ceo = identity.issue(conn, name="Test CEO", role="ceo")
    conn.commit()
    yield conn, ceo, ws
    conn.close()


def test_task_generator_role_packs(env):
    """Test all 7 newly supported roles generate exact templates, task types, and pack IDs."""
    conn, _, _ = env
    role_expectations = [
        ("design", "web-v1", "web_write", task_generator.TEMPLATE_WEB),
        ("ui", "web-v1", "web_write", task_generator.TEMPLATE_WEB),
        ("finance", "finance-v1", "finance_write", task_generator.TEMPLATE_FINANCE),
        ("quant", "analyst-v1", "analyst_write", task_generator.TEMPLATE_ANALYST),
        ("architect", "research-v1", "research_write", task_generator.TEMPLATE_RESEARCH),
        ("hr", "research-v1", "research_write", task_generator.TEMPLATE_RESEARCH),
        ("marketing", "content-v1", "content_write", task_generator.TEMPLATE_RESEARCH),
    ]

    for role, expected_pack, expected_ttype, expected_tpl in role_expectations:
        out = task_generator.generate(
            conn,
            f"为部门生成工序交付物：{role}",
            legal_entity_id="LE-GROUP-HQ",
            role_tag=role,
        )
        assert out["pack_id"] == expected_pack, f"{role} pack mismatch"
        assert out["task_type"] == expected_ttype, f"{role} task_type mismatch"
        assert out["pack_source"] == "role_tag", f"{role} pack_source mismatch"
        assert len(out["criteria"]) == len(expected_tpl), f"{role} criteria count mismatch"

        row = conn.execute("SELECT pack_id, task_type FROM tasks WHERE id=?", (out["task_id"],)).fetchone()
        assert row["pack_id"] == expected_pack
        assert row["task_type"] == expected_ttype


def test_runner_native_tool_definitions():
    """Verify runner_native._LLM_TOOLS exposes web_search and render_chart."""
    tool_names = [t["function"]["name"] for t in runner_native._LLM_TOOLS]
    assert "write_file" in tool_names
    assert "read_file" in tool_names
    assert "run_script" in tool_names
    assert "web_search" in tool_names
    assert "render_chart" in tool_names


def test_runner_native_dispatch_web_search(env, monkeypatch):
    """Verify runner_native _dispatch_llm_tool dispatches web_search correctly."""
    conn, ceo, ws = env
    task_id = "T-DISPATCH-SEARCH-1"
    run_id = "RUN-DISPATCH-SEARCH-1"
    conn.execute(
        "INSERT OR REPLACE INTO tasks(id, legal_entity_id, tenant_id, task_type, title, state) "
        "VALUES (?, 'LE-GROUP-HQ', 'TENANT-HQ', 'research-v1', '搜索调研', 'in_progress')",
        (task_id,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO task_runs(id, task_id, run_seq, legal_entity_id, tenant_id, state, runner, actor_identity) "
        "VALUES (?, ?, 1, 'LE-GROUP-HQ', 'TENANT-HQ', 'spawned', 'native', ?)",
        (run_id, task_id, ceo["id"]),
    )
    conn.commit()

    kernel = runner_native.NativeKernel(
        conn, run_id=run_id, task_id=task_id, entity="LE-GROUP-HQ", actor=ceo["id"], prompt="搜索测试"
    )

    from app.agent import web_tools
    def fake_web_search(query: str, max_results: int = 8, **kwargs):
        return {"ok": True, "query": query, "results": [{"title": "Test Title", "snippet": "Test snippet"}]}

    monkeypatch.setattr(web_tools, "web_search", fake_web_search)

    res = kernel._dispatch_llm_tool("web_search", {"query": "AI行业最新动态"})
    assert res["ok"] is True
    assert res["query"] == "AI行业最新动态"
    assert len(res["results"]) == 1


def test_runner_native_dispatch_render_chart(env):
    """Verify runner_native _dispatch_llm_tool renders chart and saves PNG to sandbox workspace."""
    conn, ceo, ws = env
    task_id = "T-DISPATCH-CHART-1"
    run_id = "RUN-DISPATCH-CHART-1"
    conn.execute(
        "INSERT OR REPLACE INTO tasks(id, legal_entity_id, tenant_id, task_type, title, state) "
        "VALUES (?, 'LE-GROUP-HQ', 'TENANT-HQ', 'analyst-v1', '财务数据分析图表', 'in_progress')",
        (task_id,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO task_runs(id, task_id, run_seq, legal_entity_id, tenant_id, state, runner, actor_identity) "
        "VALUES (?, ?, 1, 'LE-GROUP-HQ', 'TENANT-HQ', 'spawned', 'native', ?)",
        (run_id, task_id, ceo["id"]),
    )
    conn.commit()

    kernel = runner_native.NativeKernel(
        conn, run_id=run_id, task_id=task_id, entity="LE-GROUP-HQ", actor=ceo["id"], prompt="绘制财务柱状图"
    )

    res = kernel._dispatch_llm_tool("render_chart", {
        "title": "季度营收统计",
        "chart_type": "bar",
        "data": {
            "categories": ["Q1", "Q2", "Q3", "Q4"],
            "series": [{"name": "营收(万元)", "values": [350, 480, 620, 890]}],
        },
        "theme": "financial",
    })

    assert res["ok"] is True
    assert "artifact_id" in res
    assert "sandbox_path" in res
    chart_path = pathlib.Path(res["sandbox_path"])
    assert chart_path.is_file()
    assert chart_path.name == "季度营收统计.png"
    assert chart_path.stat().st_size > 1000  # valid PNG binary

    # Verify sandbox list_files sees the chart
    assert "季度营收统计.png" in kernel.list_files()


def test_blackboard_context_truncation_relaxed_to_1000(env):
    """Verify upstream stage conclusions preserve up to 1000 characters on blackboard."""
    conn, _, _ = env
    pid = "P-TEST-BB-1000"
    conn.execute(
        "INSERT OR IGNORE INTO projects (id, name, requirement) VALUES (?, 'Test Long Context', '测试长黑板上下文')",
        (pid,),
    )

    # Construct an upstream report with 800 chars (well above old 240 limit)
    long_excerpt = "【核心市场分析数据宽表】\n" + (
        "| 区域 | 细分产品 | 季度销量 | 同比增速 | 毛利率 | 核心竞品对比 |\n"
        "| 华东 | 高性能芯片A | 120,000 | +45.2% | 58.4% | 竞品X份额下降5% |\n"
        "| 华南 | 工业控制套件 | 85,000 | +32.8% | 51.2% | 替代方案研发推进 |\n"
        "| 华北 | 云服务解决方案 | 45,000 | +68.1% | 62.0% | 标杆案例已落地三甲医院 |\n"
    ) * 4
    assert len(long_excerpt) > 500

    flows.blackboard_write(
        conn,
        pid=pid,
        stage_key="research",
        role_tag="research",
        title="深度竞品与市场调研报告",
        content=long_excerpt,
        file_path="deliveries/01_research.md",
    )

    ctx = pipeline.get_blackboard_context(conn, pid)
    assert "【项目上游已交付结论与上下文】" in ctx
    assert "[research]" in ctx
    assert "深度竞品与市场调研报告" in ctx
    # Ensure the excerpt extends far beyond 240 chars and includes the later table content
    assert len(ctx) > 400
    assert "标杆案例已落地三甲医院" in ctx
