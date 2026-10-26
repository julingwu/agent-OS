"""v5.3 TASK-4.1：pack 显式优先级 + eval_runs/eval_results 物理租户隔离。

三条第一性原理：
1. 「web-v1 种子缺失」不成立 —— 种子在 schema.sql，缺的是 eval_runs 的租户物理列；
2. 判据必须跟**调用方显式声明的 pack** 走；role_tag 只是缺省推断（否则页面交付会被
   py_syntax 误杀）；
3. 没绑定任何 eval_sets 的孤儿回归批次，只能靠 eval_runs.tenant_id 物理列隔离。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import evals, identity, pipeline
from app.db import connect, init_db
from app.engine import task_generator
from app.ui.api import _make_cookie


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "v53.db")
    for key, value in {
        "DB_PATH": db_path,
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "UI_SESSION_SECRET": "v53-test-secret",
        "WECOM_MOCK": "1",
    }.items():
        monkeypatch.setenv(key, value)
    init_db()
    conn = connect(db_path)
    conn.execute("INSERT OR IGNORE INTO tenants(id,name) VALUES ('TENANT-B','B')")
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-B','B','group_hq','TENANT-B')")
    hq = identity.issue(conn, name="HQ CEO", role="ceo")
    other = identity.issue(conn, name="B CEO", role="ceo", legal_entity_id="LE-B")
    conn.commit()
    from app.main import app
    with TestClient(app) as client:
        client.cookies.set("oa_ui", _make_cookie(hq["id"]))
        yield conn, {"hq": hq, "b": other}, client, tmp_path
    conn.close()


def _scripts(conn, task_id: str) -> list[str]:
    """判据里引用到的 L2 脚本名，按 seq 排序（与 test_v52_l2_retry_digest 同口径）。"""
    out = []
    for row in conn.execute(
            "SELECT check_type, check_ref FROM acceptance_criteria WHERE task_id=?"
            " ORDER BY seq", (task_id,)).fetchall():
        if row["check_type"] != "script_exec" or not row["check_ref"]:
            continue
        try:
            cfg = json.loads(row["check_ref"])
        except (ValueError, TypeError):
            continue
        if isinstance(cfg, dict) and cfg.get("script"):
            out.append(cfg["script"])
    return out


def _check_refs(conn, task_id: str) -> list[str]:
    return [r["check_ref"] or "" for r in conn.execute(
        "SELECT check_ref FROM acceptance_criteria WHERE task_id=? ORDER BY seq",
        (task_id,)).fetchall()]


def _orphan_run(conn, run_id: str, tenant: str, *, state: str = "running") -> None:
    """孤儿批次：没有任何 eval_results 绑定 —— 只能靠物理列判断归属。"""
    conn.execute("INSERT INTO eval_runs(id,name,runner,state,tenant_id)"
                 " VALUES (?,?, 'stub', ?, ?)", (run_id, run_id, state, tenant))
    conn.commit()


# ------------------------------------------------- A) pack 种子与显式优先级
def test_web_v1_pack_seed_exists_once_and_maps_to_web_template(env):
    """计划前提「web-v1 种子缺失」的反证：新库初始化后它就在，且只有一条。"""
    conn, _, _, _ = env
    assert conn.execute("SELECT COUNT(*) FROM packs WHERE id='web-v1'").fetchone()[0] == 1
    row = conn.execute("SELECT * FROM packs WHERE id='web-v1'").fetchone()
    assert row["template"] == "web"
    assert row["state"] == "active"
    assert int(row["default_budget"]) == 35000
    assert task_generator.TEMPLATE_BY_KEY["web"] is task_generator.TEMPLATE_WEB
    assert len(task_generator.TEMPLATE_WEB) == 7


def test_explicit_pack_id_wins_over_role_tag(env):
    """pipeline.execute_build 同时传 pack_id=web-v1 与 role_tag=dev：pack 必须胜出。"""
    conn, _, _, _ = env
    out = task_generator.generate(conn, "做一个保湿面霜的落地页，600 字",
                                  legal_entity_id="LE-GROUP-HQ",
                                  pack_id="web-v1", role_tag="dev")
    assert out["pack_id"] == "web-v1"
    assert out["task_type"] == "web_write"
    assert out["pack_source"] == "explicit_pack"
    assert len(out["criteria"]) == 7
    scripts = _scripts(conn, out["task_id"])
    assert "py_syntax.py" not in scripts          # 页面交付不该被 python 语法闸卡
    assert "file_exists.py" in scripts
    refs = " ".join(_check_refs(conn, out["task_id"]))
    assert ".html" in refs and "min_bytes" in refs
    # 落库的 pack 与判据一致
    assert conn.execute("SELECT pack_id FROM tasks WHERE id=?",
                        (out["task_id"],)).fetchone()[0] == "web-v1"


def test_role_tag_still_decides_when_no_pack_id(env):
    """不传 pack_id 时 role_tag 推断必须保留（test_dev_task_keeps_py_syntax 的口径）。"""
    conn, _, _, _ = env
    out = task_generator.generate(conn, "写一个 python 脚本解析本地日志文件",
                                  legal_entity_id="LE-GROUP-HQ", role_tag="dev")
    assert out["pack_id"] == "code-local-v1"
    assert out["task_type"] == "code_write"
    assert out["pack_source"] == "role_tag"
    assert "py_syntax.py" in _scripts(conn, out["task_id"])


def test_unknown_pack_falls_back_to_role_tag_but_raises_without_it(env):
    conn, _, _, _ = env
    out = task_generator.generate(conn, "写一个 python 脚本", legal_entity_id="LE-GROUP-HQ",
                                  pack_id="no-such-v9", role_tag="dev")
    assert out["pack_id"] == "code-local-v1"
    assert out["pack_source"] == "role_tag"
    with pytest.raises(ValueError):
        task_generator.generate(conn, "写一个 python 脚本", legal_entity_id="LE-GROUP-HQ",
                                pack_id="no-such-v9")


def test_execute_build_honours_declared_pack_against_role_tag(env):
    """真实入口回归：WBS 的 web-v1 交付项必须拿到 web 判据，而不是 code 判据。"""
    conn, who, _, _ = env
    from app import flows
    pipeline.ensure(conn)
    flows.ensure(conn)
    pid = "P-V53-BUILD"
    conn.execute("INSERT OR IGNORE INTO projects (id,name,requirement,owner_identity)"
                 " VALUES (?,?,?,?)",
                 (pid, "v53 官网", "做一个全渠道营销官网，带前端html页面与宣传文案",
                  who["hq"]["id"]))
    conn.commit()
    brief = "做一个全渠道营销官网，带前端html页面与宣传文案"
    pipeline.build_wbs(conn, pid, brief)
    pipeline.execute_build(conn, pid, brief)
    rows = conn.execute("SELECT id, pack_id, task_type FROM tasks WHERE project_id=?",
                        (pid,)).fetchall()
    web_tasks = [r for r in rows if r["pack_id"] == "web-v1"]
    assert web_tasks, "WBS 应包含 web-v1 交付项"
    for row in web_tasks:
        assert row["task_type"] == "web_write"
        scripts = _scripts(conn, row["id"])
        assert "py_syntax.py" not in scripts
        assert "file_exists.py" in scripts


# ------------------------------------------------- B) eval_runs 物理租户隔离
def test_eval_runs_and_results_have_physical_tenant_columns(env):
    conn, _, _, _ = env
    for table in ("eval_runs", "eval_results"):
        cols = {r[1]: r for r in conn.execute(f"PRAGMA table_info({table})")}
        assert "tenant_id" in cols, f"{table} 缺 tenant_id 物理列"
        assert cols["tenant_id"][3] == 1, f"{table}.tenant_id 必须 NOT NULL"
        assert "TENANT-HQ" in str(cols["tenant_id"][4]), f"{table}.tenant_id 缺默认值"
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert {"idx_evalrun_tenant", "idx_evalres_tenant"} <= indexes


def test_orphan_eval_runs_are_isolated_by_physical_tenant(env):
    """DoD：两个租户各自建的孤儿批次互相不可见（列表/详情/删除 + HTTP 端点）。"""
    conn, _, client, _ = env
    _orphan_run(conn, "ER-ORPH-HQ", "TENANT-HQ")
    _orphan_run(conn, "ER-ORPH-B", "TENANT-B")
    assert {r["id"] for r in evals.list_batches(conn, tenant_id="TENANT-HQ")} == {"ER-ORPH-HQ"}
    assert {r["id"] for r in evals.list_batches(conn, tenant_id="TENANT-B")} == {"ER-ORPH-B"}
    assert evals.batch_detail(conn, "ER-ORPH-B", tenant_id="TENANT-HQ") == {}
    detail_b = evals.batch_detail(conn, "ER-ORPH-B", tenant_id="TENANT-B")
    assert detail_b["batch"]["id"] == "ER-ORPH-B" and detail_b["results"] == []
    with pytest.raises(KeyError):
        evals.delete_batch(conn, "ER-ORPH-B", tenant_id="TENANT-HQ")
    assert conn.execute("SELECT COUNT(*) FROM eval_runs WHERE id='ER-ORPH-B'").fetchone()[0] == 1
    # HTTP 端点走的是同一条口径（HQ 登录态）
    listing = client.get("/ui/evals").json()
    assert {b["id"] for b in listing["batches"]} == {"ER-ORPH-HQ"}
    assert client.get("/ui/evals/batches/ER-ORPH-B").json() == {}
    assert client.delete("/ui/evals/batches/ER-ORPH-B").status_code == 404
    assert conn.execute("SELECT COUNT(*) FROM eval_runs WHERE id='ER-ORPH-B'").fetchone()[0] == 1
    assert client.delete("/ui/evals/batches/ER-ORPH-HQ").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM eval_runs WHERE id='ER-ORPH-HQ'").fetchone()[0] == 0


def test_run_bound_to_foreign_sets_is_hidden_from_other_tenant(env):
    """老批次只存了 eval_results：运行租户仍以集合租户为准，不能被默认列值漏出去。"""
    conn, _, _, _ = env
    conn.execute("INSERT INTO eval_sets(id,name,output,criteria_json,source,tenant_id)"
                 " VALUES ('ES-B','B 基准','b-output','[]','run','TENANT-B')")
    conn.execute("INSERT INTO eval_runs(id,name,runner,state) VALUES ('ER-LEGACY-B','B','stub','done')")
    conn.execute("INSERT INTO eval_results(id,eval_run_id,set_id,output,score,state)"
                 " VALUES ('EVR-LB','ER-LEGACY-B','ES-B','b-output',50,'ok')")
    conn.commit()
    assert "ER-LEGACY-B" not in {r["id"] for r in evals.list_batches(conn, tenant_id="TENANT-HQ")}
    assert "ER-LEGACY-B" in {r["id"] for r in evals.list_batches(conn, tenant_id="TENANT-B")}
    assert evals.batch_detail(conn, "ER-LEGACY-B", tenant_id="TENANT-HQ") == {}
    with pytest.raises(KeyError):
        evals.delete_batch(conn, "ER-LEGACY-B", tenant_id="TENANT-HQ")


def test_start_regression_stamps_tenant_on_the_run_row(env):
    conn, _, _, _ = env
    conn.execute("INSERT INTO eval_sets(id,name,output,criteria_json,source,tenant_id)"
                 " VALUES ('ES-B2','B 基准 2','b-output','[]','run','TENANT-B')")
    conn.commit()
    with pytest.raises(ValueError):               # 他租户集合不得进入批次
        evals.start_regression(conn, set_ids=["ES-B2"], runner="mock_acp",
                               tenant_id="TENANT-HQ")
    bid = evals.start_regression(conn, set_ids=["ES-B2"], runner="mock_acp",
                                 tenant_id="TENANT-B")
    row = conn.execute("SELECT tenant_id FROM eval_runs WHERE id=?", (bid,)).fetchone()
    assert row["tenant_id"] == "TENANT-B"
    assert bid in {r["id"] for r in evals.list_batches(conn, tenant_id="TENANT-B")}
    assert bid not in {r["id"] for r in evals.list_batches(conn, tenant_id="TENANT-HQ")}


def test_legacy_eval_tables_get_tenant_column_and_chain_backfill(env):
    """存量库：ADD COLUMN → 按 eval_results→eval_sets→legal_entities 链路回填 → 幂等。"""
    conn, _, _, _ = env
    conn.executescript("""
        DROP TABLE eval_results; DROP TABLE eval_sets; DROP TABLE eval_runs;
        CREATE TABLE eval_sets (id TEXT PRIMARY KEY, name TEXT NOT NULL, run_id TEXT, task_id TEXT,
          project_id TEXT, agent_id TEXT, model TEXT, pack_id TEXT, task_title TEXT, prompt TEXT,
          output TEXT, criteria_json TEXT, source TEXT DEFAULT 'run', created_by TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(run_id));
        CREATE TABLE eval_runs (id TEXT PRIMARY KEY, name TEXT, runner TEXT, mode TEXT,
          state TEXT DEFAULT 'running', set_count INTEGER DEFAULT 0, avg_score REAL DEFAULT 0,
          detail_json TEXT, created_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          ended_at TIMESTAMP);
        CREATE TABLE eval_results (id TEXT PRIMARY KEY, eval_run_id TEXT NOT NULL, set_id TEXT NOT NULL,
          task_id TEXT, run_id TEXT, output TEXT, similarity REAL DEFAULT 0, judge_score REAL,
          judge_reason TEXT, score REAL DEFAULT 0, state TEXT DEFAULT 'ok', detail_json TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)
          VALUES ('T-LEG-B','LE-B','TENANT-B','eval','旧基准任务','todo');
        INSERT INTO eval_sets(id,name,output,task_id) VALUES
          ('ES-L-B','旧 B 基准','o','T-LEG-B'),
          ('ES-L-ORPH','旧孤儿基准','o',NULL);
        INSERT INTO eval_runs(id,name,runner,state) VALUES
          ('ER-L-B','旧 B 批次','stub','done'),
          ('ER-L-ORPH','旧孤儿批次','stub','running');
        INSERT INTO eval_results(id,eval_run_id,set_id,output,score,state)
          VALUES ('EVR-L-B','ER-L-B','ES-L-B','o',10,'ok');
    """)
    evals.ensure(conn)
    runs = {r["id"]: r["tenant_id"] for r in conn.execute(
        "SELECT id,tenant_id FROM eval_runs").fetchall()}
    assert runs == {"ER-L-B": "TENANT-B", "ER-L-ORPH": "TENANT-HQ"}
    results = {r["id"]: r["tenant_id"] for r in conn.execute(
        "SELECT id,tenant_id FROM eval_results").fetchall()}
    assert results == {"EVR-L-B": "TENANT-B"}
    # 回填后孤儿批次严格隔离
    assert {r["id"] for r in evals.list_batches(conn, tenant_id="TENANT-HQ")} == {"ER-L-ORPH"}
    assert {r["id"] for r in evals.list_batches(conn, tenant_id="TENANT-B")} == {"ER-L-B"}
    # 幂等：再跑一次 ensure 不改归属、不重复加列
    evals.ensure(conn)
    assert {r["id"]: r["tenant_id"] for r in conn.execute(
        "SELECT id,tenant_id FROM eval_runs").fetchall()} == runs
    assert {r["id"]: r["tenant_id"] for r in conn.execute(
        "SELECT id,tenant_id FROM eval_results").fetchall()} == results
