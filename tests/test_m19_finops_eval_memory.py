"""M19：FinOps 人民币看板 / 离线评测回归 / 分层组织经验（v2.4）。"""
from __future__ import annotations

import json
import pathlib

import pytest

from app import evals, experience, finops
from app.db import get_conn, init_db


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "m19.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_SANDBOX_ACP", "0")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("EVAL_JUDGE", "0")
    monkeypatch.setenv("MODEL_PRICES_JSON",
                       '{"deepseek-chat":{"in":2,"out":8},"test-model":{"in":10,"out":30}}')
    monkeypatch.setenv("FINOPS_INPUT_RATIO", "0.75")
    init_db()
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type)"
                  " VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type,parent_entity_id)"
                  " VALUES ('LE-FIN','财务部','department','LE-GROUP-HQ')")
        c.execute("""INSERT OR IGNORE INTO ai_employees
                     (id, legal_entity_id, name, model, status, preset)
                     VALUES ('AI-QA-01','LE-FIN','小财','test-model','online','standard')""")
        c.commit()
        yield c


def _task(conn, title="写一篇秋季护肤短文", pack="content-v1") -> str:
    from app.audit import new_id
    tid = new_id("T")
    conn.execute("""INSERT INTO tasks (id, legal_entity_id, task_type, title, state,
                                      owner_employee_id, pack_id)
                    VALUES (?, 'LE-FIN', 'content_write', ?, 'todo', 'AI-QA-01', ?)""",
                 (tid, title, pack))
    conn.commit()
    return tid


def _run(conn, task_id, usage=1_000_000) -> str:
    from app.api import verbs
    from app.models import SpawnRunIn
    out = verbs.do_spawn_run(
        conn, SpawnRunIn(task_id=task_id, run_seq=1, legal_entity_id="LE-FIN"),
        ident={"id": "ID-test", "role": "ceo", "legal_entity_id": "LE-FIN"})
    rid = out["run_id"]
    conn.execute("UPDATE task_runs SET usage_tokens=?, usage_baseline=0,"
                 " state='succeeded' WHERE id=?", (usage, rid))
    conn.commit()
    return rid


def test_finops_maps_tokens_to_cny(conn):
    tid = _task(conn)
    _run(conn, tid, usage=1_000_000)
    data = finops.overview(conn, days=30)
    # 75% 输入 * 10 + 25% 输出 * 30 = 15 元 / 百万 token
    assert data["totals"]["tokens"] == 1_000_000
    assert data["totals"]["cost"] == 15.0
    assert data["by_agent"][0]["agent_name"] == "小财"
    assert data["by_dept"][0]["dept_name"] == "财务部"
    assert data["by_model"][0]["model"] == "test-model"
    assert data["pricing"]["input_ratio"] == 0.75
    assert len(data["series"]) == 30


def test_eval_baseline_and_mock_regression(conn, monkeypatch):
    from app.engine import dsh_adapter

    tid = _task(conn)
    rid = _run(conn, tid, usage=1000)
    out = dsh_adapter.execute_run(conn, rid, prompt="写护肤短文", runner="mock_acp",
                                  budget_tokens=4000)
    assert out["exit_code"] == 0
    s = evals.create_from_run(conn, rid, name="护肤基准", by="ID-test")
    assert s["task_title"] and s["output"]
    assert json.loads(s["criteria_json"]) == []

    result = evals.run_case(conn, s["id"], runner="mock_acp", budget_tokens=4000)
    assert result["state"] == "ok", result
    assert result["similarity"] == 100.0, result
    assert result["score"] == 100.0, result

    bid = evals.start_regression(conn, set_ids=[s["id"]], runner="mock_acp")
    done = evals.execute_regression(conn, bid, set_ids=[s["id"]], runner="mock_acp")
    assert done["state"] == "done"
    assert done["avg_score"] == 100.0
    detail = evals.batch_detail(conn, bid)
    assert detail["results"][0]["set_name"] == "护肤基准"


def test_experience_capture_and_injection(conn, monkeypatch):
    e = experience.add(conn, kind="rejection", title="驳回：记账小程序",
                       content="任务《记账小程序》被驳回：金额精度判据未过。下次先做金额四舍五入与负数校验。",
                       tags=["dev", "记账"], source_type="task", source_id="T-old",
                       task_id=None)
    assert e["id"].startswith("EXP")
    ctx = experience.context_for(conn, "开发一个记账小程序，要求金额精确", record=False)
    assert ctx["count"] >= 1
    assert "金额精度" in ctx["text"]

    from app import pipeline
    proj = pipeline.create_project(conn, name="记账 2.0", requirement="开发一个记账小程序",
                                   entity="LE-FIN")
    seen = {}

    def fake_stage(conn, pid, key, requirement):
        seen.setdefault("req", requirement)

    monkeypatch.setattr(pipeline, "run_stage", fake_stage)
    pipeline.run_project(conn, proj["project_id"], "开发一个记账小程序")
    assert "【组织经验（自动注入）】" in seen["req"]
    assert "金额精度" in seen["req"]
    row = conn.execute("SELECT * FROM ai_audit_logs WHERE action='experience_injected'"
                       " ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row is not None and proj["project_id"] in (row["target"] or "")
    stats = experience.stats(conn)
    assert stats["total"] >= 1 and stats["layers"]["org_experiences"] >= 1
