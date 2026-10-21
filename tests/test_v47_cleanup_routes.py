"""v4.5.4：补齐「只能创建、无法删除」的几类数据，并给只增不减的日志一个清理入口。

覆盖：组织经验、评测样本、评测批次、自定义流程模板、系统维护批量清理。
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from app import evals, experience, identity, pipeline, trace as trace_mod
from app.db import connect, init_db
from app.main import app
from app.ui import store as ui_store


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    db_path = str(tmp_path / "v47.db")
    for key, value in {
        "DB_PATH": db_path,
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "M0_PIPELINE_EXEC": "0",
        "M0_DISPATCH_SYNC": "1",
        "UI_SESSION_SECRET": "v47-secret",
    }.items():
        monkeypatch.setenv(key, value)
    init_db()
    conn = connect(db_path)
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    pipeline.ensure(conn)
    experience.ensure(conn)
    evals.ensure(conn)
    ui_store.ensure(conn)
    trace_mod.ensure(conn)
    ceo = identity.issue(conn, name="CEO", role="ceo", legal_entity_id="LE-GROUP-HQ")
    agent = identity.issue(conn, name="agent-y", role="agent", legal_entity_id="LE-GROUP-HQ")
    conn.commit()
    conn.close()

    from app.ui import api as ui_api
    boss = TestClient(app)
    boss.cookies.set("oa_ui", ui_api._make_cookie(ceo["id"]))
    worker = TestClient(app)
    worker.cookies.set("oa_ui", ui_api._make_cookie(agent["id"]))
    return {"boss": boss, "worker": worker, "ceo": ceo, "agent": agent}


def _count(sql: str, args=()):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------- 组织经验
def test_experience_delete_removes_row_and_history(ctx):
    conn = connect()
    exp = experience.add(conn, kind="decision", title="测试经验", content="正文",
                         mandatory=0, tenant_id="TENANT-HQ", entity="LE-GROUP-HQ")
    experience._history(conn, conn.execute("SELECT * FROM org_experiences WHERE id=?",
                                           (exp["id"],)).fetchone(), "edit", ctx["ceo"]["id"])
    conn.commit()
    conn.close()
    assert _count("SELECT COUNT(*) FROM experience_reviews WHERE experience_id=?", (exp["id"],)) == 1

    res = ctx["boss"].delete(f"/ui/experiences/{exp['id']}")
    assert res.status_code == 200, res.text
    assert res.json()["reviews_removed"] == 1
    assert _count("SELECT COUNT(*) FROM org_experiences WHERE id=?", (exp["id"],)) == 0
    assert _count("SELECT COUNT(*) FROM experience_reviews WHERE experience_id=?", (exp["id"],)) == 0
    # 删除动作本身留审计
    assert _count("SELECT COUNT(*) FROM ai_audit_logs WHERE action='experience_deleted'") == 1


def test_experience_delete_forbidden_and_missing(ctx):
    assert ctx["worker"].delete("/ui/experiences/EXP-nope").status_code == 403
    assert ctx["boss"].delete("/ui/experiences/EXP-nope").status_code == 404


# --------------------------------------------------------------- 评测中心
def _seed_eval(conn):
    conn.execute("INSERT INTO eval_sets (id,name,output,criteria_json,source)"
                 " VALUES ('ES-1','基准一','out','[]','run')")
    conn.execute("INSERT INTO eval_runs (id,name,runner,state) VALUES ('ER-1','批次一','stub','succeeded')")
    conn.execute("INSERT INTO eval_results (id,eval_run_id,set_id,score,state)"
                 " VALUES ('EV-1','ER-1','ES-1',0.9,'ok')")
    conn.commit()


def test_eval_set_delete_removes_results(ctx):
    conn = connect()
    _seed_eval(conn)
    conn.close()
    res = ctx["boss"].delete("/ui/evals/sets/ES-1")
    assert res.status_code == 200, res.text
    assert res.json()["results_removed"] == 1
    assert _count("SELECT COUNT(*) FROM eval_sets WHERE id='ES-1'") == 0
    assert _count("SELECT COUNT(*) FROM eval_results WHERE set_id='ES-1'") == 0


def test_eval_batch_delete(ctx):
    conn = connect()
    _seed_eval(conn)
    conn.close()
    res = ctx["boss"].delete("/ui/evals/batches/ER-1")
    assert res.status_code == 200, res.text
    assert _count("SELECT COUNT(*) FROM eval_runs WHERE id='ER-1'") == 0
    assert _count("SELECT COUNT(*) FROM eval_results WHERE eval_run_id='ER-1'") == 0
    assert ctx["worker"].delete("/ui/evals/batches/ER-1").status_code == 403


# --------------------------------------------------------------- 流程模板
def test_flow_template_delete_custom_only(ctx):
    body = {"id": "FLOW-CUSTOM", "name": "自定义流", "kind": "custom",
            "description": "d",
            "nodes": [{"key": "a", "title": "A", "role": "dev", "gate": "完成"}]}
    assert ctx["boss"].post("/ui/flow-templates", json=body).status_code == 200
    assert _count("SELECT COUNT(*) FROM flow_templates WHERE id='FLOW-CUSTOM'") == 1

    res = ctx["boss"].delete("/ui/flow-templates/FLOW-CUSTOM")
    assert res.status_code == 200, res.text
    assert _count("SELECT COUNT(*) FROM flow_templates WHERE id='FLOW-CUSTOM'") == 0

    # 内置模板不可删
    assert ctx["boss"].delete("/ui/flow-templates/FLOW-SOFTWARE").status_code == 409
    assert ctx["boss"].delete("/ui/flow-templates/FLOW-NOPE").status_code == 404


# --------------------------------------------------------------- 系统维护
def test_maintenance_stats_and_purge(ctx, tmp_path):
    conn = connect()
    conn.execute("INSERT INTO tasks (id,legal_entity_id,task_type,title,state,tenant_id)"
                 " VALUES ('T-x','LE-GROUP-HQ','write','x','completed','TENANT-HQ')")
    conn.execute("INSERT INTO task_runs (id,task_id,run_seq,legal_entity_id,tenant_id,state)"
                 " VALUES ('RUN-x','T-x',1,'LE-GROUP-HQ','TENANT-HQ','succeeded')")
    conn.execute("INSERT INTO ai_audit_logs (id,legal_entity_id,verb,layer,actor,action,created_at)"
                 " VALUES ('AL-old','LE-GROUP-HQ','x','system','t','old',datetime('now','-200 days'))")
    conn.execute("INSERT INTO ai_audit_logs (id,legal_entity_id,verb,layer,actor,action,created_at)"
                 " VALUES ('AL-new','LE-GROUP-HQ','x','system','t','new',datetime('now'))")
    conn.execute("INSERT INTO run_stream (run_id,kind,text,created_at)"
                 " VALUES ('RUN-x','t','old',datetime('now','-90 days'))")
    conn.execute("INSERT INTO run_stream (run_id,kind,text,created_at)"
                 " VALUES ('RUN-x','t','new',datetime('now'))")
    # 孤儿 trace：run 不存在
    conn.execute("INSERT INTO agent_traces (trace_id,run_id,state) VALUES ('TR-orphan','RUN-missing','done')")
    conn.execute("INSERT INTO trace_spans (id,trace_id,kind,name) VALUES ('SP-1','TR-orphan','x','y')")
    # 软删产物 + 磁盘残留文件（放在 workspace 内，才是可清理范围）
    f = tmp_path / "workspace" / "leftover.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("stale", encoding="utf-8")
    conn.execute("INSERT INTO artifacts (id,run_id,art_seq,file_path,checksum,bytes,state)"
                 " VALUES ('ART-del','RUN-x',1,?,'x',5,'deleted')", (str(f),))
    conn.commit()
    conn.close()

    stats = ctx["boss"].get("/ui/maintenance/stats").json()
    assert stats["counts"]["audit_logs"] >= 1
    assert stats["counts"]["run_stream"] >= 1
    assert stats["counts"]["orphan_traces"] >= 1
    assert stats["counts"]["deleted_artifacts"] >= 1
    assert stats["reclaimable_files"] >= 1

    # dry run 不动数据
    dry = ctx["boss"].post("/ui/maintenance/purge",
                           json={"categories": ["audit_logs", "orphan_traces"], "dry_run": True}).json()
    assert dry["dry_run"] is True and dry["planned"]["audit_logs"] >= 1
    assert _count("SELECT COUNT(*) FROM ai_audit_logs WHERE id='AL-old'") == 1

    # 真删：只删过期与孤儿，新数据必须留着
    out = ctx["boss"].post("/ui/maintenance/purge",
                           json={"categories": ["audit_logs", "run_stream", "orphan_traces",
                                                "deleted_artifacts"], "dry_run": False}).json()
    assert out["dry_run"] is False
    assert _count("SELECT COUNT(*) FROM ai_audit_logs WHERE id='AL-old'") == 0
    assert _count("SELECT COUNT(*) FROM ai_audit_logs WHERE id='AL-new'") == 1
    assert _count("SELECT COUNT(*) FROM run_stream WHERE text='old'") == 0
    assert _count("SELECT COUNT(*) FROM run_stream WHERE text='new'") == 1
    assert _count("SELECT COUNT(*) FROM agent_traces WHERE trace_id='TR-orphan'") == 0
    assert _count("SELECT COUNT(*) FROM trace_spans WHERE trace_id='TR-orphan'") == 0
    assert _count("SELECT COUNT(*) FROM artifacts WHERE id='ART-del'") == 0
    assert not f.exists(), "软删产物的磁盘残留必须一起清掉"
    assert out["files_removed"] >= 1
    assert _count("SELECT COUNT(*) FROM ai_audit_logs WHERE action='maintenance_purged'") == 1


def test_orphan_files_purge(ctx, tmp_path):
    """没有任何表引用、且超过保留期的文件 = 孤儿，应可被清理；被引用的必须留着。"""
    ws = tmp_path / "workspace"
    kept = ws / "task-a" / "kept.md"
    orphan = ws / "task-b" / "orphan.md"
    fresh = ws / "task-c" / "fresh.md"
    for f in (kept, orphan, fresh):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")

    import os
    old = 1_600_000_000
    os.utime(kept, (old, old))
    os.utime(orphan, (old, old))     # fresh 保持当前时间

    conn = connect()
    conn.execute("INSERT INTO tasks (id,legal_entity_id,task_type,title,state,tenant_id)"
                 " VALUES ('T-o','LE-GROUP-HQ','write','o','completed','TENANT-HQ')")
    conn.execute("INSERT INTO task_runs (id,task_id,run_seq,legal_entity_id,tenant_id,state)"
                 " VALUES ('RUN-o','T-o',1,'LE-GROUP-HQ','TENANT-HQ','succeeded')")
    conn.execute("INSERT INTO artifacts (id,run_id,art_seq,file_path,checksum,bytes)"
                 " VALUES ('ART-keep','RUN-o',1,?,'x',1)", (str(kept),))
    conn.commit()
    conn.close()

    stats = ctx["boss"].get("/ui/maintenance/stats").json()
    assert stats["counts"]["orphan_files"] >= 1
    assert "orphan_files" in stats["categories"]

    out = ctx["boss"].post("/ui/maintenance/purge",
                           json={"categories": ["orphan_files"], "dry_run": False}).json()
    assert out["files_removed"] >= 1
    assert not orphan.exists(), "孤儿文件应被清理"
    assert kept.exists(), "仍被引用的文件必须保留"
    assert fresh.exists(), "未超过保留期的新文件必须保留"


def test_maintenance_requires_admin(ctx):
    assert ctx["worker"].get("/ui/maintenance/stats").status_code == 403
    assert ctx["worker"].post("/ui/maintenance/purge", json={"categories": ["run_stream"]}).status_code == 403
    assert ctx["boss"].post("/ui/maintenance/purge", json={"categories": ["nope"]}).status_code == 400
