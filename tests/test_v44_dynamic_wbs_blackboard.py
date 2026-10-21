"""Test suite for V4.4: Blackboard Context Propagation & Dynamic WBS Decomposition."""
from __future__ import annotations

import json
import sqlite3
import pytest

from app import flows, pipeline
from app.db import get_conn, init_db


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("M0_DISPATCH_SYNC", "1")
    monkeypatch.setenv("M0_PIPELINE_EXEC", "0")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "v44.db"))
    init_db()
    with get_conn() as c:
        pipeline.ensure(c)
        flows.ensure(c)
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type) VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
        c.commit()
        yield c


def test_blackboard_write_dedup(conn):
    pid = "P-TEST-DEDUP"
    conn.execute("INSERT OR IGNORE INTO projects (id, name, requirement) VALUES (?, 'test', 'test')", (pid,))
    
    aid1 = flows.blackboard_write(conn, pid=pid, stage_key="intake", role_tag="product",
                                 title="需求说明", content="核心目标：完成官网落地页",
                                 file_path="deliveries/00_intake.md")
    aid2 = flows.blackboard_write(conn, pid=pid, stage_key="intake", role_tag="product",
                                 title="需求说明", content="核心目标：完成官网落地页",
                                 file_path="deliveries/00_intake.md")
    assert aid1 == aid2, "Same file_path should deduplicate and return existing id"
    rows = conn.execute("SELECT COUNT(*) c FROM project_artifacts WHERE project_id=?", (pid,)).fetchone()[0]
    assert rows == 1


def test_get_blackboard_context(conn):
    pid = "P-TEST-BB-CTX"
    conn.execute("INSERT OR IGNORE INTO projects (id, name, requirement) VALUES (?, 'test', 'test')", (pid,))
    
    flows.blackboard_write(conn, pid=pid, stage_key="intake", role_tag="product",
                           title="立项备忘", content="预算50k，面向高端零售客户",
                           file_path="deliveries/00_intake.md")
    flows.blackboard_write(conn, pid=pid, stage_key="workshop", role_tag="architect",
                           title="架构研讨结论", content="必须采用静态单页HTML，禁止引入外部臃肿框架",
                           file_path="deliveries/02_workshop.md")

    ctx = pipeline.get_blackboard_context(conn, pid)
    assert "【项目上游已交付结论与上下文】" in ctx
    assert "[intake]" in ctx
    assert "预算50k" in ctx
    assert "[workshop]" in ctx
    assert "静态单页HTML" in ctx


def test_dynamic_wbs_decomposition(conn):
    pid = "P-TEST-WBS-DYNAMIC"
    conn.execute("INSERT OR IGNORE INTO projects (id, name, requirement) VALUES (?, 'test', 'test')", (pid,))
    
    # Compound web + api + content + qa request
    req = "做一套高端品牌官网落地页（HTML），设计后端API接口契约，撰写发布文案，并输出功能测试报告"
    res = pipeline.build_wbs(conn, pid, req, llm=False)
    
    items = conn.execute("SELECT code, title, role_tag, pack_id, depends_on FROM wbs_items WHERE project_id=? ORDER BY code", (pid,)).fetchall()
    item_map = {r["code"]: dict(r) for r in items}
    
    assert "T1" in item_map, "Must have T1 web dev"
    assert item_map["T1"]["pack_id"] == "web-v1"
    assert item_map["T1"]["role_tag"] == "dev"
    
    assert "T1B" in item_map, "Must have T1B api architect"
    assert item_map["T1B"]["role_tag"] == "architect"
    
    assert "T2" in item_map, "Must have T2 content"
    assert item_map["T2"]["role_tag"] == "content"
    assert "T1" in json.loads(item_map["T2"]["depends_on"])
    
    assert "T3Q" in item_map, "Must have T3Q QA test"
    assert item_map["T3Q"]["role_tag"] == "qa"
    deps = json.loads(item_map["T3Q"]["depends_on"])
    assert "T1" in deps and "T1B" in deps


def test_execute_build_context_injection(conn):
    pid = "P-TEST-BUILD-CTX"
    conn.execute("INSERT OR IGNORE INTO projects (id, name, requirement, owner_identity) VALUES (?, 'test', '全渠道营销官网', 'ID-7f3ed41c41ae45f9')", (pid,))
    
    # Write upstream blackboard artifact
    flows.blackboard_write(conn, pid=pid, stage_key="workshop", role_tag="architect",
                           title="技术规范", content="主文件必须是 index.html，内联完整CSS样式",
                           file_path="deliveries/02_workshop.md")
    
    # Build WBS
    pipeline.build_wbs(conn, pid, "做一个全渠道营销官网，带前端html页面与宣传文案")
    
    # Execute build in stub mode
    res = pipeline.execute_build(conn, pid, "做一个全渠道营销官网，带前端html页面与宣传文案")
    
    # Check created tasks
    tasks = conn.execute("SELECT id, title, pack_id FROM tasks WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    assert len(tasks) >= 1
    
    # Verify audit log recorded the enriched prompt/requirement containing blackboard context
    logs = conn.execute("SELECT payload FROM ai_audit_logs WHERE task_id=? AND action='create_task'", (tasks[0]["id"],)).fetchone()
    assert logs is not None
    payload = json.loads(logs["payload"])
    text = payload.get("text", "")
    assert "【项目上游已交付结论与上下文】" in text
    assert "主文件必须是 index.html" in text
