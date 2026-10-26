"""v5.3 TASK-4.2 验收：POST /ui/projects/{id}/resume 支持 stage_keys 精确过滤。

DoD 三件事：
    (a) resume(stage_keys=['build']) 后 build 被续跑；
    (b) 其余非 done 阶段的 state 必须原样保持，不能被误重置成 pending；
    (c) 不传 stage_keys 时行为与旧版完全一致（复位全部非 done 阶段）。

外加两条红线：
    * 传了项目里不存在的 stage key → 400（不静默忽略）；
    * 跳阶段（前一阶段还没 done 就直接续跑后一阶段）→ 409 显式报错，
      触发器 trg_stage_no_skip 保持原样，绝不绕过/关闭。
"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import finance, identity, pipeline
from app.db import connect, init_db
from app.ui import roles, store

REQ = "做一个产品官网落地页（HTML），并撰写发布文案"

# flow=None ⇒ 9 个标准阶段，seq 从 0 开始：intake..build(4)..retro(8)
LEGACY_KEYS = [key for key, _ in pipeline.STAGES]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "v53.db"
    for key, value in {
        "DB_PATH": str(db),
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        # 续跑线程里只想验「传了哪些 key」，执行身份不做真实装配；
        # 阶段执行本身在用例里用真实 enter_stage/finish_stage 顶替（触发器照跑）。
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "M0_INTENT_LLM": "0",
        "UI_SESSION_SECRET": "v53-test-only-secret",
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("M0_BUDGET_ELASTIC", "M0_PROJECT_BUDGET_CAP", "M0_BUDGET_PAUSE_RATIO"):
        monkeypatch.delenv(key, raising=False)
    init_db()
    conn = connect(str(db))
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    from app import notify, trace
    roles.ensure(conn)
    store.ensure(conn)
    pipeline.ensure(conn)
    finance.ensure(conn)
    trace.ensure(conn)
    notify.ensure(conn)
    ceo = identity.issue(conn, name="CEO", role="ceo")
    conn.commit()
    yield conn, ceo
    # 兜底：不许把续跑线程带进下一个用例（它自己开会话，环境变量已换人）
    _settle_resume_threads(timeout=3.0)
    conn.close()


@pytest.fixture()
def client(env):
    from app.main import app
    from app.ui import api as ui_api
    _conn, ceo = env
    with TestClient(app) as c:
        c.cookies.set("oa_ui", ui_api._make_cookie(ceo["id"]))
        yield c


# ------------------------------------------------------------------ helpers
def _project(conn, name="续跑过滤") -> str:
    return pipeline.create_project(conn, name=name, requirement=REQ)["project_id"]


def _mark_done(conn, pid: str, *keys: str) -> None:
    """走真实闸门把阶段置 done —— 不绕过 trg_stage_gate_guard。"""
    for key in keys:
        pipeline.gate(conn, pid, key, f"{key} 阶段验收通过")
        conn.execute("UPDATE project_stages SET state='done'"
                     " WHERE project_id=? AND key=?", (pid, key))
    conn.commit()


def _set_state(conn, pid: str, key: str, state: str) -> None:
    conn.execute("UPDATE project_stages SET state=? WHERE project_id=? AND key=?",
                 (state, pid, key))
    conn.commit()


def _states(conn, pid: str) -> dict[str, str]:
    return {r["key"]: r["state"] for r in conn.execute(
        "SELECT key,state FROM project_stages WHERE project_id=? ORDER BY seq", (pid,))}


def _record_stage(monkeypatch, calls: list[str]) -> None:
    """只记录「被续跑了哪些阶段」，不改任何 DB 状态（用于断言复位粒度）。"""
    def fake_run_stage(_conn, _pid, key, _requirement):
        calls.append(key)
        return {"stage": key}

    monkeypatch.setattr(pipeline, "run_stage", fake_run_stage)


def _real_trigger_stage(monkeypatch, calls: list[str]) -> None:
    """真实走 enter_stage/finish_stage —— trg_stage_no_skip 与闸门守卫照常生效。"""
    def fake_run_stage(conn_, pid_, key, _requirement):
        calls.append(key)
        pipeline.enter_stage(conn_, pid_, key)
        pipeline.gate(conn_, pid_, key, f"{key} 自动闸门")
        pipeline.finish_stage(conn_, pid_, key)
        return {"stage": key}

    monkeypatch.setattr(pipeline, "run_stage", fake_run_stage)


def _wait_until(predicate, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _settle_resume_threads(timeout: float = 8.0) -> bool:
    """等路由拉起的 oa-resume 线程收尾：线程会自己开连接，跑完再进下一个用例，
    避免 daemon 线程带着上一个用例的 DB_PATH 环境变量串到后续用例。"""
    return _wait_until(
        lambda: not any(t.name == "oa-resume" and t.is_alive()
                        for t in threading.enumerate()),
        timeout=timeout)


def _resume_url(pid: str) -> str:
    return f"/ui/projects/{pid}/resume"


# ------------------------------------------------- (a)(b) 精确过滤的核心断言
def test_resume_with_stage_keys_only_touches_requested_stage(env, client, monkeypatch):
    conn, _ = env
    calls: list[str] = []
    _record_stage(monkeypatch, calls)
    pid = _project(conn)
    _mark_done(conn, pid, "intake", "charter", "workshop", "wbs")
    # build 在等预算，另有别的非 done 阶段停在非 pending 状态（旧版会把它们全部拍平）
    _set_state(conn, pid, "build", "awaiting_budget")
    _set_state(conn, pid, "test", "awaiting_budget")
    _set_state(conn, pid, "accept", "blocked")
    before = _states(conn, pid)

    resp = client.post(_resume_url(pid), json={"stage_keys": ["build"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["resuming"] == ["build"], body
    assert body["stage_keys"] == ["build"], body

    # (a) build 被续跑：后台线程只把这一个 key 交给 pipeline.run_project
    assert _wait_until(lambda: calls), f"续跑线程没有推进任何阶段: {calls}"
    assert _settle_resume_threads(), "续跑线程没有收尾"
    assert calls == ["build"], calls
    # 端点把被点名的阶段复位成 pending（阶段执行被 stub，不会再往前走）
    assert _states(conn, pid)["build"] == "pending"

    after = _states(conn, pid)
    # (b) 其余非 done 阶段状态原样保持 —— DoD 的核心断言
    assert after["test"] == "awaiting_budget", after
    assert after["accept"] == "blocked", after
    assert after["release"] == before["release"] == "pending", after
    assert after["retro"] == before["retro"] == "pending", after
    # 已完成阶段也不许被翻回去
    for key in ("intake", "charter", "workshop", "wbs"):
        assert after[key] == "done", after
    # 只有被点名的阶段状态变了
    changed = {k for k in after if after[k] != before[k]}
    assert changed == {"build"}, changed
    # 审计可回放这次「按 key 续跑」
    row = conn.execute("SELECT payload FROM ai_audit_logs WHERE target=? AND"
                       " action='project_resume_scoped'", (pid,)).fetchone()
    assert row is not None and '"build"' in row["payload"], row


# --------------------------------------------- (c) 无 stage_keys 兼容旧行为
def test_resume_without_stage_keys_keeps_legacy_behaviour(env, client, monkeypatch):
    conn, _ = env
    calls: list[str] = []
    _record_stage(monkeypatch, calls)
    pid = _project(conn)
    _mark_done(conn, pid, "intake", "charter", "workshop", "wbs")
    _set_state(conn, pid, "build", "blocked")
    _set_state(conn, pid, "test", "awaiting_budget")
    _set_state(conn, pid, "accept", "blocked")
    legacy_keys = ["build", "test", "accept", "release", "retro"]

    resp = client.post(_resume_url(pid))          # 旧调用方：不带 body / 不带 query
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ok": True, "project_id": pid, "resuming": legacy_keys}, body

    assert _wait_until(lambda: calls), f"续跑线程没有推进任何阶段: {calls}"
    assert _settle_resume_threads(), "续跑线程没有收尾"
    assert calls == legacy_keys, calls
    after = _states(conn, pid)
    # 旧版语义：所有非 done 阶段统一复位成 pending，按 seq 顺序交给 run_project
    for key in legacy_keys:
        assert after[key] == "pending", after
    for key in ("intake", "charter", "workshop", "wbs"):
        assert after[key] == "done", after
    # 旧路径不写新审计动作，避免改变历史调用方的审计流
    assert conn.execute("SELECT COUNT(*) FROM ai_audit_logs WHERE target=? AND"
                        " action='project_resume_scoped'", (pid,)).fetchone()[0] == 0


def test_resume_already_done_short_circuits(env, client, monkeypatch):
    conn, _ = env
    calls: list[str] = []
    _record_stage(monkeypatch, calls)
    pid = _project(conn)
    _mark_done(conn, pid, *LEGACY_KEYS)

    resp = client.post(_resume_url(pid), json={"stage_keys": ["build"]})
    assert resp.status_code == 200 and resp.json() == {"ok": True, "already_done": True}
    # 旧调用方在全部 done 时拿到的也是同一个结构，且不空转线程
    resp2 = client.post(_resume_url(pid))
    assert resp2.status_code == 200 and resp2.json() == {"ok": True, "already_done": True}
    assert _settle_resume_threads(timeout=1.0), "already_done 不该拉起续跑线程"
    assert calls == [], calls


# ------------------------------------------------------------ body / query 形态
def test_resume_stage_keys_accepts_query_param_and_body_wins(env, client, monkeypatch):
    conn, _ = env
    calls: list[str] = []
    _record_stage(monkeypatch, calls)
    pid = _project(conn)
    _mark_done(conn, pid, "intake", "charter", "workshop", "wbs")

    via_query = client.post(_resume_url(pid), params={"stage_keys": "build"})
    assert via_query.status_code == 200, via_query.text
    assert via_query.json()["resuming"] == ["build"], via_query.text

    # 两者都给时以 body 为准：query 说 test，body 说 build ⇒ 只续跑 build
    both = client.post(_resume_url(pid), params={"stage_keys": "test"},
                       json={"stage_keys": ["build"]})
    assert both.status_code == 200, both.text
    assert both.json()["resuming"] == ["build"], both.text
    assert _states(conn, pid)["test"] == "pending", _states(conn, pid)
    assert _settle_resume_threads(), "续跑线程没有收尾"


# --------------------------------------------------------- 明确报错（不静默忽略）
def test_resume_stage_keys_rejects_unknown_key(env, client, monkeypatch):
    conn, _ = env
    calls: list[str] = []
    _record_stage(monkeypatch, calls)
    pid = _project(conn)
    before = _states(conn, pid)

    bad = client.post(_resume_url(pid), json={"stage_keys": ["build", "nope"]})
    assert bad.status_code == 400, bad.text
    detail = bad.json()["detail"]
    assert detail["code"] == "UnknownStageKey"
    assert detail["unknown"] == ["nope"]
    assert _states(conn, pid) == before, "未知 key 时不许动任何阶段状态"
    assert _settle_resume_threads(timeout=1.0), "被拒的请求不该拉起续跑线程"
    assert calls == [], calls


def test_resume_stage_keys_rejects_skipping_unfinished_predecessor(env, client, monkeypatch):
    conn, _ = env
    calls: list[str] = []
    _record_stage(monkeypatch, calls)
    pid = _project(conn)                       # 所有阶段都还是 pending
    before = _states(conn, pid)

    skipped = client.post(_resume_url(pid), json={"stage_keys": ["build"]})
    assert skipped.status_code == 409, skipped.text
    detail = skipped.json()["detail"]
    assert detail["code"] == "StageSkipNotAllowed"
    assert detail["stage"] == "build" and detail["blocked_by"] == "wbs", detail
    assert _states(conn, pid) == before, "被拒的续跑不许先把阶段复位"
    assert _settle_resume_threads(timeout=1.0), "被拒的请求不该拉起续跑线程"
    assert calls == [], calls


def test_resume_stage_keys_rejects_empty_list(env, client):
    conn, _ = env
    pid = _project(conn)
    empty = client.post(_resume_url(pid), json={"stage_keys": []})
    assert empty.status_code == 400, empty.text
    assert empty.json()["detail"]["code"] == "StageKeysEmpty"


# ------------------------------------ 同批续跑连续阶段合法：真触发器不炸 StageSkipped
def test_resume_stage_keys_runs_contiguous_chain_in_seq_order(env, client, monkeypatch):
    conn, _ = env
    calls: list[str] = []
    _real_trigger_stage(monkeypatch, calls)
    pid = _project(conn)
    _mark_done(conn, pid, "intake", "charter", "workshop")

    # 故意倒序传：wbs 必须先于 build 执行，否则 build 会撞 trg_stage_no_skip
    resp = client.post(_resume_url(pid), json={"stage_keys": ["build", "wbs"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["resuming"] == ["wbs", "build"], resp.text

    assert _wait_until(lambda: len(calls) >= 2), f"连续阶段没有跑完: {calls}"
    assert _settle_resume_threads(), "续跑线程没有收尾"
    assert calls == ["wbs", "build"], calls
    after = _states(conn, pid)
    assert after["wbs"] == "done" and after["build"] == "done", after
    # 没被点名的后续阶段不许被顺手推进
    assert after["test"] == "pending" and after["retro"] == "pending", after


def test_trigger_stage_no_skip_still_armed(env):
    """红线守卫：触发器没被摘掉 —— 前置阶段未 done 时置 active 仍然 ABORT。"""
    conn, _ = env
    pid = _project(conn)
    _mark_done(conn, pid, "intake", "charter")
    with pytest.raises(sqlite3.IntegrityError) as excinfo:
        pipeline.enter_stage(conn, pid, "build")
    assert "StageSkipped" in str(excinfo.value), excinfo.value
