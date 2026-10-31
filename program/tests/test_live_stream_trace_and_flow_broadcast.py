import json
import pytest
from fastapi.testclient import TestClient

from app import flows, identity, pipeline, trace
from app.db import connect, init_db
from app.engine.runner_native import NativeKernel, execute_native
from app.ui import api as ui_api, dispatcher, roles as roles_mod, store


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "stream_trace.db"
    for key, value in {
        "DB_PATH": str(db_file),
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "M0_PIPELINE_EXEC": "1",
        "M0_DISPATCH_SYNC": "1",
        "M0_INTENT_LLM": "0",
        "UI_SESSION_SECRET": "test-stream-secret",
        "WECOM_MOCK": "1",
    }.items():
        monkeypatch.setenv(key, value)
    init_db()
    conn = connect(str(db_file))
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type,tenant_id)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq','TENANT-HQ')")
    roles_mod.ensure(conn)
    store.ensure(conn)
    pipeline.ensure(conn)
    flows.ensure(conn)
    trace.ensure(conn)
    ceo = identity.issue(conn, name="test_ceo", role="ceo")
    for tag in ("dev", "qa", "pm", "data", "research", "audit"):
        r = conn.execute("SELECT id FROM role_templates WHERE tag=? LIMIT 1", (tag,)).fetchone()
        if r:
            roles_mod.instantiate(conn, r["id"], "LE-GROUP-HQ")
    conn.commit()
    yield conn, ceo
    conn.close()


def test_native_kernel_emits_trace_spans(env):
    conn, ceo = env
    task_id = "T-NATIVE-TRACE-1"
    run_id = "RUN-NATIVE-TRACE-1"
    conn.execute("INSERT OR REPLACE INTO tasks(id, legal_entity_id, tenant_id, task_type, title, state)"
                 " VALUES (?, 'LE-GROUP-HQ', 'TENANT-HQ', 'web-v1', 'ROI计算器', 'in_progress')",
                 (task_id,))
    conn.execute("INSERT OR REPLACE INTO task_runs(id, task_id, run_seq, legal_entity_id, tenant_id, state, runner, actor_identity)"
                 " VALUES (?, ?, 1, 'LE-GROUP-HQ', 'TENANT-HQ', 'spawned', 'native', ?)",
                 (run_id, task_id, ceo["id"]))
    conn.commit()

    # Start trace and execute native
    trace.start(conn, run_id=run_id, task_id=task_id)
    kernel = NativeKernel(conn, run_id=run_id, task_id=task_id, entity="LE-GROUP-HQ",
                          actor=ceo["id"], prompt="构建计算器")

    # Test emit_thought
    kernel.emit_thought("思考测试：设计数据结构")
    # Test emit_tool_call & result
    kernel.emit_tool_call("call_test_1", "read_file", {"path": "index.html"})
    kernel.emit_tool_result("call_test_1", "read_file", "文件内容读取完毕", ok=True)
    # Test emit_message
    kernel.emit_message("最终交付消息")

    # Verify trace spans were written into database
    spans = conn.execute(
        "SELECT s.kind, s.name, s.detail, s.status FROM trace_spans s JOIN agent_traces t ON t.trace_id=s.trace_id"
        " WHERE t.run_id=? ORDER BY s.rowid", (run_id,)).fetchall()
    
    kinds = [s["kind"] for s in spans]
    assert "thinking" in kinds
    assert "tool" in kinds
    assert "model" in kinds

    thinking_span = next(s for s in spans if s["kind"] == "thinking")
    assert thinking_span["name"] == "模型思考"
    assert "思考测试" in thinking_span["detail"]

    tool_span = next(s for s in spans if s["name"] == "read_file")
    assert "index.html" in tool_span["detail"]

    result_span = next(s for s in spans if s["name"] == "read_file_result")
    assert result_span["status"] == "ok"
    assert "读取完毕" in result_span["detail"]

    msg_span = next(s for s in spans if s["kind"] == "model")
    assert msg_span["name"] == "模型回答"
    assert "最终交付消息" in msg_span["detail"]


def test_execute_native_sets_running_state_and_generates_spans(env):
    conn, ceo = env
    task_id = "T-NATIVE-RUN-2"
    run_id = "RUN-NATIVE-RUN-2"
    conn.execute("INSERT OR REPLACE INTO tasks(id, legal_entity_id, tenant_id, task_type, title, state)"
                 " VALUES (?, 'LE-GROUP-HQ', 'TENANT-HQ', 'web-v1', 'ROI计算器', 'in_progress')",
                 (task_id,))
    conn.execute("INSERT OR REPLACE INTO task_runs(id, task_id, run_seq, legal_entity_id, tenant_id, state, runner, actor_identity)"
                 " VALUES (?, ?, 1, 'LE-GROUP-HQ', 'TENANT-HQ', 'spawned', 'native', ?)",
                 (run_id, task_id, ceo["id"]))
    conn.commit()

    # Run execute_native (which should start trace and immediately set state='running' then finish with 'succeeded')
    res = execute_native(conn, run_id, task_id=task_id, entity="LE-GROUP-HQ",
                         actor=ceo["id"], prompt="构建赛博霓虹 FinOps 计算器")
    assert res["task_state"] in ("pending_review", "completed")

    # Verify trace spans are non-empty
    spans = conn.execute(
        "SELECT s.kind, s.name FROM trace_spans s JOIN agent_traces t ON t.trace_id=s.trace_id"
        " WHERE t.run_id=?", (run_id,)).fetchall()
    assert len(spans) > 0
    kinds = {s["kind"] for s in spans}
    assert "thinking" in kinds
    assert "tool" in kinds


def test_pipeline_stage_broadcast_and_non_software_flow_verdict(env, tmp_path):
    conn, ceo = env
    # Route a non-software flow (e.g. 分析黄金走势 -> FLOW-ANALYSIS)
    res = dispatcher.route(conn, "分析黄金走势", actor_id=ceo["id"])
    pid = res["project_id"]
    chat_id = res["chat_id"]

    # Check broadcast messages in collaboration group chat
    messages = conn.execute(
        "SELECT content, kind, meta FROM chat_messages WHERE chat_id=? AND kind='progress' ORDER BY id ASC",
        (chat_id,)).fetchall()
    
    # There should be progress messages emitted for stages
    assert len(messages) > 0
    msg_texts = [m["content"] for m in messages]
    has_started = any("阶段已启动" in t for t in msg_texts)
    has_completed = any("阶段已完成" in t for t in msg_texts)
    assert has_started, f"Expected started broadcast in: {msg_texts}"
    assert has_completed, f"Expected completed broadcast in: {msg_texts}"

    # Test non-software flow verdict with human_check criteria
    # Create a task for an analysis stage with a blocking human_check criterion
    task_id = "T-ANALYSIS-HUMAN-1"
    run_id = "RUN-ANALYSIS-HUMAN-1"
    conn.execute("INSERT OR REPLACE INTO tasks(id, project_id, stage, legal_entity_id, tenant_id, task_type, title, state)"
                 " VALUES (?, ?, 'retrieve', 'LE-GROUP-HQ', 'TENANT-HQ', 'analysis', '检索资料', 'in_progress')",
                 (task_id, pid))
    conn.execute("INSERT OR REPLACE INTO task_runs(id, task_id, run_seq, legal_entity_id, tenant_id, state)"
                 " VALUES (?, ?, 1, 'LE-GROUP-HQ', 'TENANT-HQ', 'succeeded')",
                 (run_id, task_id))
    # Add an artifact so arts > 0
    art_path = tmp_path / "01_retrieve.md"
    art_path.write_text("# 检索结论\n数据合规正常，符合要求。", encoding="utf-8")
    conn.execute("INSERT OR REPLACE INTO artifacts(id, run_id, tenant_id, art_seq, file_path, checksum, bytes)"
                 " VALUES ('ART-TEST-1', ?, 'TENANT-HQ', 1, ?, 'dummy', 100)",
                 (run_id, str(art_path)))
    # Add a blocking human_check criterion
    conn.execute("INSERT OR REPLACE INTO acceptance_criteria(id, task_id, seq, criterion, check_type, is_blocking, state, legal_entity_id)"
                 " VALUES ('CRIT-HC-1', ?, 1, '人工复核检索来源', 'human_check', 1, 'pending', 'LE-GROUP-HQ')",
                 (task_id,))
    conn.commit()

    # Call _flow_verdict for this non-software flow
    node = {"key": "retrieve", "title": "资料检索", "role": "research"}
    verdict_state = pipeline._flow_verdict(conn, task_id, run_id, node)
    
    # In non-interactive pipeline mode for non-software flows,
    # it should safely transition to completed and return "completed"
    assert verdict_state == "completed"
    task_state = conn.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()["state"]
    assert task_state == "completed"

    # The human_check criterion should be marked passed
    crit_state = conn.execute("SELECT state FROM acceptance_criteria WHERE id='CRIT-HC-1'").fetchone()["state"]
    assert crit_state == "passed"
