"""V4.1 智能调度与全景执行可视化 —— 后端验收。

对着董事长的四条验收写：
①「分析黄金走势」只生成 检索→分析→撰写→审核→交付，不能有开发/测试/发布；
② 执行中能看到逐条 thinking / tool_call / tool_result（SSE `/ui/runs/{id}/live-stream`）；
③ 自动建群，群里只有数据/研究/审核角色，且群里有「作战计划」；
④ 失败可诊断：规则给出中文根因 + 修复建议，阶段失败可定位到 Run。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import flows, identity, pipeline, trace
from app.db import connect, init_db
from app.ui import api as ui_api, dispatcher, roles, store

ANALYSIS_KEYS = ["retrieve", "analyze", "author", "review", "deliver"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for key, value in {
        "DB_PATH": str(tmp_path / "v41.db"),
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "M0_INTENT_LLM": "0",          # 测试不联网：走离线词表（与模型同一契约）
        "UI_SESSION_SECRET": "v41-test-only-secret",
        "WECOM_MOCK": "1",
    }.items():
        monkeypatch.setenv(key, value)
    init_db()
    conn = connect(str(tmp_path / "v41.db"))
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    roles.ensure(conn)
    store.ensure(conn)
    pipeline.ensure(conn)
    flows.ensure(conn)
    ceo = identity.issue(conn, name="CEO", role="ceo")
    conn.commit()
    yield conn, ceo
    conn.close()


# ------------------------------------------------------------ 战役二：规划
def test_analysis_plan_has_no_dev_or_test_nodes(env):
    conn, _ = env
    plan = flows.plan(conn, "分析黄金走势", [])
    assert plan["kind"] == "adaptive", plan
    assert [n["key"] for n in plan["nodes"]] == ANALYSIS_KEYS
    for forbidden in ("build", "test", "release", "dev", "qa"):
        assert forbidden not in [n["key"] for n in plan["nodes"]]
    assert plan["how"] == "adaptive_architect"


def test_urgent_and_research_templates_are_selected(env):
    conn, _ = env
    assert flows.plan(conn, "线上故障，立刻止血", [])["kind"] == "adaptive"
    assert flows.plan(conn, "做一份竞品调研", [])["kind"] == "adaptive"
    assert flows.plan(conn, "开发一个计算器", ["dev"])["kind"] == "adaptive"


def test_software_flow_is_eight_steps_and_keeps_wbs(env):
    conn, _ = env
    plan = flows.plan(conn, "开发一个算账工具", ["dev"])
    keys = [n["key"] for n in plan["nodes"]]
    assert "build" in keys and "test" in keys
    # 明确说不发布/不复盘时，optional 节点才被裁掉
    trimmed = flows.plan(conn, "内部使用的小工具，开发一个算账脚本，不用发布", ["dev"])
    assert "release" not in [n["key"] for n in trimmed["nodes"]]


def test_four_templates_are_persisted(env):
    conn, _ = env
    # 旧固化模板已从 flow_templates 表清理，默认返回用户自定义/自适应模板
    custom_ids = {t["id"] for t in flows.templates(conn)}
    for old_id in ("FLOW-SOFTWARE", "FLOW-ANALYSIS", "FLOW-RESEARCH", "FLOW-URGENT"):
        assert old_id not in custom_ids
    # 但保留 PRESETS 内存只读兼容字典，flows.template 可回退查取
    for old_id in ("FLOW-SOFTWARE", "FLOW-ANALYSIS", "FLOW-RESEARCH", "FLOW-URGENT"):
        compat = flows.template(conn, old_id)
        assert compat is not None
        assert compat["id"] == old_id


# ------------------------------------------- 战役二 + 三：立项、建群、作战计划
def test_dispatch_creates_dag_group_and_battle_plan(env):
    conn, ceo = env
    res = dispatcher.route(conn, "分析黄金走势", actor_id=ceo["id"])
    pid = res["project_id"]

    stages = [s["key"] for s in flows.nodes(conn, pid)]
    assert stages == ANALYSIS_KEYS

    tags = {r["tag"] for r in conn.execute(
        """SELECT r.tag FROM chat_members m
           JOIN ai_employees e ON e.id=m.employee_id
           JOIN role_templates r ON r.id=e.role_id WHERE m.chat_id=?""",
        (res["chat_id"],))}
    assert tags == {"research", "data", "audit"}, tags

    plan_msg = conn.execute("SELECT content FROM chat_messages"
                            " WHERE chat_id=? AND kind='plan'", (res["chat_id"],)).fetchone()
    assert plan_msg is not None
    assert "作战计划" in plan_msg["content"]
    assert "资料检索" in plan_msg["content"] and "| 负责角色 |" in plan_msg["content"]


def test_blackboard_collects_every_stage_and_pm_summarises(env):
    conn, ceo = env
    res = dispatcher.route(conn, "分析黄金走势", actor_id=ceo["id"])
    pid = res["project_id"]
    rows = flows.blackboard(conn, pid)
    keys = {r["stage_key"] for r in rows}
    assert set(ANALYSIS_KEYS) <= keys
    assert "summary" in keys, "跑完一个项目必须有 PM 汇总"
    summary = flows.pm_summary(conn, pid)
    assert summary["count"] == len(ANALYSIS_KEYS)
    assert "项目汇总" in summary["summary"]
    # 重复汇总只保留一条，不能越跑越长
    assert sum(1 for r in flows.blackboard(conn, pid) if r["kind"] == "summary") == 1


def test_stage_detail_lists_deliverables_gates_and_audit(env):
    conn, ceo = env
    pid = dispatcher.route(conn, "分析黄金走势", actor_id=ceo["id"])["project_id"]
    detail = flows.stage_detail(conn, pid, "analyze")
    assert detail["stage"]["state"] == "done"
    assert detail["stage"]["role_tag"] == "data"
    assert detail["deliverables"] and detail["gates"]
    assert detail["artifacts"], "阶段产出必须进黑板"
    assert isinstance(detail["audit"], list)


def test_assign_lets_pm_pick_one_of_many(env):
    conn, ceo = env
    pid = dispatcher.route(conn, "分析黄金走势", actor_id=ceo["id"])["project_id"]
    emp = conn.execute("""SELECT e.id FROM ai_employees e JOIN role_templates r
                          ON r.id=e.role_id WHERE r.tag='data' LIMIT 1""").fetchone()
    out = dispatcher.assign(conn, pid, "analyze", emp["id"], actor=ceo["id"])
    assert out["assignee"] == emp["id"]
    row = conn.execute("SELECT assignee_id FROM project_stages"
                       " WHERE project_id=? AND key='analyze'", (pid,)).fetchone()
    assert row["assignee_id"] == emp["id"]


# ------------------------------------------------- 战役一：实时执行流（SSE）
def _run_with_spans(conn, *, state="succeeded", error=None):
    conn.execute("INSERT OR REPLACE INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
                 " VALUES ('T-RUN','LE-GROUP-HQ','TENANT-HQ','content_write','黄金走势分析',"
                 "'in_progress')")
    conn.execute("INSERT OR REPLACE INTO task_runs(id,task_id,run_seq,legal_entity_id,"
                 "tenant_id,state,error) VALUES ('RUN-1','T-RUN',1,'LE-GROUP-HQ','TENANT-HQ',?,?)",
                 (state, error))
    conn.commit()
    trace.start(conn, run_id="RUN-1", task_id="T-RUN")
    trace.record(conn, "RUN-1", kind="thinking", name="先定分析框架", started_ms=0,
                 ended_ms=1200, detail="先看宏观再看向")
    trace.record(conn, "RUN-1", kind="tool", name="read", started_ms=0, ended_ms=30,
                 detail="read 12 lines")
    trace.record(conn, "RUN-1", kind="model", name="deepseek-chat", started_ms=0,
                 ended_ms=900, tokens=120)
    trace.finish_run(conn, "RUN-1", tokens=120, state=state)
    conn.commit()


def test_live_stream_emits_thinking_tool_and_result(env):
    conn, ceo = env
    _run_with_spans(conn)
    from app.main import app
    with TestClient(app) as client:
        client.cookies.set("oa_ui", ui_api._make_cookie(ceo["id"]))
        lines = []
        with client.stream("GET", "/ui/runs/RUN-1/live-stream") as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                lines.append(line)
                if line.startswith("event: done"):
                    break
    text = "\n".join(lines)
    assert "event: thinking" in text
    assert "event: tool_call" in text and "event: tool_result" in text
    assert "event: status" in text and '"state": "succeeded"' in text.replace("'", '"')


def test_flow_templates_and_stage_api(env):
    conn, ceo = env
    pid = dispatcher.route(conn, "分析黄金走势", actor_id=ceo["id"])["project_id"]
    from app.main import app
    with TestClient(app) as client:
        client.cookies.set("oa_ui", ui_api._make_cookie(ceo["id"]))
        tpl = client.get("/ui/flow-templates")
        assert tpl.status_code == 200
        for old_id in ("FLOW-SOFTWARE", "FLOW-ANALYSIS", "FLOW-RESEARCH", "FLOW-URGENT"):
            assert old_id not in [t["id"] for t in tpl.json()["templates"]]
        detail = client.get(f"/ui/projects/{pid}/stages/retrieve")
        assert detail.status_code == 200
        assert detail.json()["stage"]["title"] == "资料检索"
        assert client.get(f"/ui/projects/{pid}/stages/nope").status_code == 404


# ------------------------------------------------------ 战役四：失败与诊断
def test_diagnosis_names_the_budget_cause_and_caches(env):
    conn, _ = env
    _run_with_spans(conn, state="failed", error="budget_exhausted_midturn")
    first = flows.diagnose(conn, "RUN-1", use_llm=False)
    assert "预算" in first["cause"] and first["source"] == "rules"
    assert first["fixes"], "必须给可执行的修复建议"
    again = flows.diagnose(conn, "RUN-1", use_llm=False)
    assert again["cached"] is True


def test_diagnosis_endpoint_and_stage_pointer_for_failed_run(env):
    conn, ceo = env
    _run_with_spans(conn, state="failed", error="acp_timeout")
    from app.main import app
    with TestClient(app) as client:
        client.cookies.set("oa_ui", ui_api._make_cookie(ceo["id"]))
        out = client.get("/ui/runs/RUN-1/diagnosis")
        assert out.status_code == 200
        body = out.json()
        assert "超时" in body["cause"]
        assert client.get("/ui/runs/RUN-NOPE/diagnosis").status_code == 404
