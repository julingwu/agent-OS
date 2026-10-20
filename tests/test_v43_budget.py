"""V4.3 动态财务预算引擎验收。

三条主线：
1. 财务 Agent 逐阶段估 base，按弹性系数（默认 1.5）加 50% 缓冲 → 阶段额度；
   项目池 = 各阶段额度之和，超过全局上限时等比压缩（安全锁）。
2. 阶段花到 80% 必须暂停成「要钱」状态（BudgetPause），不是继续烧；
   批准从池子未花余额划拨，池子见底项目停摆（BudgetHalt）。
3. 设置页的弹性系数 / 全局上限走接口可改，越界拒绝。
"""
from __future__ import annotations

import pytest

from app import finance, flows, identity, pipeline
from app.db import connect, init_db
from app.engine import dsh_adapter
from app.ui import roles, store


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for key, value in {
        "DB_PATH": str(tmp_path / "v43.db"),
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "M0_INTENT_LLM": "0",
        "UI_SESSION_SECRET": "v43-test-only-secret",
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("M0_BUDGET_ELASTIC", "M0_PROJECT_BUDGET_CAP", "M0_BUDGET_PAUSE_RATIO"):
        monkeypatch.delenv(key, raising=False)
    init_db()
    conn = connect(str(tmp_path / "v43.db"))
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    roles.ensure(conn)
    store.ensure(conn)
    pipeline.ensure(conn)
    flows.ensure(conn)
    finance.ensure(conn)
    from app import trace
    trace.ensure(conn)
    from app import notify
    notify.ensure(conn)
    ceo = identity.issue(conn, name="CEO", role="ceo")
    conn.commit()
    yield conn, ceo
    conn.close()


ANALYSIS_NODES = [
    {"key": "retrieve", "title": "资料检索", "role": "research", "kind": "retrieve"},
    {"key": "analyze", "title": "数据分析", "role": "data", "kind": "analyze"},
    {"key": "author", "title": "报告撰写", "role": "data", "kind": "author"},
    {"key": "review", "title": "审核", "role": "audit", "kind": "review"},
    {"key": "deliver", "title": "交付", "role": "platform", "kind": "deliver"},
]


def _project(conn, name="黄金分析", owner=None):
    proj = pipeline.create_project(conn, name=name, requirement="帮我瞧瞧今天金子啥情况",
                                   entity="LE-GROUP-HQ", owner=owner, flow=None)
    return proj["project_id"]


# ------------------------------------------------------------ 战役一：估算
def test_estimate_adds_50_percent_buffer(env):
    req = "帮我瞧瞧今天金子啥情况，顺便给个短线判断、关键价位和风险提示，我要拿去和同事讨论"
    rows = finance.estimate(ANALYSIS_NODES, req, "FLOW-ANALYSIS")
    retrieve = next(r for r in rows if r["stage_key"] == "retrieve")
    assert retrieve["base_estimate"] == 30000
    assert retrieve["buffer_pct"] == 50
    assert retrieve["quota"] == 45000          # 30000 × 1.5
    deliver = next(r for r in rows if r["stage_key"] == "deliver")
    assert deliver["quota"] >= finance.MIN_STAGE_QUOTA


def test_pool_is_the_sum_of_stage_quotas(env):
    conn, _ = env
    pid = _project(conn)
    ov = finance.open_project(conn, pid, ANALYSIS_NODES, requirement="帮我瞧瞧今天金子啥情况",
                              flow_id="FLOW-ANALYSIS")
    assert ov["pool"] == sum(s["quota"] for s in ov["stages"])
    assert ov["pool"] > 18000 and len(ov["stages"]) == 5
    assert ov["state"] == "active" and ov["remaining"] == ov["pool"]


def test_project_cap_scales_quotas_down(env):
    conn, _ = env
    finance.set_config(conn, cap=50000)
    pid = _project(conn, "开发一个黄金监控并分析的系统")
    nodes = ANALYSIS_NODES * 3                     # 15 个阶段，天然超上限
    ov = finance.open_project(conn, pid, nodes, requirement="开发一个黄金监控并分析的系统",
                              flow_id="FLOW-SOFTWARE")
    assert ov["capped"] == 1
    assert ov["pool"] <= 50000
    assert all(s["quota"] >= finance.MIN_STAGE_QUOTA for s in ov["stages"])


# ------------------------------------------------------------ 战役二：暂停/追加
def test_reaching_80_percent_opens_one_approval_and_pauses(env):
    conn, _ = env
    pid = _project(conn)
    finance.open_project(conn, pid, ANALYSIS_NODES, flow_id="FLOW-ANALYSIS")
    first = finance.request_topup(conn, pid, "retrieve", reason="到 80%")
    second = finance.request_topup(conn, pid, "retrieve", reason="到 80%")
    assert first["id"] == second["id"], "同一阶段不应刷出多张待批卡"
    assert finance.quota(conn, pid, "retrieve")["state"] == "awaiting_budget"
    assert finance.overview(conn, pid)["state"] == "paused"
    assert any(a["id"] == first["id"] for a in finance.pending(conn))


def test_approval_transfers_from_the_pool(env):
    conn, _ = env
    pid = _project(conn)
    ov0 = finance.open_project(conn, pid, ANALYSIS_NODES, flow_id="FLOW-ANALYSIS")
    before = finance.quota(conn, pid, "retrieve")["quota"]
    ap = finance.request_topup(conn, pid, "retrieve")
    out = finance.decide(conn, ap["id"], approve=True, actor="CEO")
    assert out["ok"] and out["state"] == "approved"
    assert out["granted"] == ap["requested_tokens"]
    after = finance.overview(conn, pid)
    assert finance.quota(conn, pid, "retrieve")["quota"] == before + ap["requested_tokens"]
    assert after["pool"] == ov0["pool"], "划拨只改分配，不改池子总量"
    assert after["state"] == "active"
    assert finance.pending(conn) == []


def test_pool_exhausted_halts_the_project(env):
    conn, _ = env
    pid = _project(conn)
    ov = finance.open_project(conn, pid, ANALYSIS_NODES, flow_id="FLOW-ANALYSIS")
    finance.spend(conn, pid, "retrieve", ov["pool"])          # 池子花光
    ap = finance.request_topup(conn, pid, "retrieve")
    out = finance.decide(conn, ap["id"], approve=True, actor="CEO")
    assert out["ok"] is False and out["state"] == "exhausted"
    assert finance.overview(conn, pid)["state"] == "halted"
    stage = finance.quota(conn, pid, "retrieve")
    assert stage["quota"] == finance.quota(conn, pid, "retrieve")["quota"]  # 没白给额度
    with pytest.raises(finance.BudgetHalt):
        pipeline._flow_execute(conn, pid, ANALYSIS_NODES[0], "帮我瞧瞧今天金子啥情况")


def test_reject_keeps_the_project_paused(env):
    conn, _ = env
    pid = _project(conn)
    finance.open_project(conn, pid, ANALYSIS_NODES, flow_id="FLOW-ANALYSIS")
    ap = finance.request_topup(conn, pid, "retrieve")
    out = finance.decide(conn, ap["id"], approve=False, actor="CEO")
    assert out["state"] == "rejected"
    assert finance.overview(conn, pid)["state"] == "paused"


# ------------------------------------------------------------ 底座接线
def test_flow_execute_reports_spend_and_pauses(env, monkeypatch):
    conn, ceo = env
    pid = _project(conn, owner=ceo["id"])
    finance.open_project(conn, pid, ANALYSIS_NODES, flow_id="FLOW-ANALYSIS")
    from app.engine import task_generator
    from app.ui import roles as ui_roles
    monkeypatch.setattr(ui_roles, "pick", lambda *a, **k: [{"id": "EMP-1", "name": "研究员"}])
    monkeypatch.setattr(task_generator, "generate",
                        lambda *a, **k: {"task_id": "T-V43"})
    conn.execute("INSERT OR REPLACE INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
                 " VALUES ('T-V43','LE-GROUP-HQ','TENANT-HQ','content_write','检索','assigned')")
    conn.commit()
    monkeypatch.setattr(dsh_adapter, "execute_run",
                        lambda *a, **k: {"usage_tokens": 15000, "reason": "budget_pause",
                                         "budget_pause": {"used": 15000}, "task_state": "running"})
    with pytest.raises(finance.BudgetPause) as caught:
        pipeline._flow_execute(conn, pid, ANALYSIS_NODES[0], "帮我瞧瞧今天金子啥情况")
    assert caught.value.approval_id
    assert finance.quota(conn, pid, "retrieve")["spent"] == 15000
    assert finance.quota(conn, pid, "retrieve")["state"] == "awaiting_budget"
    assert finance.overview(conn, pid)["spent"] == 15000


def test_run_project_pauses_instead_of_blocking(env, monkeypatch):
    conn, _ = env
    pid = _project(conn)
    conn.execute("DELETE FROM project_stages WHERE project_id=?", (pid,))
    for i, node in enumerate(ANALYSIS_NODES, start=1):
        conn.execute("INSERT INTO project_stages(id,project_id,seq,key,title,state)"
                     " VALUES (?,?,?,?,?,'pending')",
                     (f"PS-V43-{i}", pid, i, node["key"], node["title"]))
    conn.commit()
    finance.open_project(conn, pid, ANALYSIS_NODES, flow_id="FLOW-ANALYSIS")

    def fake_stage(conn_, pid_, key, requirement):
        if key == "analyze":
            ap = finance.request_topup(conn_, pid_, key, reason="80%")
            raise finance.BudgetPause("暂停", ap["id"], key, ap["requested_tokens"])
        # 其它阶段：不真的跑（状态由断言不关心），只保证不抛异常

    monkeypatch.setattr(pipeline, "run_stage", fake_stage)
    out = pipeline.run_project(conn, pid, "帮我瞧瞧今天金子啥情况")
    assert out.get("paused") == "awaiting_budget"
    assert conn.execute("SELECT state FROM projects WHERE id=?", (pid,)).fetchone()["state"] == "paused"
    assert conn.execute("SELECT state FROM project_stages WHERE project_id=? AND key='analyze'",
                        (pid,)).fetchone()["state"] == "awaiting_budget"
    assert out["approval_id"]


# ------------------------------------------------------------ 设置接口
def test_settings_expose_elastic_and_cap(env):
    conn, ceo = env
    from app.main import app
    from app.ui import api as ui_api
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        client.cookies.set("oa_ui", ui_api._make_cookie(ceo["id"]))
        got = client.get("/ui/settings").json()
        assert got["budget_elastic"] == 1.5
        assert got["project_budget_cap"] == 1_000_000
        assert got["budget_pause_ratio"] == 0.8
        put = client.put("/ui/settings", json={"budget_elastic": 1.8,
                                              "project_budget_cap": 200000})
        assert put.status_code == 200, put.text
        got2 = client.get("/ui/settings").json()
        assert got2["budget_elastic"] == 1.8 and got2["project_budget_cap"] == 200000
        bad = client.put("/ui/settings", json={"budget_elastic": 9})
        assert bad.status_code == 400 and bad.json()["detail"]["code"] == "InvalidElastic"


def test_budget_api_and_resume(env):
    conn, ceo = env
    pid = _project(conn)
    finance.open_project(conn, pid, ANALYSIS_NODES, flow_id="FLOW-ANALYSIS")
    finance.request_topup(conn, pid, "retrieve")
    from app.main import app
    from app.ui import api as ui_api
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        client.cookies.set("oa_ui", ui_api._make_cookie(ceo["id"]))
        lst = client.get("/ui/budgets/approvals").json()["approvals"]
        assert len(lst) == 1 and lst[0]["remaining"] > 0
        decided = client.post(f"/ui/budgets/approvals/{lst[0]['id']}",
                              json={"approve": True}).json()
        assert decided["state"] == "approved"
        ov = client.get(f"/ui/projects/{pid}/budget").json()
        assert ov["pool"] > 0 and ov["stages"]
        detail = client.get(f"/ui/projects/{pid}").json()
        assert detail["budget"]["pool"] == ov["pool"]
