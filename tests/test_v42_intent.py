"""V4.2 三战役验收：LLM 意图分类、工具调用上限、复合流（多标签）。

要点：
* 意图分类走**一次**轻量模型调用；测试里用 monkeypatch 顶掉网络，只验契约。
* 置信度 < 0.6 必须追问用户，绝不硬派流程（错派的代价是一次烧 token 的执行）。
* 工具上限在底座（runner_acp）强制：搜索 5 次 / 抓取 3 次，超限报中文错误。
"""
from __future__ import annotations

import json

import pytest

from app import flows, identity, pipeline
from app.db import connect, init_db
from app.engine import runner_acp
from app.ui import dispatcher, roles, store


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for key, value in {
        "DB_PATH": str(tmp_path / "v42.db"),
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "M0_INTENT_LLM": "1",
        "UI_SESSION_SECRET": "v42-test-only-secret",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("M0_MAX_SEARCH_CALLS", raising=False)
    monkeypatch.delenv("M0_MAX_FETCH_CALLS", raising=False)
    init_db()
    conn = connect(str(tmp_path / "v42.db"))
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    roles.ensure(conn)
    store.ensure(conn)
    pipeline.ensure(conn)
    flows.ensure(conn)
    from app import trace
    trace.ensure(conn)
    ceo = identity.issue(conn, name="CEO", role="ceo")
    conn.commit()
    yield conn, ceo
    conn.close()


def fake_llm(monkeypatch, payload: dict | str, *, capture: list | None = None):
    from app import llm

    def complete(prompt, system=None, *, max_tokens=1800, temperature=0.5, timeout=120.0):
        if capture is not None:
            capture.append({"prompt": prompt, "system": system,
                            "max_tokens": max_tokens, "temperature": temperature})
        return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(llm, "complete", complete)


# ------------------------------------------------ 战役一：LLM 意图分类
def test_colloquial_gold_request_is_classified_as_analysis(env, monkeypatch):
    conn, _ = env
    fake_llm(monkeypatch, {"task_type": "analysis", "confidence": 0.93,
                           "reason": "用户想知道金价行情，属于分析类"})
    plan = flows.plan(conn, "帮我瞧瞧今天金子啥情况", [])
    assert plan["flow_id"] == "FLOW-ANALYSIS", plan
    assert plan["how"] == "llm"
    assert [n["key"] for n in plan["nodes"]] == ["retrieve", "analyze", "author",
                                                 "review", "deliver"]
    # 口语请求里没有「分析/走势」这类词，靠的是模型理解
    assert "分析" not in "帮我瞧瞧今天金子啥情况"


def test_intent_prompt_is_small_and_calls_the_model_once(env, monkeypatch):
    conn, _ = env
    seen: list = []
    fake_llm(monkeypatch, {"task_type": "analysis", "confidence": 0.9, "reason": "x"},
             capture=seen)
    flows.plan(conn, "帮我瞧瞧今天金子啥情况", [])
    assert len(seen) == 1, "一次派单只能有一次意图调用"
    assert seen[0]["max_tokens"] <= 150
    assert len(seen[0]["prompt"]) < 1200, "提示词要够小（约 500 token 以内）"
    assert "帮我瞧瞧今天金子啥情况" in seen[0]["prompt"]


def test_low_confidence_asks_the_user_instead_of_dispatching(env, monkeypatch):
    conn, ceo = env
    fake_llm(monkeypatch, {"task_type": "software", "confidence": 0.42,
                           "reason": "看不出来"})
    before = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    res = dispatcher.route(conn, "帮我把那个东西弄一下", actor_id=ceo["id"])
    assert res["clarify"] is True and res["project_id"] is None
    assert res["question"] == flows.CLARIFY_QUESTION
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == before
    msg = conn.execute("SELECT content,kind FROM chat_messages WHERE kind='clarify'").fetchone()
    assert msg is not None and "报告" in msg["content"]


def test_model_failure_falls_back_to_offline_words(env, monkeypatch):
    conn, _ = env

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    from app import llm
    monkeypatch.setattr(llm, "complete", boom)
    plan = flows.plan(conn, "帮我瞧瞧今天金子啥情况", [])
    assert plan["how"] == "fallback"
    assert plan["flow_id"] == "FLOW-ANALYSIS", "模型挂了也要把「金子」认成分析"


def test_garbage_model_output_does_not_break_dispatch(env, monkeypatch):
    conn, ceo = env
    fake_llm(monkeypatch, "我觉得这是开发任务")   # 不是 JSON
    res = dispatcher.route(conn, "帮我瞧瞧今天金子啥情况", actor_id=ceo["id"])
    assert res["project_id"], res
    assert res["flow"]["flow_id"] == "FLOW-ANALYSIS"


# ------------------------------------------------ 流程节点验收（v4.2 修复）
def test_flow_verdict_accepts_when_the_run_produced_an_artifact(env):
    """真跑成功的节点不该被「文案字数」判据误杀：有交付物即通过。"""
    conn, _ = env
    conn.execute("INSERT OR REPLACE INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
                 " VALUES ('T-FLOW','LE-GROUP-HQ','TENANT-HQ','content_write','检索','pending_review')")
    conn.execute("INSERT OR REPLACE INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,"
                 "state,error) VALUES ('RUN-FLOW','T-FLOW',1,'LE-GROUP-HQ','TENANT-HQ',"
                 "'succeeded',NULL)")
    conn.execute("INSERT OR REPLACE INTO artifacts(id,run_id,tenant_id,art_seq,file_path,"
                 "checksum,bytes) VALUES ('ART-FLOW','RUN-FLOW','TENANT-HQ',1,"
                 "'C:/tmp/research.md','x',10)")
    conn.commit()
    node = {"key": "retrieve", "title": "资料检索"}
    assert pipeline._flow_verdict(conn, "T-FLOW", "RUN-FLOW", node) == "completed"
    assert conn.execute("SELECT state FROM tasks WHERE id='T-FLOW'").fetchone()["state"] == "completed"


def test_flow_verdict_rejects_a_run_without_deliverables(env):
    conn, _ = env
    conn.execute("INSERT OR REPLACE INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
                 " VALUES ('T-NOART','LE-GROUP-HQ','TENANT-HQ','content_write','空跑','pending_review')")
    conn.execute("INSERT OR REPLACE INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,"
                 "state) VALUES ('RUN-NOART','T-NOART',1,'LE-GROUP-HQ','TENANT-HQ','succeeded')")
    conn.commit()
    with pytest.raises(RuntimeError, match="没有任何交付物"):
        pipeline._flow_verdict(conn, "T-NOART", "RUN-NOART", {"key": "retrieve", "title": "资料检索"})


def test_succeeded_run_with_failed_stage_has_a_non_generic_diagnosis(env):
    conn, _ = env
    conn.execute("INSERT OR REPLACE INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
                 " VALUES ('T-OK','LE-GROUP-HQ','TENANT-HQ','content_write','检索','rejected')")
    conn.execute("INSERT OR REPLACE INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,"
                 "state,error) VALUES ('RUN-OK','T-OK',1,'LE-GROUP-HQ','TENANT-HQ',"
                 "'succeeded',NULL)")
    conn.commit()
    out = flows.diagnose(conn, "RUN-OK", use_llm=False)
    assert "运行本身成功" in out["cause"]


# ------------------------------------------------ 战役二：工具调用上限
def test_tool_cap_counts_and_reports_chinese(env):
    st = {}
    for i in range(5):
        assert runner_acp._tool_cap_check(st, "web_search") is None, i
    err = runner_acp._tool_cap_check(st, "web_search")
    assert err and "网络检索次数超限" in err and "5" in err

    st2 = {}
    for i in range(3):
        assert runner_acp._tool_cap_check(st2, "web_fetch") is None
    err2 = runner_acp._tool_cap_check(st2, "web_fetch")
    assert err2 and "网页抓取次数超限" in err2

    # 非网络工具不计数
    st3 = {}
    for _ in range(20):
        assert runner_acp._tool_cap_check(st3, "pwsh") is None
    assert st3.get("search_calls") in (None, 0)


def test_tool_cap_limits_are_configurable(env, monkeypatch):
    monkeypatch.setenv("M0_MAX_SEARCH_CALLS", "2")
    st = {}
    assert runner_acp._tool_cap_check(st, "web_search") is None
    assert runner_acp._tool_cap_check(st, "web_search") is None
    err = runner_acp._tool_cap_check(st, "web_search")
    assert err and "2" in err


def test_tool_budget_is_stated_in_the_prompt(env):
    block = runner_acp._TOOL_BUDGET_BLOCK.format(search=5, fetch=3)
    assert "web_search 5 次" in block and "抓取网页 3 次" in block
    assert "中断" in block


def test_tool_cap_failure_has_a_chinese_diagnosis(env):
    conn, _ = env
    conn.execute("INSERT OR REPLACE INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
                 " VALUES ('T-CAP','LE-GROUP-HQ','TENANT-HQ','content_write','检索','failed')")
    conn.execute("INSERT OR REPLACE INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,"
                 "state,error) VALUES ('RUN-CAP','T-CAP',1,'LE-GROUP-HQ','TENANT-HQ',"
                 "'failed','tool_cap_exceeded')")
    conn.commit()
    out = flows.diagnose(conn, "RUN-CAP", use_llm=False)
    assert "工具调用超限" in out["cause"]
    assert any("基于已有资料" in f for f in out["fixes"])


# ------------------------------------------------ 战役三：复合流（多标签）
def test_multi_label_creates_composite_flow_and_pulls_two_teams(env, monkeypatch):
    conn, ceo = env
    fake_llm(monkeypatch, {"task_type": "software", "confidence": 0.88,
                           "secondary": "analysis",
                           "reason": "要先开发再产出分析报告"})
    plan = flows.plan(conn, "开发一个黄金监控并分析的系统", [])
    keys = [n["key"] for n in plan["nodes"]]
    assert plan["kind"] == "composite", plan
    assert keys[0] == "intake" and "build" in keys
    assert "an_retrieve" in keys and "an_author" in keys, "副流程节点要有前缀，不能撞车"
    assert keys.index("build") < keys.index("an_analyze"), "先开发后分析"

    res = dispatcher.route(conn, "开发一个黄金监控并分析的系统", actor_id=ceo["id"])
    tags = {r["tag"] for r in conn.execute(
        """SELECT r.tag FROM chat_members m JOIN ai_employees e ON e.id=m.employee_id
           JOIN role_templates r ON r.id=e.role_id WHERE m.chat_id=?""",
        (res["chat_id"],))}
    assert {"dev", "data"} <= tags, tags
    stages = [s["key"] for s in flows.nodes(conn, res["project_id"])]
    assert stages == keys
