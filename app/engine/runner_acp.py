"""ACP runner: JSON-RPC to `dsh --profile acp`, live usage, cancel-based gate.

Why this exists: headless is a black box — the shell only learns the cost after
the process exits. ACP streams `usage_update` after every model call, so the
shell can cancel the turn the moment the reservation is crossed, stopping every
subsequent call. It cannot un-send the call already in flight; that last call is
the documented overshoot (DESIGN_GAPS §13).

v2.3: every `tool_call` notification is also a **permission gate**. If the
target lives under a managed resource the agent cannot write, the run is parked
(`awaiting_approval`) with approval_id + run_id + session_id; approving resumes
the same ACP session instead of re-running the prompt. Each thinking block and
tool call is recorded as its own trace span.
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys
import threading
import time

from fastapi import HTTPException

from .. import policy, rbac, trace
from ..audit import write_audit
from . import dsh_adapter as base
from .acp_client import AcpClient, AcpError

MOCK_ACP_AGENT = base.ROOT / "tools" / "mock_acp_agent.py"


def _agent_context(conn, task_id: str) -> tuple[str | None, str | None]:
    """(owner_employee_id, chat_id) —— 审批单要能指回「谁」和「哪个会话」。"""
    agent_id = chat_id = None
    try:
        row = conn.execute("SELECT owner_employee_id FROM tasks WHERE id=?",
                           (task_id,)).fetchone()
        if row and row["owner_employee_id"]:
            agent_id = row["owner_employee_id"]
    except Exception:                        # noqa: BLE001
        pass
    try:
        row = conn.execute("SELECT chat_id FROM chat_messages WHERE task_id=?"
                           " ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
        if row:
            chat_id = row["chat_id"]
    except Exception:                        # noqa: BLE001
        pass
    return agent_id, chat_id


def execute_acp(conn: sqlite3.Connection, run_id: str, *, task_id: str, entity: str,
                actor: str, prompt: str, runner: str, resume_session: str | None,
                est_tokens: int, timeout: int, hb_interval: int) -> dict:
    ws = base.task_workspace(task_id, actor)
    from . import sandbox_env
    env = sandbox_env.build_env(ws)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "MOCK_WORKSPACE": str(ws)})

    # ---- presets：standard 走 workspace-write + 外层沙箱；cordis 放开到 full access
    # （创造模式：允许 agent 用 DSH 原生插件扩展工具、装依赖，不再被审批卡住）
    preset = "standard"
    try:
        row = conn.execute(
            """SELECT e.preset FROM tasks t JOIN ai_employees e ON e.id = t.owner_employee_id
               WHERE t.id = ?""", (task_id,)).fetchone()
        if row and row["preset"]:
            preset = row["preset"]
    except Exception:                        # noqa: BLE001
        pass
    if preset == "cordis":
        env["DSH_PERMISSION_MODE"] = "danger-full-access"
        write_audit(conn, legal_entity_id=entity, verb="preset", task_id=task_id,
                    run_id=run_id, layer="system", action="cordis_enabled", target=task_id,
                    payload={"preset": preset, "mode": "danger-full-access"},
                    response={"sandbox": "off"})
        conn.commit()

    cmd = ([sys.executable, str(MOCK_ACP_AGENT)] if runner == "mock_acp"
           else [base.DSH_CLI, "--profile", "acp", "--patch",
                 str(base.ROOT / "tools" / "dsh-acp.patch.yml")])
    # ACP 子进程里 DSH 已经自带工具级沙箱（提权时会明确拒绝），再套一层 Low 完整性
    # 会形成嵌套启动，实测偶发死锁（RUN-a45bd37b26704874 卡在 0 token）。
    # M0_SANDBOX_ACP=0 时跳过外层包装；默认仍开启。
    if preset != "cordis" and os.environ.get("M0_SANDBOX_ACP", "1") == "1":
        cmd = base.sandbox_cmd(ws, cmd)

    live_log = base._log_path(run_id).with_suffix(".live.jsonl")
    client = AcpClient(cmd, cwd=str(ws), env=env, default_timeout=timeout)

    def on_started(process):
        base.register_process(run_id, process)
        base.ensure_not_stopped(conn, run_id)

    client.on_started = on_started
    stop = threading.Event()
    hb = base.start_heartbeat(run_id, hb_interval, stop)

    cap = est_tokens                      # absolute ceiling, corrected after the session is known
    baseline = 0
    agent_id, chat_id = _agent_context(conn, task_id)
    rec = trace.Recorder(conn, run_id)
    st = {"used": 0, "cancelled": False, "checks": 0, "writes": [], "final": "",
          "resumed": bool(resume_session), "stop_reason": None, "error": None,
          "sid": resume_session, "updates": 0, "tool_calls": 0, "policy_violation": None,
          "approval": None, "last_used": 0}
    prompt_started = False
    started = time.time()

    try:
        base.ensure_not_stopped(conn, run_id)
        client.start()
        base.ensure_not_stopped(conn, run_id)
        if resume_session:
            # A previous process may still be releasing its write handle; the lock
            # clears within a second or two once session/close lands.
            for attempt in range(3):
                try:
                    client.resume(resume_session)
                    break
                except AcpError as exc:
                    msg = str(exc)
                    if "active write handle" in msg and attempt < 2:
                        time.sleep(1.5)
                        continue
                    if "not resumable" in msg:
                        # the previous turn was cancelled mid-flight; DSH refuses to
                        # resume it. Better a fresh session than a dead retry.
                        st["resume_fallback"] = True
                        st["resumed"] = False
                        resume_session = None
                        break
                    raise
            if resume_session:
                sid = resume_session
            else:
                sid = client.new_session()["sessionId"]
        else:
            sid = client.new_session()["sessionId"]
        st["sid"] = sid
        baseline = base.session_baseline(conn, sid, run_id)
        cap = baseline + est_tokens      # usage_update is cumulative per session
        # span 的 token 归因从「本 run 已累计」开始；用 baseline 会把上一段
        # 会话的 token 一次性记到续跑的第一个 thinking 上（v2.3 踩过）。
        prev_used = conn.execute(
            "SELECT COALESCE(MAX(usage_tokens), 0) FROM task_runs WHERE session_id=? AND id=?",
            (sid, run_id)).fetchone()[0]
        st["last_used"] = max(int(prev_used or 0), baseline)

        conn.execute(
            "UPDATE task_runs SET pid = ?, runner = ?, state = 'running', session_id = ?"
            " WHERE id = ? AND state NOT IN ('cancelled', 'failed')",
            (client.pid, runner, sid, run_id))
        conn.commit()

        def on_update(update: dict) -> None:
            kind = update.get("sessionUpdate")
            event_ms, prev_event = rec._now(), rec.last_event
            rec.last_event = event_ms
            # ---- trace：每个 thinking 块 / tool_call 一根独立 span
            # thinking 的起点用「上一事件结束」——DSH 的 thought_chunk 是整块到达的，
            # 只有把模型等待时间算进去，瀑布图才看得出哪一步最耗时。
            if kind == "agent_thought_chunk":
                txt = (update.get("content") or {}).get("text") or ""
                mid = update.get("messageId") or "thought"
                rec.open_span(f"think:{mid}", "thinking",
                              "思考 · " + (txt.strip().replace("\n", " ")[:34] or "…"),
                              detail=txt[:400], start=prev_event)
            elif kind == "tool_call":
                rec.close_kind("thinking")
                label = update.get("title") or update.get("kind") or "tool"
                rec.open_span(f"tool:{update.get('toolCallId')}", "tool",
                              "工具 · " + str(label).split()[0],
                              detail=json.dumps(update.get("rawInput") or {},
                                                ensure_ascii=False)[:300])
            elif kind == "tool_call_update":
                status = update.get("status") or "ok"
                rec.close_span(f"tool:{update.get('toolCallId')}",
                               status="ok" if status == "completed" else status)
            # Whitebox feed for the console: every thought chunk and tool call
            # lands in run_stream so the UI can render the thinking chain live.
            try:
                from ..ui import store as ui_store
                if kind == "agent_thought_chunk":
                    txt = (update.get("content") or {}).get("text") or ""
                    if txt.strip():
                        ui_store.add_stream(conn, run_id, "thinking", txt)
                elif kind == "tool_call":
                    label = update.get("title") or update.get("kind") or "tool"
                    ui_store.add_stream(conn, run_id, "tool", str(label))
            except Exception:                    # noqa: BLE001 - never break a run
                pass
            # 边跑边落盘：老板要能「盯紧执行日志」，而不是等结束后才知道发生了什么
            try:
                with open(live_log, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"kind": kind, "update": update},
                                        ensure_ascii=False) + chr(10))
            except OSError:
                pass

            if kind == "usage_update":
                used = int(update.get("used") or 0)
                st["used"] = max(st["used"], used)
                try:
                    conn.execute("UPDATE task_runs SET usage_tokens=? WHERE id=?", (used, run_id))
                    conn.commit()
                except Exception:                # noqa: BLE001
                    pass
                delta = max(0, used - int(st.get("last_used") or 0))
                if delta:
                    rec.add_tokens(delta)
                    st["last_used"] = used
                st["checks"] += 1
                if used > cap and not st["cancelled"]:
                    st["cancelled"] = True
                    client.cancel(sid)
                    write_audit(
                        conn, legal_entity_id=entity, verb="budget_check", task_id=task_id,
                        run_id=run_id, layer="system", action="budget_cancel", target=run_id,
                        payload={"used": used, "cap": cap, "checks": st["checks"]},
                        response={"cancelled": True, "reason": "reservation_exceeded"})
                    conn.commit()
            elif kind == "tool_call":
                st["tool_calls"] += 1
                label = update.get("title") or update.get("kind") or ""
                tool = str(label).split()[0].lower() if label else ""
                verdict = rbac.gate_tool_call(
                    conn, agent_id=agent_id, tool=tool, update=update, run_id=run_id,
                    task_id=task_id, session_id=sid, chat_id=chat_id, workspace=ws,
                    entity=entity)
                if verdict.get("blocked"):
                    rec.close_span(f"tool:{update.get('toolCallId')}", status="blocked",
                                   detail=f"审批 {verdict.get('approval_id')} · "
                                          f"{verdict.get('resource_name') or ''}")
                    st["approval"] = dict(verdict)
                    if not st["cancelled"]:
                        st["cancelled"] = True
                        client.cancel(sid)
                        write_audit(
                            conn, legal_entity_id=entity, verb="rbac", task_id=task_id,
                            run_id=run_id, layer="system", action="run_parked",
                            target=run_id,
                            payload={"approval_id": verdict.get("approval_id"),
                                     "tool": tool, "target": verdict.get("target")},
                            response={"ok": True, "state": "awaiting_approval"})
                        conn.commit()
                    return
                if verdict.get("in_resource"):
                    # 受管资源由 RBAC 判权；不再套 workspace-only 写入策略
                    pass
                else:
                    pol = policy.evaluate(update, ws)
                    if pol["action"] == "deny":
                        # ACP has no approval request, but the notification arrives
                        # before the tool completes: cancel now, the sandbox is the backstop.
                        st["policy_violation"] = pol
                        if not st["cancelled"]:
                            st["cancelled"] = True
                            client.cancel(sid)
                            write_audit(
                                conn, legal_entity_id=entity, verb="tool_policy", task_id=task_id,
                                run_id=run_id, layer="system", action="tool_denied", target=run_id,
                                payload=pol, response={"cancelled": True})
                            conn.commit()
                        return
                    fp = pol.get("target")
                    title = (update.get("title") or "").lower()
                    if fp and title in base.WRITE_TOOLS:
                        # ACP runs with the task workspace as cwd. Resolve the
                        # permitted target there, never against the server cwd.
                        path = pathlib.Path(fp)
                        if not path.is_absolute():
                            path = ws / path
                        st["writes"].append(str(path.resolve()))
            elif kind == "agent_message_chunk":
                st["final"] += ((update.get("content") or {}).get("text") or "")

        base.ensure_not_stopped(conn, run_id)
        prompt_started = True
        out = client.prompt(sid, prompt, on_update=on_update, timeout=timeout)
        st["stop_reason"] = (out.get("result") or {}).get("stopReason")
        st["updates"] = out.get("updates", 0)
    except (AcpError, OSError) as exc:
        if not prompt_started:
            # A stop can close stdio during initialize; preserve a parked run's
            # existing usage instead of treating that EOF as a new model turn.
            base.ensure_not_stopped(conn, run_id)
        st["error"] = str(exc)
    except HTTPException:
        raise
    finally:
        stop.set()
        if hb:
            hb.join(timeout=2)
        rec.flush(status="blocked" if st.get("approval") else "ok")
        try:
            client.close(session_id=st.get("sid"))
        finally:
            base.unregister_process(run_id)

    base._log_path(run_id).write_text(
        json.dumps({"events": client.log, "stderr": client.stderr[-80:], "state": st},
                   ensure_ascii=False, indent=1),
        encoding="utf-8")

    ok_run = (not st["cancelled"]) and (not st["error"]) and st["stop_reason"] == "end_turn"
    files = base.collect_artifact_files(st["writes"], since=started, task_id=task_id,
                                        actor=actor, success=ok_run)
    artifacts = base.attach_files(conn, run_id, files)
    delta = max(0, st["used"] - baseline)
    base.settle_budget(conn, task_id, delta, est_tokens)

    cancelled, error = st["cancelled"], st["error"]
    if st["approval"]:
        # 挂起：任务状态不动（仍是 in_progress），Run 标记 awaiting_approval。
        appr = st["approval"]
        return base.park_run(
            conn, run_id=run_id, task_id=task_id, entity=entity, runner=runner,
            approval_id=appr.get("approval_id"), session_id=st["sid"], used=delta,
            event_count=st["updates"], final=st["final"],
            usage_total=st["used"], baseline=baseline,
            extra={"approval": appr, "target": appr.get("target"),
                   "resource_id": appr.get("resource_id"), "tool_calls": st["tool_calls"],
                   "parked_reason": appr.get("reason"),
                   "parked_ms": trace.now_run(conn, run_id)})

    ok = (not cancelled) and (not error) and st["stop_reason"] == "end_turn"
    if st["policy_violation"]:
        reason, exit_code = "tool_policy_violation", 4
    elif cancelled:
        reason, exit_code = "budget_exhausted_midturn", 3
    elif error:
        reason, exit_code = ("acp_timeout" if "timeout" in error.lower() else "acp_error"), 1
    elif st["stop_reason"] == "end_turn":
        reason, exit_code = "completed", 0
    else:
        reason, exit_code = f"stop_{st['stop_reason']}", 1

    return base.finish(
        conn, run_id=run_id, task_id=task_id, entity=entity, runner=runner, ok=ok,
        reason=reason, exit_code=exit_code, session_id=st["sid"],
        used=delta, event_count=st["updates"], artifacts=artifacts,
        final=st["final"], timed_out=(reason == "acp_timeout"),
        usage_total=st["used"], baseline=baseline,
        extra={"resumed": st["resumed"], "stop_reason": st["stop_reason"],
               "usage_total": st["used"], "usage_baseline": baseline,
               "gate": {"cap": cap, "cap_delta": est_tokens, "used": st["used"],
                        "baseline": baseline, "checks": st["checks"],
                        "cancelled": cancelled},
               "tool_calls": st["tool_calls"],
               "policy_violation": st["policy_violation"],
               "error": error})
