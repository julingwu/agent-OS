"""v5.4.2 TASK-2: complete() 的阶段 overhead 记账回路。

v5.4.1 的事实：`llm.complete()` 把 API 返回的 usage 丢了（或只累加进模块全局计数），
立项书 / 研讨 / WBS / 复盘这些**阶段内**的模型调用在 FinOps 上一律记 0 —— 成本黑洞。
这里验证 v5.4.2 的四件事：
1. usage sink 存在 contextvars 里，不再被别的线程覆盖；
2. 阶段执行处注册 sink 后，complete() 的真实 token 会累加进 stage_budgets.spent，
   并写一条 kind='stage_overhead' 的 budget_events（80% 预算检查因此覆盖到它）；
3. finops.overview 的口径能分列出 task_tokens / overhead_tokens；
4. 没有 project_id 的调用（立项前意图路由等）安全忽略，绝不阻塞主流程。
"""
from __future__ import annotations

import threading

import pytest

from app import finance, finops, llm, pipeline
from app.db import connect


def _fake_complete(tokens_in: int = 700, tokens_out: int = 300, text: str = "# 文档\n正文"):
    """替身：走真实 _record_usage，所以 sink 记账链路是真的。

    研讨会那几处调用要的是 JSON（question/risk/stance），返回合法 JSON 才能让
    阶段跑到收尾（否则会走「模型不可用 ⇒ 审计/法务反对 ⇒ 一票否决」分支）。
    """
    usage = {"prompt_tokens": tokens_in, "completion_tokens": tokens_out}

    def _call(prompt, system=None, **kwargs):
        llm._record_usage("complete", usage)
        if "stance" in (prompt or ""):
            return ('{"question": "预算与边界如何界定？",'
                    ' "risk": "范围蔓延导致阶段超支",'
                    ' "stance": "有条件同意"}'), usage
        return text, usage

    return _call


def _advance_to(conn, pid: str, key: str, requirement: str) -> None:
    """按 seq 顺序跑到目标阶段之前（trg_stage_no_skip 不许跳阶段）。"""
    order = [k for k, _ in pipeline.STAGES]
    for stage_key in order[:order.index(key)]:
        pipeline.run_stage(conn, pid, stage_key, requirement)


@pytest.fixture()
def project():
    conn = connect()
    pipeline.ensure(conn)
    finance.ensure(conn)
    created = pipeline.create_project(conn, name="overhead-验证",
                                      requirement="开发一个计算器页面并上线",
                                      entity="LE-GROUP-HQ", use_llm_docs=1)
    pid = created["project_id"]
    nodes = [{"key": k, "kind": k, "role": "engineer", "title": t} for k, t in pipeline.STAGES]
    finance.open_project(conn, pid, nodes, requirement="开发一个计算器页面并上线",
                         flow_id="")
    yield conn, pid
    conn.close()


def _spent(conn, pid: str, stage_key: str) -> int:
    return int(finance.quota(conn, pid, stage_key)["spent"] or 0)


def _overhead_events(conn, pid: str, stage_key: str | None = None) -> list[dict]:
    sql = "SELECT * FROM budget_events WHERE project_id=? AND kind='stage_overhead'"
    args: list = [pid]
    if stage_key is not None:
        sql += " AND stage_key=?"
        args.append(stage_key)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]


# ------------------------------------------------------------------ sink 隔离
def test_usage_sink_is_context_local_not_global():
    main_hits: list[dict] = []
    llm.set_usage_sink(lambda kind, usage: main_hits.append(usage))
    try:
        worker_hits: list[dict] = []

        def _worker() -> None:
            llm.set_usage_sink(lambda kind, usage: worker_hits.append(usage))
            llm._record_usage("complete", {"prompt_tokens": 3, "completion_tokens": 4})

        t = threading.Thread(target=_worker)
        t.start()
        t.join()
        assert len(worker_hits) == 1
        assert main_hits == [], "子线程注册的 sink 绝不能覆盖/触发主线程的 sink"
        llm._record_usage("complete", {"prompt_tokens": 10, "completion_tokens": 20})
        assert len(main_hits) == 1 and main_hits[0]["prompt_tokens"] == 10
    finally:
        llm.set_usage_sink(None)


def test_usage_sink_context_manager_restores_previous():
    outer = lambda kind, usage: None                    # noqa: E731
    inner = lambda kind, usage: None                    # noqa: E731
    llm.set_usage_sink(outer)
    try:
        with llm.usage_sink(inner):
            assert llm.current_usage_sink() is inner
        assert llm.current_usage_sink() is outer
    finally:
        llm.set_usage_sink(None)


def test_sink_exception_never_blocks_generation(monkeypatch):
    def _boom(kind, usage):
        raise RuntimeError("账本挂了也不能影响生成")

    monkeypatch.setattr(llm, "complete_with_usage", _fake_complete())
    with llm.usage_sink(_boom):
        assert llm.complete("写点东西") == "# 文档\n正文"


# ------------------------------------------------------- 阶段 overhead 真正入账
def test_charter_complete_tokens_land_in_stage_budget(project, monkeypatch):
    conn, pid = project
    req = "开发一个计算器页面并上线"
    monkeypatch.setattr(llm, "complete_with_usage", _fake_complete(700, 300))
    _advance_to(conn, pid, "charter", req)
    before = _spent(conn, pid, "charter")

    pipeline.run_stage(conn, pid, "charter", req)

    assert _spent(conn, pid, "charter") - before == 1000, "complete() 的 1000 token 必须记进阶段"
    events = _overhead_events(conn, pid, "charter")
    assert len(events) == 1
    assert events[0]["tokens"] == 1000
    assert events[0]["actor"] == "llm.complete"
    assert "prompt_tokens" in (events[0]["detail"] or "")


def test_workshop_complete_tokens_are_all_captured(project, monkeypatch):
    """研讨会一轮 = 4 个角色质询 + 1 份纪要，5 次 complete 都要算进去。"""
    conn, pid = project
    req = "开发一个计算器页面并上线"
    monkeypatch.setattr(llm, "complete_with_usage", _fake_complete(200, 100))
    _advance_to(conn, pid, "workshop", req)
    before = _spent(conn, pid, "workshop")

    pipeline.run_stage(conn, pid, "workshop", req)

    delta = _spent(conn, pid, "workshop") - before
    events = _overhead_events(conn, pid, "workshop")
    assert len(events) == 1
    assert delta == events[0]["tokens"] > 0
    assert delta % 300 == 0 and delta >= 1500, f"5 次 × 300 token 应全被捕获，实际 {delta}"


def test_pause_threshold_sees_stage_overhead(project, monkeypatch):
    """80% 预算检查必须把 overhead 算进去（v5.4.1 里这部分消耗是隐形的）。"""
    conn, pid = project
    quota = finance.quota(conn, pid, "intake")["quota"]
    tokens = max(1000, quota // 2)
    monkeypatch.setattr(llm, "complete_with_usage",
                        _fake_complete(tokens, 0, text="# 需求登记"))

    pipeline.run_stage(conn, pid, "intake", "开发一个计算器页面并上线")

    row = finance.quota(conn, pid, "intake")
    assert int(row["spent"]) >= tokens
    head = conn.execute("SELECT spent_tokens FROM project_budgets WHERE project_id=?",
                        (pid,)).fetchone()
    assert int(head["spent_tokens"]) >= tokens, "项目池总消耗也要同步累加"


def test_project_level_overhead_without_stage(project, monkeypatch):
    """pm_summary 这类没有阶段上下文的调用 ⇒ 记到项目级（stage_key=None）。"""
    conn, pid = project
    monkeypatch.setattr(llm, "complete_with_usage", _fake_complete(120, 80))
    head_before = int(conn.execute(
        "SELECT spent_tokens FROM project_budgets WHERE project_id=?", (pid,)).fetchone()[0])

    with finance.overhead_scope(conn, pid):
        llm.complete("把阶段交付压成一段中文汇总")

    assert int(conn.execute("SELECT spent_tokens FROM project_budgets WHERE project_id=?",
                            (pid,)).fetchone()[0]) - head_before == 200
    events = _overhead_events(conn, pid)
    assert len(events) == 1 and events[0]["stage_key"] is None


def test_no_project_context_is_safely_ignored(monkeypatch):
    """立项前的意图路由：没有 project_id，绝不能报错，也不能写脏账。"""
    conn = connect()
    try:
        monkeypatch.setattr(llm, "complete_with_usage", _fake_complete(50, 50))
        with finance.overhead_scope(conn, None):
            assert llm.complete("这条需求应该走哪个流程？") == "# 文档\n正文"
        assert conn.execute("SELECT COUNT(*) FROM budget_events WHERE kind='stage_overhead'"
                            ).fetchone()[0] == 0
    finally:
        conn.close()


# --------------------------------------------------------------- FinOps 口径
def test_finops_overview_splits_task_and_overhead(project, monkeypatch):
    conn, pid = project
    req = "开发一个计算器页面并上线"
    monkeypatch.setattr(llm, "complete_with_usage", _fake_complete(600, 400))
    _advance_to(conn, pid, "charter", req)
    pipeline.run_stage(conn, pid, "charter", req)

    # 再补一条「任务执行」用量，验证两个口径是分列而不是混在一起
    task = conn.execute("SELECT id FROM tasks LIMIT 1").fetchone()
    conn.execute("INSERT INTO tasks (id,title,state,legal_entity_id,tenant_id)"
                 " VALUES ('T-OH','任务','completed','LE-GROUP-HQ','TENANT-HQ')"
                 " ON CONFLICT(id) DO NOTHING")
    task_id = task["id"] if task is not None else "T-OH"
    conn.execute("INSERT INTO task_runs (id,task_id,state,run_seq,usage_tokens,"
                 "legal_entity_id,tenant_id,started_at)"
                 " VALUES ('R-OH',?,'succeeded',1,5000,'LE-GROUP-HQ','TENANT-HQ',"
                 "CURRENT_TIMESTAMP)", (task_id,))
    conn.commit()

    ov = finops.overview(conn, days=30)
    assert ov["totals"]["overhead_tokens"] >= 1000
    assert ov["totals"]["task_tokens"] >= 5000
    assert ov["totals"]["tokens"] == ov["totals"]["task_tokens"] + ov["totals"]["overhead_tokens"]
    assert ov["overhead"]["source"] == "budget_events.kind='stage_overhead'"
    stages = {s["stage_key"]: s for s in ov["overhead"]["by_stage"]}
    assert stages["charter"]["tokens"] >= 1000
    assert stages["charter"]["project_id"] == pid

    tenant_ov = finops.overview(conn, days=30, tenant_id="TENANT-UNKNOWN")
    assert tenant_ov["totals"]["overhead_tokens"] == 0, "租户过滤必须对 overhead 也生效"
