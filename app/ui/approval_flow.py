"""审批流：卡片、企微推送后的站内状态、批准后的断点续跑（v2.3）。

关键点：批准不是「再跑一遍」。审批单在拦截时已经记下 run_id + session_id，
这里用**同一个 run_id** 调 `execute_run(..., resume_session=session_id)`，
prompt 只带「继续刚才被拦截的一步」。token 按 session 基线结算，审批等待时间
单独落一根 approval span。
"""
from __future__ import annotations

import json
import os
import threading
import time

from .. import rbac, trace
from ..audit import write_audit
from ..db import connect
from ..engine import dsh_adapter
from . import store


def _chat_for(conn, approval) -> str | None:
    row = dict(approval)
    owner = rbac.approval_owner(conn, row)
    chat_id = row.get("chat_id")
    if chat_id:
        return chat_id  # approval_owner already validates an explicit chat.
    if not rbac._has_table(conn, "chats"):
        return None
    if row.get("task_id") and rbac._has_table(conn, "chat_messages"):
        for found in conn.execute("SELECT chat_id FROM chat_messages WHERE task_id=?"
                                   " ORDER BY id DESC", (row["task_id"],)):
            if rbac.approval_chat_allowed(conn, found["chat_id"], owner):
                return found["chat_id"]
    for found in conn.execute("SELECT id FROM chats WHERE kind='ceo' AND"
                               " COALESCE(tenant_id,'TENANT-HQ')=? ORDER BY id", (owner[0],)):
        if rbac.approval_chat_allowed(conn, found["id"], owner):
            return found["id"]
    return None


def post_card(conn, approval) -> None:
    row = dict(approval)
    chat_id = _chat_for(conn, row)
    if not chat_id:
        return
    if conn.execute("SELECT 1 FROM chat_messages WHERE chat_id=? AND kind='approval' AND meta LIKE ?",
                    (chat_id, f'%"{row["id"]}"%')).fetchone():
        return                                   # 幂等：刷新页面不重复发卡
    content = (f"⛔ 越权拦截：{row.get('agent_name') or row.get('agent_id')} 试图 "
               f"{row.get('tool')} `{row.get('target')}`\n"
               f"资源：{row.get('resource_name') or row.get('resource_id')} · "
               f"Run {row.get('run_id')} 已挂起（批准后从断点继续）")
    store.add_message(
        conn, chat_id, "agent", content, author="系统守卫", kind="approval",
        task_id=row.get("task_id"),
        meta=json.dumps({"approval_id": row.get("id"), "run_id": row.get("run_id"),
                         "resource_id": row.get("resource_id"), "tool": row.get("tool"),
                         "target": row.get("target"), "state": "pending",
                         "session_id": row.get("session_id")}, ensure_ascii=False))


def _wait_span(conn, approval_id: str) -> None:
    row = rbac.get(conn, approval_id)
    if row is None or not row["run_id"]:
        return
    wait_ms = conn.execute(
        """SELECT CAST((julianday(COALESCE(decided_at, CURRENT_TIMESTAMP))
                        - julianday(created_at)) * 86400000 AS INTEGER) AS ms
           FROM tool_approvals WHERE id=?""", (approval_id,)).fetchone()["ms"] or 0
    start = int(row["parked_at_ms"] or 0)
    trace.record(conn, row["run_id"], kind="approval",
                 name=f"等待审批 · {approval_id}",
                 started_ms=start, ended_ms=start + max(0, int(wait_ms)),
                 status="ok" if row["state"] == "approved" else "rejected",
                 detail=(f"resource={row['resource_id']} tool={row['tool']} "
                         f"state={row['state']} by={row['decided_by']}"),
                 audit_id=None)


def decide(approval_id: str, approve: bool, by: str) -> dict:
    """站内/接口审批：先落定状态，再把「续跑」丢给后台线程。"""
    with connect() as conn:
        row = rbac.get(conn, approval_id)
        if row is None:
            raise ValueError("approval not found")
        _, entity = rbac.approval_owner(conn, row)
        chat_id = _chat_for(conn, row)
        if row["state"] != "pending":
            return {"ok": True, "state": row["state"], "already": True,
                    "run_id": row["run_id"], "resumable": False}
        out = rbac.decide(conn, approval_id, approve, by=by)
        row = rbac.get(conn, approval_id)
        _wait_span(conn, approval_id)
        if not approve:
            out["resumable"] = False
            run = conn.execute("SELECT * FROM task_runs WHERE id=?", (row["run_id"],)).fetchone()
            task = conn.execute("SELECT state FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
            if run and run["state"] == "awaiting_approval" and task and task["state"] == "in_progress":
                from ..state_machine import transition
                transition(conn, row["task_id"], "run_failed", actor=by,
                           payload={"reason": "approval_rejected", "approval_id": approval_id})
                conn.execute("UPDATE task_runs SET state='failed', exit_code=9,"
                             " ended_at=CURRENT_TIMESTAMP WHERE id=?", (row["run_id"],))
                trace.finish_run(conn, row["run_id"], tokens=int(run["usage_tokens"] or 0), state="failed")
        if chat_id:
            store.add_message(
                conn, chat_id, "agent",
                (f"✅ 审批通过（{approval_id}，by {by}）"
                 + ("：使用原会话从断点继续，不重放前文。"
                    if approve and out.get("resumable") else "。")
                 if approve else f"⛔ 审批被拒绝（{approval_id}，by {by}），本次操作不再执行。"),
                author="系统守卫", kind="text", task_id=row["task_id"])
        write_audit(conn, legal_entity_id=entity, verb="rbac", task_id=row["task_id"],
                    run_id=row["run_id"], layer="system", action="approval_decided",
                    target=approval_id, payload={"approve": approve, "by": by},
                    response={"resume": bool(approve and out.get("resumable"))})
        conn.commit()

    if approve and out.get("resumable"):
        threading.Thread(target=resume, args=(approval_id,), daemon=True).start()
    return out


def resume(approval_id: str) -> dict:
    """后台线程：同一个 run_id + session_id 续跑。"""
    with connect() as conn:
        ap = rbac.get(conn, approval_id)
        if ap is None or ap["state"] != "approved" or not ap["run_id"]:
            return {"ok": False, "reason": "not resumable"}
        run = conn.execute("SELECT * FROM task_runs WHERE id=?", (ap["run_id"],)).fetchone()
        if run is None:
            return {"ok": False, "reason": "run missing"}
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (ap["task_id"],)).fetchone()
        if task is None or task["state"] != "in_progress" or run["state"] != "awaiting_approval":
            return {"ok": False, "reason": "task or run is no longer awaiting approval"}
        owner = rbac.approval_owner(conn, ap)
        entity = owner[1]
        chat_id = _chat_for(conn, ap)
        if not conn.execute("SELECT 1 FROM trace_spans WHERE trace_id IN"
                            " (SELECT trace_id FROM agent_traces WHERE run_id=?)"
                            " AND kind='approval' LIMIT 1", (ap["run_id"],)).fetchone():
            _wait_span(conn, approval_id)
        applied: dict = {}
        try:
            applied = rbac.apply_approval(conn, approval_id)
        except PermissionError:
            raise
        except Exception as exc:                     # noqa: BLE001
            applied = {"applied": False, "error": str(exc)}
            write_audit(conn, legal_entity_id=entity, verb="rbac", task_id=ap["task_id"],
                        run_id=run["id"], layer="system", action="approval_apply_failed",
                        target=approval_id, payload={"error": str(exc)},
                        response={"ok": False})
            conn.commit()
        prompt = (ap["resume_prompt"] or
                  "审批已通过（{approval_id}）。请继续刚才被拦截的一步，"
                  "不要重做已完成部分，完成后只回复 DONE。").format(approval_id=approval_id)
        if applied.get("applied"):
            prompt = (
                f"审批已通过（{approval_id}）。平台已代你执行被拦下的 {ap['tool']}：\n"
                f"目标 {applied['path']}（{applied['bytes']} 字节，sha256 "
                f"{str(applied.get('sha256') or '')[:12]}…）已落盘。\n"
                "不要再次写入这个文件；请读取确认内容后继续原任务，完成后只回复 DONE。")
        sid = ap["session_id"]
        runner = run["runner"] or "acp"
        start_ms = trace.now_run(conn, run["id"])
        if chat_id:
            if applied.get("applied"):
                store.add_message(
                    conn, chat_id, "agent",
                    f"📝 已按审批代为落盘：{applied['path']} · {applied['bytes']} 字节 · "
                    f"sha256 {str(applied.get('sha256') or '')[:12]}…",
                    author="系统守卫", kind="text", task_id=ap["task_id"])
            store.add_message(
                conn, chat_id, "agent",
                f"▶ 断点续跑：run {run['id']} 复用会话 {str(sid)[:8]}…，"
                "只补被拦下的那一步（不重放 prompt）。",
                author="系统守卫", kind="text", task_id=ap["task_id"])
        write_audit(conn, legal_entity_id=entity, verb="rbac", task_id=ap["task_id"],
                    run_id=run["id"], layer="system", action="resume_started",
                    target=approval_id, payload={"session_id": sid, "runner": runner},
                    response={"ok": True})
        conn.commit()

    out: dict = {}
    mock_env = None
    if runner == "mock_acp":
        # 零成本演练：mock 不读 prompt 内容，目标路径从审批单的 rawInput 恢复
        try:
            args = json.loads(ap["args"] or "{}")
        except (ValueError, TypeError):
            args = {}
        mock_env = (os.environ.get("MOCK_ACP_TARGET"), os.environ.get("MOCK_ACP_TEXT"))
        os.environ["MOCK_ACP_TARGET"] = args.get("file_path") or ap["target"] or ""
        os.environ["MOCK_ACP_TEXT"] = args.get("content") or ""
    try:
        with connect() as conn:
            current = rbac.get(conn, approval_id)
            if current is None or rbac.approval_owner(conn, current) != owner:
                raise PermissionError("ApprovalScopeMismatch")
            chat_id = _chat_for(conn, current)
            out = dsh_adapter.execute_run(conn, ap["run_id"], prompt=prompt, runner=runner,
                                          resume_session=sid)
    except PermissionError:
        raise
    except Exception as exc:                         # noqa: BLE001
        with connect() as conn:
            current = rbac.get(conn, approval_id)
            if current is not None and rbac.approval_owner(conn, current) == owner:
                chat_id = _chat_for(conn, current)
                if chat_id:
                    store.add_message(conn, chat_id, "agent", f"断点续跑失败：{exc}",
                                      author="系统守卫", kind="text", task_id=ap["task_id"])
        return {"ok": False, "error": str(exc)}
    finally:
        if mock_env is not None:
            for key, val in zip(("MOCK_ACP_TARGET", "MOCK_ACP_TEXT"), mock_env):
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val

    with connect() as conn:
        current = rbac.get(conn, approval_id)
        if current is None or rbac.approval_owner(conn, current) != owner:
            raise PermissionError("ApprovalScopeMismatch")
        chat_id = _chat_for(conn, current)
        end_ms = trace.now_run(conn, ap["run_id"])
        trace.record(conn, ap["run_id"], kind="resume",
                     name="断点续跑（session/resume）",
                     started_ms=start_ms, ended_ms=end_ms,
                     status="ok" if out.get("exit_code") == 0 else "failed",
                     detail=f"session={sid} runner={runner} fallback={out.get('resume_fallback')}")
        if not out.get("approval_id"):
            trace.finish_run(conn, ap["run_id"],
                             tokens=int(out.get("usage_total") or out.get("usage_tokens") or 0),
                             state="done" if out.get("task_state") else "failed")
            # 本次续跑用到的批准单（含只读回执）统一标记已消费，避免长期挂着
            conn.execute("UPDATE tool_approvals SET consumed_at=COALESCE(consumed_at,"
                         " CURRENT_TIMESTAMP) WHERE run_id=? AND state='approved'",
                         (ap["run_id"],))
            conn.commit()
        state = out.get("task_state") or out.get("state")
        if out.get("state") == "awaiting_approval":
            msg = (f"⛔ 续跑时又被拦截：{out.get('approval_id')}。"
                   "请到「审批」页处理新的单。")
        else:
            msg = (f"🏁 断点续跑完成：state={state} · exit={out.get('exit_code')} · "
                   f"本轮 tokens={out.get('usage_tokens')}（会话累计 "
                   f"{out.get('usage_total')}）")
        if chat_id:
            store.add_message(conn, chat_id, "agent", msg, author="系统守卫",
                              kind="text", task_id=ap["task_id"])
        if state == "pending_review":
            try:
                from .. import acceptance_run
                acceptance_run.run(conn, ap["task_id"], [entity])
            except Exception:                        # noqa: BLE001
                pass
        conn.commit()
    return {"ok": True, **out}


def list_all(conn, limit: int = 50) -> dict:
    return {"pending": rbac.pending(conn), "history": rbac.history(conn, limit),
            "count": len(rbac.pending(conn))}
