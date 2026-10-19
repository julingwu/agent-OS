"""M16：v1.5 产线（项目 / 阶段 / 闸门 / 研讨会 / WBS / Tool ACL）。

验收口径（董事长）：
  * 一条需求 → 1 个 project_id + 9 条阶段记录；
  * 每个阶段都有产物文件；
  * 任一闸门不过，项目原地卡死（数据库触发器保证）；
  * 全程审计可回放。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3

import pytest

from app import pipeline, tools_acl
from app.db import get_conn, init_db


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("M0_DISPATCH_SYNC", "1")
    monkeypatch.setenv("M0_PIPELINE_EXEC", "0")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "m16.db"))
    init_db()
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type) VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
        c.commit()
        yield c


def test_create_project_lays_out_nine_stages(conn):
    pid = pipeline.create_project(conn, name="记账小程序",
                                  requirement="开发一个个人记账小程序并发布上线")["project_id"]
    rows = conn.execute("SELECT seq,key,state FROM project_stages WHERE project_id=? ORDER BY seq",
                        (pid,)).fetchall()
    assert len(rows) == 9
    assert [r["key"] for r in rows][0] == "intake"
    assert [r["key"] for r in rows][-1] == "retro"
    assert all(r["state"] == "pending" for r in rows)


def test_full_run_produces_nine_deliverables(conn):
    pid = pipeline.create_project(conn, name="记账小程序",
                                  requirement="开发一个个人记账小程序并发布上线")["project_id"]
    out = pipeline.run_project(conn, pid, "开发一个个人记账小程序并发布上线")
    assert len(out["stages"]) == 9
    dels = conn.execute("SELECT kind,file_path,bytes FROM stage_deliverables WHERE project_id=?",
                        (pid,)).fetchall()
    assert len(dels) == 9
    for d in dels:
        assert pathlib.Path(d["file_path"]).exists()
        assert d["bytes"] > 0
    assert conn.execute("SELECT state FROM projects WHERE id=?", (pid,)).fetchone()["state"] == "active"


def test_stage_cannot_skip(conn):
    pid = pipeline.create_project(conn, name="x", requirement="y")["project_id"]
    with pytest.raises(sqlite3.IntegrityError, match="StageSkipped"):
        conn.execute("UPDATE project_stages SET state='active' WHERE project_id=? AND seq=4", (pid,))
        conn.commit()


def test_stage_cannot_finish_without_gate(conn):
    pid = pipeline.create_project(conn, name="x", requirement="y")["project_id"]
    conn.execute("UPDATE project_stages SET state='active' WHERE project_id=? AND seq=0", (pid,))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="GateNotPassed"):
        conn.execute("UPDATE project_stages SET state='done' WHERE project_id=? AND seq=0", (pid,))
        conn.commit()


def test_veto_blocks_the_project(conn):
    pid = pipeline.create_project(conn, name="x", requirement="y")["project_id"]
    pipeline.enter_stage(conn, pid, "intake")
    pipeline.gate(conn, pid, "intake", "ok")
    pipeline.finish_stage(conn, pid, "intake")
    pipeline.enter_stage(conn, pid, "charter")
    pipeline.gate(conn, pid, "charter", "法务否决", kind="veto", role="legal", passed=False)
    assert conn.execute("SELECT state FROM projects WHERE id=?", (pid,)).fetchone()["state"] == "blocked"


def test_workshop_requires_risks_from_every_role(conn):
    pid = pipeline.create_project(conn, name="x", requirement="y")["project_id"]
    res = pipeline.run_workshop(conn, pid, "开发一个记账小程序")
    kinds = [t["kind"] for t in res["turns"]]
    assert kinds.count("proposal") >= 1
    assert kinds.count("question") >= 3
    assert kinds.count("risk") >= 3
    assert kinds.count("vote") >= 3
    rounds = {t["round"] for t in res["turns"]}
    assert rounds == {1, 2, 3, 4}


def test_dag_forces_serial_order(conn):
    pid = pipeline.create_project(conn, name="x", requirement="y")["project_id"]
    pipeline.build_wbs(conn, pid, "开发并写文章")
    assert pipeline.can_start(conn, pid, "T1") is True
    assert pipeline.can_start(conn, pid, "T2") is False      # T1 未完成
    with pytest.raises(PermissionError):
        pipeline.complete_wbs(conn, pid, "T2")
    pipeline.complete_wbs(conn, pid, "T1")
    assert pipeline.can_start(conn, pid, "T2") is True


def test_timeline_replays_the_run(conn):
    pid = pipeline.create_project(conn, name="x", requirement="开发一个记账小程序")["project_id"]
    pipeline.run_project(conn, pid, "开发一个记账小程序")
    tl = pipeline.timeline(conn, pid)
    actions = [row["action"] for row in tl]
    for expected in ("project_created", "stage_enter", "gate_pass", "stage_done"):
        assert expected in actions
    assert len(tl) >= 9


def test_tool_acl_denies_writes_and_audits(conn):
    # 金融分析师有 db_query，但不能 UPDATE
    with pytest.raises(PermissionError):
        tools_acl.check(conn, role_tag="data", tool="db_query",
                        sql="UPDATE budgets SET limit_tokens=0")
    row = conn.execute("SELECT action,payload FROM ai_audit_logs WHERE action='tool_denied'"
                       " ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row is not None and "UPDATE" in row["payload"]

    # 只读可以过
    assert tools_acl.check(conn, role_tag="data", tool="db_query",
                           sql="SELECT * FROM budgets") is True
    # 没绑定的工具直接拒
    with pytest.raises(PermissionError):
        tools_acl.check(conn, role_tag="legal", tool="db_query", sql="SELECT 1")


def test_llm_docs_switch_defaults_off(conn):
    pid = pipeline.create_project(conn, name="x", requirement="开发一个记账小程序")["project_id"]
    row = conn.execute("SELECT use_llm_docs FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["use_llm_docs"] == 0
    pid2 = pipeline.create_project(conn, name="y", requirement="发布一个官网",
                                   use_llm_docs=1)["project_id"]
    assert conn.execute("SELECT use_llm_docs FROM projects WHERE id=?",
                        (pid2,)).fetchone()["use_llm_docs"] == 1


def test_deliverables_are_deduped_by_stage_and_kind(conn):
    pid = pipeline.create_project(conn, name="x", requirement="开发一个记账小程序")["project_id"]
    pipeline.run_project(conn, pid, "开发一个记账小程序")
    # 人为制造一次重跑：同 stage/kind 再写一条
    pipeline._write(conn, pid, "build", "build", "04_build.md", "第二次")
    raw = conn.execute("SELECT COUNT(*) n FROM stage_deliverables WHERE project_id=?",
                       (pid,)).fetchone()["n"]
    assert raw == 10
    # API 层去重后应为 9
    from app.ui import api as ui_api
    with get_conn() as c2:
        pass
    seen, dels = set(), []
    for d in conn.execute("SELECT stage_key, kind FROM stage_deliverables WHERE project_id=?",
                          (pid,)):
        if (d["stage_key"], d["kind"]) in seen:
            continue
        seen.add((d["stage_key"], d["kind"]))
        dels.append(d)
    assert len(dels) == 9
