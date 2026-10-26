"""v5.2 TASK-3.1：经验沉淀降噪、批量分组审核与 eval_sets 租户隔离。

覆盖：
  * 单次 rejection 不再写 pending 经验（452 条积压的根因）；
  * 保留的三类高价值来源：自愈成功 / 研讨会一票否决 / 项目复盘；
  * 批量审核单事务 + 逐条 experience_reviewed 审计 + 跨租户跳过；
  * eval_sets 的 tenant_id 迁移、回填、列表/执行/读取/删除全入口隔离。
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import acceptance_run, evals, experience, identity, pipeline
from app.db import connect, init_db
from app.state_machine import transition
from app.ui.api import _make_cookie


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "v52.db")
    for key, value in {
        "DB_PATH": db_path,
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "M0_SANDBOX": "none",
        "M0_SANDBOX_ACP": "0",
        "M0_HEARTBEAT_SECONDS": "0",
        "UI_SESSION_SECRET": "v52-test-secret",
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
    ceo = identity.issue(conn, name="HQ CEO", role="ceo")
    other = identity.issue(conn, name="B CEO", role="ceo", legal_entity_id="LE-B")
    conn.commit()
    from app.main import app
    with TestClient(app) as client:
        client.cookies.set("oa_ui", _make_cookie(ceo["id"]))
        yield conn, {"ceo": ceo, "other": other}, client, tmp_path
    conn.close()


def _exp_rows(conn, **where):
    sql = "SELECT * FROM org_experiences"
    args = []
    if where:
        sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
        args = list(where.values())
    return [dict(r) for r in conn.execute(sql + " ORDER BY rowid", args).fetchall()]


def _rejected_task(conn, folder, *, task_id="T-V52", entity="LE-GROUP-HQ",
                   tenant="TENANT-HQ") -> str:
    """造一个 L2 字数判据失败的任务（真实走 acceptance_run，不 mock 判据）。"""
    conn.execute(
        "INSERT INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
        " VALUES (?,?,?,'content_write','降噪测试任务','pending_review')",
        (task_id, entity, tenant))
    conn.execute(
        "INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,state)"
        " VALUES (?,?,1,?,?,'succeeded')", ("R-" + task_id, task_id, entity, tenant))
    text = "too short"
    path = folder / f"{task_id}.txt"
    path.write_text(text, encoding="utf-8")
    conn.execute(
        "INSERT INTO artifacts(id,run_id,tenant_id,art_seq,file_path,content,checksum,bytes)"
        " VALUES (?,?,?,1,?,?, 'x', ?)",
        ("A-" + task_id, "R-" + task_id, tenant, str(path), text, len(text)))
    conn.execute(
        """INSERT INTO acceptance_criteria
             (id,legal_entity_id,task_id,criterion,check_type,check_ref,is_blocking,seq)
           VALUES (?,?,?,'字数达标','script_exec',?,1,1)""",
        ("C-" + task_id, entity, task_id,
         json.dumps({"script": "word_count.py", "min": 10000})))
    conn.commit()
    return task_id


def test_single_rejection_no_longer_writes_pending_noise(env):
    conn, _, _, folder = env
    tid = _rejected_task(conn, folder)
    result = acceptance_run.run(conn, tid, ["LE-GROUP-HQ"])
    assert result["verdict"] == "rejected"
    assert result["task_state"] == "rejected"
    # 关键断言：驳回只留在 task_runs.error / 审计，不再生成 pending 经验。
    assert _exp_rows(conn) == []
    # 能力没有被删掉：显式 opt_in 仍可沉淀（人工判定高价值时）。
    row = experience.capture_rejection(conn, tid, verdict="rejected", opt_in=True)
    assert row is not None and row["state"] == "pending" and row["kind"] == "rejection"


def test_veto_class_rejection_is_retained_and_idempotent(env):
    conn, _, _, folder = env
    tid = _rejected_task(conn, folder, task_id="T-VETO")
    assert experience.capture_rejection(conn, tid, verdict="rejected") is None
    row = experience.capture_rejection(conn, tid, verdict="vetoed")
    assert row is not None
    assert row["kind"] == "decision" and row["state"] == "pending"
    assert "否决" in row["title"]
    assert experience.capture_rejection(conn, tid, verdict="vetoed") is None


def test_self_healed_task_writes_experience_and_blocks_rejection_noise(env):
    conn, _, _, folder = env
    tid = _rejected_task(conn, folder, task_id="T-HEAL")
    assert acceptance_run.run(conn, tid, ["LE-GROUP-HQ"])["verdict"] == "rejected"
    # 重跑并通过：真实触发 capture_self_healed（acceptance_run 内既有调用点）。
    transition(conn, tid, "retry", actor="test")
    conn.execute("UPDATE acceptance_criteria SET state='pending' WHERE task_id=?", (tid,))
    conn.execute("UPDATE tasks SET state='pending_review' WHERE id=?", (tid,))
    long_text = "测" * 12000          # word_count.py 按 CJK 字数计，不能喂英文单词串
    path = folder / "healed.txt"
    path.write_text(long_text, encoding="utf-8")
    conn.execute(
        "INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,state)"
        " VALUES ('R-T-HEAL-2',?,2,'LE-GROUP-HQ','TENANT-HQ','succeeded')", (tid,))
    conn.execute(
        "INSERT INTO artifacts(id,run_id,tenant_id,art_seq,file_path,content,checksum,bytes)"
        " VALUES ('A-T-HEAL-2','R-T-HEAL-2','TENANT-HQ',2,?,?, 'y', ?)",
        (str(path), long_text, len(long_text)))
    conn.commit()
    healed = acceptance_run.run(conn, tid, ["LE-GROUP-HQ"])
    assert healed["verdict"] == "completed", healed
    rows = _exp_rows(conn)
    assert [r["kind"] for r in rows] == ["consensus"]
    assert rows[0]["title"].startswith("自愈成功：")
    # 已自愈的任务不再写反向驳回噪音。
    assert experience.capture_rejection(conn, tid, verdict="vetoed") is None


def test_workshop_consensus_only_captured_on_veto(env):
    conn, _, _, folder = env
    pid = pipeline.create_project(conn, name="研讨会", requirement="方案")["project_id"]
    statements = [{"role": "architect", "title": "架构师", "risk": "兼容风险",
                   "stance": "有条件同意"},
                  {"role": "legal", "title": "法务", "risk": "合规风险", "stance": "反对",
                   "question": "证据在哪"}]
    assert experience.capture_consensus(
        conn, project_id=pid, workshop_id="W-1", requirement="方案",
        statements=[{"role": "architect", "title": "架构师", "risk": "r",
                     "stance": "同意"}]) is None
    row = experience.capture_consensus(
        conn, project_id=pid, workshop_id="W-2", requirement="方案", statements=statements)
    assert row is not None and row["kind"] == "decision"
    assert row["title"].startswith("研讨会否决：")
    assert _exp_rows(conn, state="pending") != []


def test_retro_capture_requires_done_stage_and_is_idempotent(env):
    conn, _, client, _ = env
    pid = pipeline.create_project(conn, name="复盘项目", requirement="做一个官网")["project_id"]
    # 未走完复盘不沉淀
    assert experience.capture_retro(conn, pid) is None
    pipeline.run_project(conn, pid, "做一个官网")
    assert conn.execute("SELECT state FROM project_stages WHERE project_id=? AND key='retro'",
                        (pid,)).fetchone()[0] == "done"
    row = experience.capture_retro(conn, pid)
    assert row is not None and row["title"].startswith("复盘：")
    assert row["source_type"] == experience.SOURCE_RETRO
    assert experience.capture_retro(conn, pid) is None
    # 治理页读取时做幂等补齐（来源 c 的可达路径，不改 pipeline）。
    pid2 = pipeline.create_project(conn, name="复盘项目2", requirement="写一份调研")["project_id"]
    pipeline.run_project(conn, pid2, "写一份调研")
    response = client.get("/ui/experiences")
    assert response.status_code == 200
    titles = [item["title"] for item in response.json()["experiences"]]
    assert any(title.startswith("复盘：复盘项目2") for title in titles), titles
    assert response.json()["groups"]["kind"]


def test_batch_review_single_transaction_and_audit(env):
    conn, _, client, _ = env
    ids = [experience.add(conn, kind="decision", title=f"待审{i}", content="内容")
           for i in range(3)]
    ids = [row["id"] for row in ids]
    before_audit = conn.execute(
        "SELECT COUNT(*) FROM ai_audit_logs WHERE action='experience_reviewed'").fetchone()[0]
    response = client.post("/ui/experiences/batch-review",
                           json={"exp_ids": ids[:2], "action": "approve",
                                 "note": "批量通过测试"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2 and set(body["processed_ids"]) == set(ids[:2])
    states = {r["id"]: r["state"] for r in _exp_rows(conn)}
    assert states[ids[0]] == states[ids[1]] == "approved"
    assert states[ids[2]] == "pending"
    after_audit = conn.execute(
        "SELECT COUNT(*) FROM ai_audit_logs WHERE action='experience_reviewed'").fetchone()[0]
    assert after_audit - before_audit == 2
    history = conn.execute("SELECT COUNT(*) FROM experience_reviews"
                           " WHERE action='approve'").fetchone()[0]
    assert history == 2
    # 已审核过的 id 再批量通过：跳过而不是重复计数。
    again = client.post("/ui/experiences/batch-review",
                        json={"experience_ids": [ids[0]], "action": "reject"}).json()
    assert again["count"] == 0 and again["skipped"][0]["code"] == "ExperienceAlreadyReviewed"
    # 非法动作 422；其余一条可通过批量驳回。
    assert client.post("/ui/experiences/batch-review",
                       json={"experience_ids": [ids[2]], "action": "maybe"}).status_code == 422
    rejected = client.post("/ui/experiences/batch-review",
                           json={"experience_ids": [ids[2]], "action": "reject",
                                 "note": "低质"}).json()
    assert rejected["count"] == 1
    assert {r["id"]: r["state"] for r in _exp_rows(conn)}[ids[2]] == "rejected"


def test_batch_review_and_list_cannot_cross_tenant(env):
    conn, actors, client, _ = env
    foreign = experience.add(conn, kind="decision", title="B 私有经验", content="B 的规则",
                             entity="LE-B", tenant_id="TENANT-B")
    own = experience.add(conn, kind="consensus", title="A 自己的经验", content="A 的规则")
    listed = {item["id"] for item in client.get("/ui/experiences").json()["experiences"]}
    assert foreign["id"] not in listed and own["id"] in listed
    # 批量接口拿到他租户 id 只能跳过，绝不能写。
    body = client.post("/ui/experiences/batch-review",
                       json={"experience_ids": [foreign["id"]], "action": "reject"}).json()
    assert body["count"] == 0
    assert body["skipped"] == [{"id": foreign["id"], "code": "NotFound"}]
    assert conn.execute("SELECT state FROM org_experiences WHERE id=?",
                        (foreign["id"],)).fetchone()[0] == "pending"


def test_eval_sets_are_tenant_isolated_everywhere(env):
    conn, _, client, _ = env
    conn.execute("INSERT INTO eval_sets(id,name,output,criteria_json,source,tenant_id)"
                 " VALUES ('ES-A','A 基准','a-output','[]','run','TENANT-HQ')")
    conn.execute("INSERT INTO eval_sets(id,name,output,criteria_json,source,tenant_id)"
                 " VALUES ('ES-B','B 基准','b-secret-output','[]','run','TENANT-B')")
    conn.execute("INSERT INTO eval_runs(id,name,runner,state) VALUES ('ER-A','A批次','stub','done')")
    conn.execute("INSERT INTO eval_runs(id,name,runner,state) VALUES ('ER-B','B批次','stub','done')")
    conn.execute("INSERT INTO eval_results(id,eval_run_id,set_id,output,score,state)"
                 " VALUES ('EVR-A','ER-A','ES-A','a-output',90,'ok')")
    conn.execute("INSERT INTO eval_results(id,eval_run_id,set_id,output,score,state)"
                 " VALUES ('EVR-B','ER-B','ES-B','b-secret-output',90,'ok')")
    conn.commit()
    # 列表只看到本租户
    listing = client.get("/ui/evals").json()
    assert {s["id"] for s in listing["sets"]} == {"ES-A"}
    assert {b["id"] for b in listing["batches"]} == {"ER-A"}
    # 批次详情不返回他租户结果 / 输出全文
    assert client.get("/ui/evals/batches/ER-B").json() == {}
    assert client.get("/ui/evals/batches/ER-A").json()["results"][0]["set_id"] == "ES-A"
    # 删除他租户基准必须拿不到（404），本租户可删。
    assert client.delete("/ui/evals/sets/ES-B").status_code == 404
    assert client.delete("/ui/evals/batches/ER-B").status_code == 404
    assert conn.execute("SELECT COUNT(*) FROM eval_sets WHERE id='ES-B'").fetchone()[0] == 1
    assert client.delete("/ui/evals/sets/ES-A").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM eval_sets WHERE id='ES-A'").fetchone()[0] == 0
    # 函数级入口同样收敛：跨租户 run_case 视为不存在；回归不会带入他租户 set。
    with pytest.raises(ValueError):
        evals.run_case(conn, "ES-B", runner="mock_acp", tenant_id="TENANT-HQ")
    assert evals.list_sets(conn, tenant_id="TENANT-HQ") == []
    with pytest.raises(ValueError):
        evals.start_regression(conn, set_ids=["ES-B"], runner="mock_acp",
                               tenant_id="TENANT-HQ")
    assert evals.resolve_set_ids(conn, ["ES-B"], "TENANT-HQ") == []


def test_eval_sets_legacy_table_gets_tenant_column_and_backfill(env):
    conn, _, _, _ = env
    conn.execute("DROP TABLE IF EXISTS eval_results")
    conn.execute("DROP TABLE IF EXISTS eval_sets")
    conn.execute("""CREATE TABLE eval_sets (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, run_id TEXT, task_id TEXT,
        project_id TEXT, agent_id TEXT, model TEXT, pack_id TEXT, task_title TEXT,
        prompt TEXT, output TEXT, criteria_json TEXT, source TEXT DEFAULT 'run',
        created_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(run_id))""")
    conn.execute("INSERT INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
                 " VALUES ('T-LEGACY','LE-B','TENANT-B','eval','旧基准','todo')")
    conn.execute("INSERT INTO eval_sets(id,name,task_id,output) VALUES ('ES-OLD','旧','T-LEGACY','o')")
    conn.execute("INSERT INTO eval_sets(id,name,output) VALUES ('ES-ORPHAN','孤儿','o')")
    conn.commit()
    evals.ensure(conn)
    rows = {r["id"]: r["tenant_id"] for r in conn.execute(
        "SELECT id,tenant_id FROM eval_sets").fetchall()}
    assert rows["ES-OLD"] == "TENANT-B"      # 从任务继承
    assert rows["ES-ORPHAN"] == "TENANT-HQ"  # 兜底
    assert "tenant_id" in {r[1] for r in conn.execute("PRAGMA table_info(eval_sets)")}
    # 幂等：重复 ensure 不重复加列/改数据
    evals.ensure(conn)
    assert {r["id"]: r["tenant_id"] for r in conn.execute(
        "SELECT id,tenant_id FROM eval_sets").fetchall()} == rows


def test_eval_endpoints_require_login(env):
    conn, _, _, _ = env
    from app.main import app
    with TestClient(app) as anonymous:
        assert anonymous.get("/ui/evals").status_code == 401
