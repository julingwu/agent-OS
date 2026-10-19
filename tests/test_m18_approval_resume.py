"""M18：越权拦截 → 审批单关联 run/session → 批准后断点续跑（v2.3）。

用真实的 mock ACP 子进程走完整链路：tool_call 通知先到达 → 闸门拦下并 cancel →
Run 挂起（任务仍是 in_progress）→ 批准 → 同一个 run_id + session_id 续跑 →
写盘成功、审批被消费、trace 有 thinking/tool/approval/resume 四类 span。
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from app import rbac, resources, trace
from app.db import get_conn, init_db


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "m18.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    monkeypatch.setenv("M0_SANDBOX_ACP", "0")
    monkeypatch.setenv("M0_HEARTBEAT_SECONDS", "0")
    monkeypatch.setenv("MOCK_ACP_TARGET", "")
    monkeypatch.setenv("MOCK_ACP_TEXT", "")
    init_db()
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type)"
                  " VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
        c.execute("""INSERT OR IGNORE INTO ai_employees
                     (id, legal_entity_id, name, model, status, preset)
                     VALUES ('AI-QUANT-01','LE-GROUP-HQ','小码','deepseek-chat','online','standard')""")
        c.commit()
        yield c


def _spawn(conn, task_id: str) -> str:
    from app.api import verbs
    from app.models import SpawnRunIn
    out = verbs.do_spawn_run(
        conn, SpawnRunIn(task_id=task_id, run_seq=1, legal_entity_id="LE-GROUP-HQ"),
        ident={"id": "ID-system", "role": "ceo", "legal_entity_id": "LE-GROUP-HQ"})
    conn.commit()
    return out["run_id"]


def _task(conn, text: str = "RBAC 越权演练") -> str:
    from app.audit import new_id
    tid = new_id("T")
    conn.execute("""INSERT INTO tasks (id, legal_entity_id, task_type, title, state,
                                      owner_employee_id, pack_id)
                    VALUES (?, 'LE-GROUP-HQ', 'rbac_drill', ?, 'todo', 'AI-QUANT-01', NULL)""",
                 (tid, text))
    conn.commit()
    return tid


def test_cross_resource_write_is_parked_and_resumes(conn, monkeypatch):
    from app.engine import dsh_adapter
    from app.ui import approval_flow

    view = resources.ensure_demo(conn)
    assert {r["id"] for r in view["resources"]} >= {"RES-DEV", "RES-FIN"}
    assert not rbac.has(conn, "AI-QUANT-01", "RES-FIN", "write")

    tid = _task(conn)
    run_id = _spawn(conn, tid)
    label = "越权写入_test.md"
    monkeypatch.setenv("MOCK_ACP_TARGET", f"shared/RES-FIN/{label}")
    monkeypatch.setenv("MOCK_ACP_TEXT", "should be blocked\n")

    out = dsh_adapter.execute_run(conn, run_id, prompt="写一份薪资汇总到 RES-FIN",
                                  runner="mock_acp", budget_tokens=8000)
    assert out["state"] == "awaiting_approval", out
    assert out["resumable"] is True

    ap = rbac.get(conn, out["approval_id"])
    assert ap is not None
    assert ap["run_id"] == run_id
    assert ap["session_id"], "审批单必须带 ACP session_id，否则无法断点续跑"
    assert ap["resource_id"] == "RES-FIN"
    run = conn.execute("SELECT * FROM task_runs WHERE id=?", (run_id,)).fetchone()
    assert run["state"] == "awaiting_approval"
    assert run["approval_id"] == ap["id"]
    assert conn.execute("SELECT state FROM tasks WHERE id=?", (tid,)).fetchone()["state"] == "in_progress"
    target = pathlib.Path(resources.root()) / "RES-FIN" / label
    assert not target.exists(), "闸门拦下后不应该真的写进财务目录"

    # trace 至少已经有 thinking / tool(blocked)
    tr = trace.load(conn, run_id)
    kinds = {s["kind"] for s in tr["spans"]}
    assert "thinking" in kinds and "tool" in kinds

    # 批准 → 同一个 run_id + session_id 续跑
    rbac.decide(conn, ap["id"], True, by="ID-boss")
    result = approval_flow.resume(ap["id"])
    assert result["ok"] is True, result
    assert result["exit_code"] == 0, result
    assert target.exists(), "批准后应该真的写入"
    assert target.read_text(encoding="utf-8").strip() == "should be blocked"

    ap2 = rbac.get(conn, ap["id"])
    assert ap2["consumed_at"], "一次性审批必须被消费"
    assert ap2["applied_at"], "平台应按审批单代执行写操作"
    import hashlib
    assert ap2["applied_sha256"] == hashlib.sha256(b"should be blocked\n").hexdigest()
    assert ap2["applied_bytes"] == len(b"should be blocked\n")
    run2 = conn.execute("SELECT * FROM task_runs WHERE id=?", (run_id,)).fetchone()
    assert run2["state"] == "succeeded"
    assert conn.execute("SELECT state FROM tasks WHERE id=?", (tid,)).fetchone()["state"] == "pending_review"

    tr = trace.load(conn, run_id)
    kinds = {s["kind"] for s in tr["spans"]}
    assert {"thinking", "tool", "approval", "resume"} <= kinds, kinds
    ap_span = [s for s in tr["spans"] if s["kind"] == "approval"][0]
    assert ap_span["ended_ms"] >= ap_span["started_ms"]


def test_gate_links_run_and_creates_pending(conn):
    resources.ensure_demo(conn)
    tid = _task(conn)
    run_id = _spawn(conn, tid)
    target = str(resources.root() / "RES-FIN" / "x.md")
    out = rbac.gate_tool_call(
        conn, agent_id="AI-QUANT-01", tool="write",
        update={"sessionUpdate": "tool_call", "title": "write",
                "rawInput": {"file_path": target}},
        run_id=run_id, task_id=tid, session_id="sess-123", workspace=resources.root())
    assert out["blocked"] is True
    ap = rbac.get(conn, out["approval_id"])
    assert ap["run_id"] == run_id and ap["session_id"] == "sess-123"
    assert json.loads(ap["args"])["file_path"] == target
    assert rbac.pending(conn)[0]["id"] == out["approval_id"]
