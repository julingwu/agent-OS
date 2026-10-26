"""v5.2 TASK-3.2 验收：阶段内只重跑未完成任务 + blocked 项目一键自愈的底座。

场景（DoD 原文）：
    模拟阶段内第 1 个任务成功、第 2 个任务触发 80% 预算暂停，
    划拨恢复后第 1 个任务不再重复执行。

断言口径：
    * 第 1 个任务的 task_runs 记录数不增加（执行次数不变）；
    * 恢复只新增第 2 个任务的一次 Run；
    * 第 1 个任务在 project_artifacts 黑板上的产物被直接复用；
    * 追加额度按阶段当前额度的 30% 给出（一键自愈按钮用同一个数）。
"""
from __future__ import annotations

import uuid

import pytest

from app import acceptance_run, finance, identity, pipeline
from app.db import connect, init_db
from app.engine import dsh_adapter, task_generator

PROJECT_REQ = "做一个产品官网落地页（HTML），并撰写发布文案"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "v52.db"
    for key, value in {
        "DB_PATH": str(db),
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "M0_PIPELINE_EXEC": "1",
        "M0_DISPATCH_SYNC": "1",
        "M0_INTENT_LLM": "0",
        "UI_SESSION_SECRET": "v52-test-only-secret",
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("M0_BUDGET_ELASTIC", "M0_PROJECT_BUDGET_CAP", "M0_BUDGET_PAUSE_RATIO"):
        monkeypatch.delenv(key, raising=False)
    init_db()
    conn = connect(str(db))
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    from app import notify, trace
    from app.ui import roles, store
    roles.ensure(conn)
    store.ensure(conn)
    pipeline.ensure(conn)
    finance.ensure(conn)
    trace.ensure(conn)
    notify.ensure(conn)
    conn.execute("INSERT OR IGNORE INTO positions(id,legal_entity_id,title)"
                 " VALUES ('POS-DEV-1','LE-GROUP-HQ','开发工程师')")
    conn.execute("INSERT OR IGNORE INTO ai_employees(id,legal_entity_id,name,position_id,"
                 "status) VALUES ('EMP-DEV-1','LE-GROUP-HQ','开发一号','POS-DEV-1','online')")
    ceo = identity.issue(conn, name="CEO", role="ceo")
    conn.commit()
    yield conn, ceo
    conn.close()


def _patch_runtime(monkeypatch, tmp_path):
    """只替换「外部执行 + 派单对象」，其余身份/状态机/审计保持真实实现。

    第 1 次 Run：任务成功并落 HTML 产物；
    第 2 次 Run：底座返回 budget_pause（模拟花到 80% 暂停线）；
    第 3 次及以后：重试成功。
    """
    from app import state_machine

    calls: dict = {"runs": [], "per_task": {}, "pause_on": 2}

    def _transition(conn, task_id, event):
        row = conn.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is not None and state_machine.next_state(row["state"], event):
            state_machine.transition(conn, task_id, event, actor="v52-test")

    def fake_execute_run(conn, run_id, **kwargs):
        row = conn.execute("SELECT task_id FROM task_runs WHERE id=?", (run_id,)).fetchone()
        task_id = row["task_id"] if row else None
        calls["runs"].append({"run_id": run_id, "task_id": task_id})
        index = len(calls["runs"])
        _transition(conn, task_id, "run_started")      # assigned -> in_progress
        if index == calls["pause_on"]:
            conn.execute("UPDATE task_runs SET state='failed', exit_code=5,"
                         " usage_tokens=4000, error='budget_pause' WHERE id=?", (run_id,))
            _transition(conn, task_id, "run_failed")   # in_progress -> failed
            conn.commit()
            return {"task_state": "failed", "usage_tokens": 4000,
                    "reason": "budget_pause", "budget_pause": {"used": 4000}}
        art = tmp_path / "deliverables" / f"{task_id}.html"
        art.parent.mkdir(parents=True, exist_ok=True)
        art.write_text(
            "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<title>企业产品官网</title></head><body><h1>企业产品官网</h1>"
            "<p>面向企业客户的产品介绍、在线咨询入口与售后支持说明。</p>"
            "</body></html>\n", encoding="utf-8")
        conn.execute("INSERT INTO artifacts(id,run_id,tenant_id,art_seq,file_path,"
                     "checksum,bytes) VALUES (?,?,?,1,?,?,?)",
                     ("ART-" + uuid.uuid4().hex[:10], run_id, "TENANT-HQ", str(art),
                      "mock", art.stat().st_size))
        conn.execute("UPDATE task_runs SET state='succeeded', exit_code=0,"
                     " usage_tokens=4000 WHERE id=?", (run_id,))
        _transition(conn, task_id, "run_succeeded")    # in_progress -> pending_review
        conn.commit()
        calls["per_task"][task_id] = calls["per_task"].get(task_id, 0) + 1
        return {"task_state": "pending_review", "usage_tokens": 4000,
                "reason": "completed"}

    def fake_accept(conn, task_id, entities):
        _transition(conn, task_id, "review_passed")    # pending_review -> completed
        conn.commit()
        return {"task_state": "completed"}

    def fake_generate(conn, brief, **kwargs):
        tid = "T-V52-" + uuid.uuid4().hex[:8]
        conn.execute("INSERT INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
                     " VALUES (?,?,?,'content_write',?,'todo')",
                     (tid, "LE-GROUP-HQ", "TENANT-HQ", (brief or "")[:60]))
        conn.commit()
        return {"task_id": tid}

    monkeypatch.setattr(dsh_adapter, "execute_run", fake_execute_run)
    monkeypatch.setattr(acceptance_run, "run", fake_accept)
    monkeypatch.setattr(task_generator, "generate", fake_generate)
    monkeypatch.setattr(pipeline, "_execution_actor", lambda *a, **k: {"id": "ID-CEO"})
    monkeypatch.setattr(pipeline, "_build_employee",
                        lambda *a, **k: {"id": "EMP-DEV-1", "position_id": "POS-DEV-1"})
    return calls


def _resume_like_endpoint(conn, pid):
    """复刻 POST /ui/projects/{id}/resume 的数据库动作（不改路由本身）。"""
    keys = [r["key"] for r in conn.execute(
        "SELECT key,state FROM project_stages WHERE project_id=? ORDER BY seq", (pid,))
        if r["state"] != "done"]
    conn.execute("UPDATE project_stages SET state='pending' WHERE project_id=?"
                 " AND state<>'done'", (pid,))
    conn.execute("UPDATE projects SET state='active' WHERE id=?", (pid,))
    finance.set_state(conn, pid, "active")
    conn.commit()
    return keys


def _runs_of(conn, task_id):
    return conn.execute("SELECT COUNT(*) FROM task_runs WHERE task_id=?",
                        (task_id,)).fetchone()[0]


def test_budget_pause_resume_does_not_rerun_completed_task(env, monkeypatch, tmp_path):
    conn, ceo = env
    calls = _patch_runtime(monkeypatch, tmp_path)
    pid = pipeline.create_project(conn, name="双任务交付", requirement=PROJECT_REQ)["project_id"]
    pipeline.build_wbs(conn, pid, PROJECT_REQ)
    codes = [r["code"] for r in conn.execute(
        "SELECT code FROM wbs_items WHERE project_id=? ORDER BY rowid", (pid,)).fetchall()]
    assert codes[:2] == ["T1", "T2"], codes

    # build 阶段的额度池：45,000（implement 30,000 × 弹性 1.5）
    finance.open_project(conn, pid, [
        {"key": "build", "title": "实现", "role": "dev", "kind": "implement"}],
        requirement=PROJECT_REQ, flow_id="FLOW-SOFTWARE")
    quota0 = finance.quota(conn, pid, "build")["quota"]
    assert quota0 >= 45000          # implement 30,000 × 弹性 1.5（网页类还会补正）

    # 先把前置文档阶段跑完（stage seq 必须连续，不能跳阶段）
    warm = pipeline.run_project(conn, pid, PROJECT_REQ,
                                keys=["intake", "charter", "workshop", "wbs"])
    assert warm["stages"] == ["intake", "charter", "workshop", "wbs"]

    first = pipeline.run_project(conn, pid, PROJECT_REQ, keys=["build"])
    assert first["paused"] == "awaiting_budget", first
    assert first["approval_id"]
    t1 = conn.execute("SELECT task_id FROM wbs_items WHERE project_id=? AND code='T1'",
                      (pid,)).fetchone()["task_id"]
    t2 = conn.execute("SELECT task_id FROM wbs_items WHERE project_id=? AND code='T2'",
                      (pid,)).fetchone()["task_id"]
    assert t1 and t2 and t1 != t2
    assert conn.execute("SELECT state FROM wbs_items WHERE project_id=? AND code='T1'",
                        (pid,)).fetchone()["state"] == "done"
    assert _runs_of(conn, t1) == 1
    assert _runs_of(conn, t2) == 1
    assert conn.execute("SELECT state FROM tasks WHERE id=?", (t2,)).fetchone()["state"] == "failed"
    stage_row = conn.execute("SELECT state FROM project_stages WHERE project_id=? AND key='build'",
                             (pid,)).fetchone()
    assert stage_row["state"] == "awaiting_budget"
    # build 阶段的真实消耗必须回写财务账（暂停线才有依据）
    assert finance.quota(conn, pid, "build")["spent"] == 8000

    # 一键自愈：按阶段当前额度追加 30%（前端按钮用的就是这个建议值）
    pending = finance.pending(conn)
    assert len(pending) == 1
    expected = max(1000, int(round(quota0 * finance.TOPUP_RATIO_DEFAULT / 1000.0)) * 1000)
    assert pending[0]["stage_quota"] == quota0
    assert pending[0]["topup_tokens"] == expected
    decided = finance.decide(conn, pending[0]["id"], approve=True, actor=ceo["id"],
                             tokens=pending[0]["topup_tokens"])
    assert decided["state"] == "approved" and decided["granted"] == expected

    # 划拨后恢复：只补未完成任务
    before_total_runs = conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0]
    before_t1_runs = _runs_of(conn, t1)
    before_spent = finance.quota(conn, pid, "build")["spent"]
    keys = _resume_like_endpoint(conn, pid)
    assert keys[0] == "build" and "build" in keys
    resumed = pipeline.run_project(conn, pid, PROJECT_REQ, keys=["build"])
    assert resumed.get("paused") is None, resumed

    # ① 第 1 个任务不再重复执行：run 记录数不增加
    assert _runs_of(conn, t1) == before_t1_runs == 1
    # ② 恢复只新增第 2 个任务的一次 Run（第一次是失败的预算暂停 Run）
    assert conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == before_total_runs + 1
    # ③ 两个任务最终都完成，且 T2 换成了新的（成功）任务
    assert conn.execute("SELECT state FROM wbs_items WHERE project_id=? AND code='T1'",
                        (pid,)).fetchone()["state"] == "done"
    assert conn.execute("SELECT state FROM wbs_items WHERE project_id=? AND code='T2'",
                        (pid,)).fetchone()["state"] == "done"
    t2_now = conn.execute("SELECT task_id FROM wbs_items WHERE project_id=? AND code='T2'",
                          (pid,)).fetchone()["task_id"]
    assert t2_now != t2
    # ④ 恢复只花了重跑任务的钱
    assert finance.quota(conn, pid, "build")["spent"] == before_spent + 4000
    # ⑤ 第 1 个任务的产物已在项目黑板上被复用（不重跑 ACP 也能拿到上下文）
    reused_paths = {r["file_path"] for r in conn.execute(
        "SELECT file_path FROM project_artifacts WHERE project_id=? AND stage_key='build'",
        (pid,)).fetchall()}
    t1_artifact = conn.execute(
        "SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id=a.run_id"
        " WHERE r.task_id=? ORDER BY a.rowid DESC LIMIT 1", (t1,)).fetchone()["file_path"]
    assert t1_artifact in reused_paths
    # ⑥ 审计可回放这次「复用已完成任务」
    actions = {r["action"] for r in conn.execute(
        "SELECT action FROM ai_audit_logs WHERE target=?", (pid,)).fetchall()}
    assert "stage_resume_reused_completed" in actions
    assert "stage_budget_paused" in actions


def test_stage_resume_plan_skips_completed_and_reuses_artifacts(env, tmp_path):
    conn, _ = env
    pid = pipeline.create_project(conn, name="计划", requirement=PROJECT_REQ)["project_id"]
    pipeline.build_wbs(conn, pid, PROJECT_REQ)
    art = tmp_path / "done.md"
    art.write_text("# 已完成模块\n\n该模块已交付并通过验收。\n", encoding="utf-8")
    t_done = "T-PLAN-DONE"
    conn.execute("INSERT INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
                 " VALUES (?,?,?,'content_write','已完成模块','completed')",
                 (t_done, "LE-GROUP-HQ", "TENANT-HQ"))
    conn.execute("INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,state,"
                 "runner,actor_identity) VALUES ('RUN-PLAN-1',?,1,'LE-GROUP-HQ','TENANT-HQ',"
                 "'succeeded','acp','ID-CEO')", (t_done,))
    conn.execute("INSERT INTO artifacts(id,run_id,tenant_id,art_seq,file_path,checksum,bytes)"
                 " VALUES ('ART-PLAN-1','RUN-PLAN-1','TENANT-HQ',1,?, 'mock', ?)",
                 (str(art), art.stat().st_size))
    conn.execute("UPDATE wbs_items SET task_id=? WHERE project_id=? AND code='T1'", (t_done, pid))
    conn.commit()

    plan = pipeline.resumable_stage_plan(conn, pid)
    assert [e["code"] for e in plan["skip"]] == ["T1"]
    assert "T2" in [e["code"] for e in plan["run"]]

    reused = pipeline.reuse_completed_task(conn, pid, t_done, stage_key="build", role_tag="dev")
    assert reused["reused"] is True and reused["artifacts"] == [str(art)]
    row = conn.execute("SELECT file_path,title,stage_key FROM project_artifacts"
                       " WHERE project_id=? AND file_path=?", (pid, str(art))).fetchone()
    assert row is not None and row["stage_key"] == "build"
    # 幂等：再复用一次不会刷出第二条黑板记录，也不会新增 Run
    runs_before = conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0]
    pipeline.reuse_completed_task(conn, pid, t_done, stage_key="build", role_tag="dev")
    assert conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == runs_before
    assert conn.execute("SELECT COUNT(*) FROM project_artifacts WHERE project_id=?"
                        " AND file_path=?", (pid, str(art))).fetchone()[0] == 1


def test_flow_stage_resume_reuses_completed_task_without_new_run(env, monkeypatch, tmp_path):
    conn, _ = env
    node = {"key": "retrieve", "title": "资料检索", "role": "research",
            "kind": "retrieve", "gate": "检索结论可核对", "seq": 1}
    pid = pipeline.create_project(conn, name="流程", requirement="调研一下行业现状",
                                  flow=None)["project_id"]
    art = tmp_path / "retrieve.md"
    art.write_text("# 检索结论\n\n来源：公开资料；结论：行业仍在增长。\n", encoding="utf-8")
    t_done = "T-FLOW-DONE"
    conn.execute("INSERT INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state,"
                 "project_id,stage) VALUES (?,?,?,'content_write','资料检索','completed',?,?)",
                 (t_done, "LE-GROUP-HQ", "TENANT-HQ", pid, node["key"]))
    conn.execute("INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,state,"
                 "runner,actor_identity) VALUES ('RUN-FLOW-1',?,1,'LE-GROUP-HQ','TENANT-HQ',"
                 "'succeeded','acp','ID-CEO')", (t_done,))
    conn.execute("INSERT INTO artifacts(id,run_id,tenant_id,art_seq,file_path,checksum,bytes)"
                 " VALUES ('ART-FLOW-1','RUN-FLOW-1','TENANT-HQ',1,?, 'mock', ?)",
                 (str(art), art.stat().st_size))
    conn.commit()

    def explode(*args, **kwargs):
        raise AssertionError("已完成的任务不应再调用 ACP")

    monkeypatch.setattr(dsh_adapter, "execute_run", explode)
    runs_before = conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0]
    out = pipeline._flow_execute(conn, pid, node, "调研一下行业现状")
    assert out["resumed"] is True and out["state"] == "completed"
    assert out["reused_artifacts"] == [str(art)]
    assert conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0] == runs_before
    restored = conn.execute("SELECT file_path FROM project_artifacts WHERE project_id=?"
                            " AND stage_key=?", (pid, node["key"])).fetchone()
    assert restored is not None and restored["file_path"] == str(art)
