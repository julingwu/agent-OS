"""v5.4 TASK-6.1：L1 人工验收闸严格性。

要证明的四条规则：
1. build/test 只要 L0+L2 全绿就放行，任务停在 pending_review（不是 rejected，也不是 completed）；
2. accept 阶段发现未签批的 blocking L1 就挂起等人工，严禁自动置 passed；
3. 人工带 evidence_snippet 签完所有 blocking L1 后，任务转 completed 并自动唤醒
   流水线走完 release/retro 直至项目 done；
4. 自动签批通道只在 M0_AUTO_SIGN_L1_FOR_TEST=1 时存在，本文件全程关闭它以验证严格路径。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid

import pytest
from starlette.testclient import TestClient

from app import acceptance_run, finance, identity, pipeline
from app.db import connect, init_db
from app.engine import task_generator
from app.main import app
from app.ui import api as ui_api, store as ui_store

GOOD_HTML = (
    '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
    '<title>秋季护肤落地页</title>\n<style>body{margin:0;padding:2rem;background:#fdf7f2;}</style>'
    '</head>\n<body><h1>秋季敏感肌修护</h1>\n'
    '<p>温和补水、修护屏障，换季期减少去角质。</p>\n'
    '<button class="cta" type="button">立即购买</button>\n'
    + "<p>秋季护肤常识：早晚基础护理，注意保湿与防晒。</p>\n" * 26
    + "</body></html>\n"
)


# ---------------------------------------------------------------- 基础夹具
@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "v54.db")
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    for k, v in {
        "DB_PATH": db,
        "M0_WORKSPACE": str(ws),
        "M0_PIPELINE_EXEC": "1",       # 真实验收路径（stub 会跳过整条验收链）
        "UI_RUNNER": "native",         # 强制 NativeKernel，避免拉真实 ACP 子进程
        "M0_NATIVE_MODE": "template",  # 确定性产物，秒级可复现
        "M0_MOCK_LLM": "1",
        "UI_SESSION_SECRET": "v54-secret",
        "WECOM_MOCK": "1",
    }.items():
        monkeypatch.setenv(k, v)
    # 规则 4：显式关掉测试自动签批通道 —— 本文件验证的就是「没有它也必须严格」
    monkeypatch.delenv("M0_AUTO_SIGN_L1_FOR_TEST", raising=False)
    init_db()
    conn = connect(db)
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    pipeline.ensure(conn)
    finance.ensure(conn)
    ui_store.ensure(conn)
    ceo = identity.issue(conn, name="CEO", role="ceo", legal_entity_id="LE-GROUP-HQ")
    conn.commit()
    conn.close()
    boss = TestClient(app)
    boss.cookies.set("oa_ui", ui_api._make_cookie(ceo["id"]))
    return {"boss": boss, "db": db, "ceo": ceo}


def _q(db: str, sql: str, args=()) -> list[sqlite3.Row]:
    conn = connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _signed_post(boss: TestClient, ident: dict, path: str, payload: dict):
    """签批接口要 HMAC 签名，不是只靠 Cookie。签名口径见 app/identity.canonical。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    sig = identity.sign(ident["secret"], "POST", path, "", ts, nonce, body)
    return boss.post(path, content=body, headers={
        "X-OA-Key": ident["id"], "X-OA-Ts": ts, "X-OA-Nonce": nonce,
        "X-OA-Sig": sig, "Content-Type": "application/json"})


def _wait(db: str, sql: str, args=(), want=None, timeout: float = 90.0):
    """轮询直到条件成立，返回最后一行；超时返回 None。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        rows = _q(db, sql, args)
        last = rows[0] if rows else None
        if last is not None and (want is None or last[0] == want):
            return last
        time.sleep(0.4)
    return last


# --------------------------------- 规则 4：自动签批通道的双因子物理熔断
def test_autosign_allowed_inside_pytest_on_temp_db(env, monkeypatch):
    """唯一合法场景：pytest 进程内 + 非生产库。"""
    monkeypatch.setenv("M0_AUTO_SIGN_L1_FOR_TEST", "1")
    conn = connect(env["db"])
    try:
        assert acceptance_run.auto_sign_for_test_enabled(conn) is True
    finally:
        conn.close()


def test_autosign_breaker_trips_outside_pytest(env, monkeypatch):
    """即使变量开着，只要不是在真正的 pytest 进程内，就必须当场熔断。"""
    monkeypatch.setenv("M0_AUTO_SIGN_L1_FOR_TEST", "1")
    monkeypatch.setattr(acceptance_run, "_REAL_PYTEST", object())  # 模拟：非 pytest 进程
    with pytest.raises(RuntimeError, match="SecurityViolation"):
        acceptance_run.auto_sign_for_test_enabled()


def test_autosign_breaker_trips_on_production_database(env, monkeypatch):
    """即使是 pytest，只要目标是生产库 data/oa.db，也必须熔断。

    这里替换的是路径解析函数，**不设置 DB_PATH、也不打开**生产库
    （tests/conftest.py 的 isolate_test_environment 明确禁止 DB_PATH 指向 oa.db）。
    """
    monkeypatch.setenv("M0_AUTO_SIGN_L1_FOR_TEST", "1")
    monkeypatch.setattr("app.db.db_path", lambda: str(acceptance_run._PRODUCTION_DB))
    with pytest.raises(RuntimeError, match="production database"):
        acceptance_run.auto_sign_for_test_enabled()


# -------------------------------------------------- 规则 4：自动签批通道是关着的
def test_auto_sign_channel_is_closed(env):
    assert acceptance_run.auto_sign_for_test_enabled() is False
    conn = connect(env["db"])
    try:
        gen = task_generator.generate(conn, "做一个秋季护肤落地页", pack_id="web-v1")
        tid = gen["task_id"]
        pending = acceptance_run.task_pending_human_signoffs(conn, tid)
        assert pending, "web-v1 判据里应当有 blocking human_check"
        before = len(pending)
        assert acceptance_run.auto_sign_pending_l1(conn, tid) == 0, "通道关闭时不得改动任何判据"
        assert len(acceptance_run.task_pending_human_signoffs(conn, tid)) == before
        conn.commit()
    finally:
        conn.close()


# --------------------------------- 规则 1/2：_flow_verdict 绝不自动签批 L1
def test_flow_verdict_never_autosigns_human_check(env, tmp_path):
    conn = connect(env["db"])
    try:
        gen = task_generator.generate(conn, "做一个秋季护肤落地页，含立即购买 CTA", pack_id="web-v1")
        tid = gen["task_id"]
        art = tmp_path / "index.html"
        art.write_text(GOOD_HTML, encoding="utf-8")
        run_id = "RUN-V54-1"
        conn.execute("INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id, state, runner)"
                     " VALUES (?,?,1,'LE-GROUP-HQ','succeeded','native')", (run_id, tid))
        conn.execute("INSERT INTO artifacts (id, run_id, tenant_id, art_seq, file_path, checksum, bytes)"
                     " VALUES (?,?,'TENANT-HQ',1,?,?,?)",
                     ("ART-V54-1", run_id, str(art), "x", art.stat().st_size))
        conn.commit()

        verdict = pipeline._flow_verdict(conn, tid, run_id, {"key": "build"})
        assert verdict == "pending_review", "未签批的 L1 只能停在 pending_review"

        rows = conn.execute("SELECT check_type, is_blocking, state, note FROM acceptance_criteria"
                            " WHERE task_id=? AND is_blocking=1", (tid,)).fetchall()
        human = [r for r in rows if r["check_type"] == "human_check"]
        assert human and all(r["state"] != "passed" for r in human), \
            "blocking human_check 被自动置 passed —— 这就是伪造签批"
        auto = [r for r in rows if r["check_type"] != "human_check"]
        assert auto and all(r["state"] == "passed" for r in auto), \
            "L0/L2 自动判定应被放行（规则 1）"

        # 已判失败的自动判据：既不许被自动翻盘，也不许被无声放行
        auto_id = conn.execute(
            "SELECT id FROM acceptance_criteria WHERE task_id=? AND is_blocking=1"
            " AND check_type<>'human_check' LIMIT 1", (tid,)).fetchone()["id"]
        conn.execute("UPDATE acceptance_criteria SET state='failed', note='人工判定不达标'"
                     " WHERE id=?", (auto_id,))
        conn.commit()
        with pytest.raises(RuntimeError):
            pipeline._flow_verdict(conn, tid, run_id, {"key": "build"})
        assert conn.execute("SELECT state FROM acceptance_criteria WHERE id=?",
                            (auto_id,)).fetchone()[0] == "failed", \
            "失败的自动判据被自动翻成了 passed"
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------- 规则 2/3：端到端挂起 -> 签批 -> 出货
def test_project_holds_at_accept_until_human_signs_then_finishes(env):
    boss = env["boss"]
    db = env["db"]

    res = boss.post("/ui/projects", json={
        "name": "秋季护肤品牌官网落地页",
        "requirement": "做一个秋季护肤品牌官网落地页，含 Banner、产品卖点与立即购买 CTA",
        "flow": "FLOW-SOFTWARE",
        "use_llm_docs": 0,
    })
    assert res.status_code == 200, res.text
    pid = res.json()["project_id"]

    # --- 规则 1+2：项目必须自己停住，且是停在 accept 等人工，而不是 build 失败
    row = _wait(db, "SELECT state FROM projects WHERE id=?", (pid,), want="awaiting_accept")
    assert row is not None and row[0] == "awaiting_accept", \
        f"项目未停在 awaiting_accept，实际 {row[0] if row else None}"

    acc = _q(db, "SELECT state FROM project_stages WHERE project_id=? AND key='accept'", (pid,))[0]
    assert acc[0] == "awaiting_accept", f"accept 阶段应为 awaiting_accept，实际 {acc[0]}"

    gate_rows = _q(db, "SELECT state FROM stage_gates WHERE project_id=? AND stage_key='accept'"
                       " AND superseded=0", (pid,))
    assert gate_rows and all(r[0] != "passed" for r in gate_rows), \
        "还没人签批，验收闸门不允许是 passed"

    pending = _q(db, "SELECT c.id, c.task_id FROM acceptance_criteria c JOIN tasks t ON t.id=c.task_id"
                     " WHERE t.project_id=? AND c.is_blocking=1 AND c.check_type='human_check'"
                     " AND c.state NOT IN ('passed','na')", (pid,))
    assert pending, "accept 挂起时必然存在未签批的 blocking L1"
    assert all(
        r[0] != "passed"
        for r in _q(db, "SELECT c.state FROM acceptance_criteria c JOIN tasks t ON t.id=c.task_id"
                        " WHERE t.project_id=? AND c.is_blocking=1"
                        " AND c.check_type='human_check'", (pid,)))
    # build 阶段的交付任务不能因为「等人工」就被判失败
    build_stage = _q(db, "SELECT state FROM project_stages WHERE project_id=? AND key='build'",
                     (pid,))[0]
    assert build_stage[0] == "done", f"build 应已完成（L0+L2 全绿即可放行），实际 {build_stage[0]}"

    # --- 签批不许无证据：缺 evidence_snippet 必须 400，且判据状态不变
    cid = pending[0]["id"]
    bad = _signed_post(boss, env["ceo"], f"/criteria/{cid}/check",
                       {"passed": True, "note": "看看"})
    assert bad.status_code == 400, bad.text
    assert _q(db, "SELECT state FROM acceptance_criteria WHERE id=?", (cid,))[0][0] != "passed"

    # --- 规则 3：人工逐条带证据签批，签完自动唤醒收尾
    signed = 0
    for _ in range(40):
        left = _q(db, "SELECT c.id FROM acceptance_criteria c JOIN tasks t ON t.id=c.task_id"
                      " WHERE t.project_id=? AND c.is_blocking=1 AND c.check_type='human_check'"
                      " AND c.state NOT IN ('passed','na')", (pid,))
        if not left:
            break
        for r in left:
            ok = _signed_post(boss, env["ceo"], f"/criteria/{r['id']}/check",
                              {"passed": True, "note": "人工复核通过",
                               "evidence_snippet": "已人工核对交付物：结构与文案符合验收标准"})
            assert ok.status_code == 200, ok.text
            signed += 1
        time.sleep(0.3)
    assert signed >= 1

    done = _wait(db, "SELECT state FROM projects WHERE id=?", (pid,), want="done", timeout=120)
    assert done is not None and done[0] == "done", \
        f"签批完成后项目应自动走到 done，实际 {done[0] if done else None}"

    stages = _q(db, "SELECT key, state FROM project_stages WHERE project_id=? ORDER BY seq", (pid,))
    assert all(s["state"] == "done" for s in stages), \
        [(s["key"], s["state"]) for s in stages]
    keys = [s["key"] for s in stages]
    assert "release" in keys and "retro" in keys, keys
    assert "accept" in keys

    tasks = _q(db, "SELECT state, COUNT(*) n FROM tasks WHERE project_id=? GROUP BY state", (pid,))
    assert all(t["state"] == "completed" for t in tasks), [dict(t) for t in tasks]

    # DoD：签批后自动走完 release 并产出可交付的 zip 包裹
    import zipfile

    bundle = pipeline._wdir(pid) / "release" / "Bundle.zip"
    assert bundle.is_file(), f"release 阶段没有产出 zip 包裹：{bundle}"
    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
        assert zf.testzip() is None, "zip 内容校验失败"
    assert len(names) >= 3, f"zip 里交付物过少：{names}"
    assert any(n.endswith(".md") for n in names), names
    assert bundle.stat().st_size > 200, bundle.stat().st_size

    # 签批必须留下真实人工痕迹与证据
    ev = _q(db, "SELECT COUNT(*) FROM evidence_refs e JOIN acceptance_criteria c"
                " ON c.id=e.owner_id JOIN tasks t ON t.id=c.task_id"
                " WHERE t.project_id=? AND c.check_type='human_check'", (pid,))[0][0]
    assert ev >= 1, "人工签批必须落 evidence_refs"
    auto = _q(db, "SELECT COUNT(*) FROM ai_audit_logs WHERE action='l1_auto_signed_for_test'"
                  " AND task_id IN (SELECT id FROM tasks WHERE project_id=?)", (pid,))[0][0]
    assert auto == 0, "严格路径下不允许出现任何自动签批审计"


# ------------------------------------------ 五类 pack 在 L1 闸上行为必须一致
def test_all_packs_share_the_same_l1_gate(env):
    conn = connect(env["db"])
    try:
        packs = ["web-v1", "content-v1", "code-v1", "code-local-v1", "review-v1",
                 "finance-v1", "analyst-v1", "research-v1"]
        checked = 0
        for pack in packs:
            try:
                gen = task_generator.generate(conn, "交付一个落地页与配套文档", pack_id=pack)
            except ValueError:
                continue
            tid = gen["task_id"]
            humans = [r for r in conn.execute(
                "SELECT state, is_blocking FROM acceptance_criteria"
                " WHERE task_id=? AND check_type='human_check'", (tid,)).fetchall()]
            if not humans:
                continue
            checked += 1
            assert all(r["state"] != "passed" for r in humans), \
                f"{pack} 的 human_check 初始不得为 passed"
            assert acceptance_run.auto_sign_pending_l1(conn, tid) == 0, \
                f"{pack} 在严格模式下不得被自动签批"
            conn.commit()
        assert checked >= 3, f"至少应覆盖 3 个含 L1 判据的 pack，实际 {checked}"
    finally:
        conn.close()
