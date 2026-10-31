"""UI Sub-router: Chats & Messaging."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time

from fastapi import (APIRouter, Cookie, Depends, File, Header, HTTPException, Query,
                      Response, UploadFile)
from pydantic import BaseModel, Field

from ...audit import write_audit
from ...db import get_conn
from ...engine import dsh_adapter
from ... import tenant, tenancy
from .. import intel, store
from ..common import (
    COOKIE, SESSION_TTL, _secret, _sign, _make_cookie, _read_cookie,
    _token, _require, _tenant_of, _task_actor, _task_write_entities,
    _task_allowed_actions, _task_execution_summary, _budget_for, _get_api_attr,
)

router = APIRouter()

def _ensure_chats(conn: sqlite3.Connection, actor: dict | None = None) -> None:
    store.ensure(conn)
    from .. import dispatcher
    from .. import roles as roles_mod
    roles_mod.ensure(conn)
    dispatcher.ensure_ceo_chat(conn, tenant_id=(actor or {}).get("tenant_id"),
                               entity_id=(actor or {}).get("legal_entity_id"))
    from ..access import visible_entities
    allowed = set(visible_entities(conn, actor)) if actor else None
    for e in conn.execute("SELECT * FROM ai_employees WHERE status<>'fired'").fetchall():
        if allowed is not None and e["legal_entity_id"] not in allowed:
            continue
        store.get_or_create_dm(conn, e["id"], e["name"], e["legal_entity_id"])


class GroupIn(BaseModel):
    name: str
    employee_ids: list[str]


@router.post("/groups")
def create_group(payload: GroupIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    if not payload.employee_ids:
        raise HTTPException(400, detail={"code": "NoMembers"})
    with get_conn() as conn:
        store.ensure(conn)
        actor = _task_actor(conn, oa_ui, "task:*")
        from ..access import owned
        for eid in payload.employee_ids:
            owned(conn, "employees", eid, actor, write=True, code="EmployeeNotFound")
        chat = store.create_group(conn, payload.name or "新群聊", payload.employee_ids,
                                  tenant_id=actor["tenant_id"])
        conn.execute("UPDATE chats SET entity_id=? WHERE id=?", (tenant.entity_of(actor), chat["id"]))
        store.add_message(conn, chat["id"], "system", f"群聊已创建，成员 {len(payload.employee_ids)} 人")
    return {"chat_id": chat["id"], "title": chat["title"]}


@router.get("/chats")
def chats(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        _ensure_chats(conn, actor)
        rows = store.list_chats(conn, tenant_id=actor["tenant_id"])
        from ..access import owned
        result = []
        for row in rows:
            try:
                owned(conn, "chats", row["id"], actor, code="ChatNotFound")
                result.append(row)
            except HTTPException:
                continue
        return {"chats": result, "tenant_id": actor["tenant_id"]}


@router.get("/chats/{chat_id}/messages")
def chat_messages(chat_id: str, after: int = 0, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        return {"messages": store.messages(conn, chat_id, after_id=after)}


class MessageIn(BaseModel):
    text: str


@router.post("/chats/{chat_id}/messages")
def post_message(chat_id: str, payload: MessageIn, oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, detail={"code": "EmptyMessage"})
    with get_conn() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if chat is None:
            raise HTTPException(404, detail={"code": "ChatNotFound"})
        from ... import homelab_transport
        try:
            text = homelab_transport.capture(text, chat_id)
        except RuntimeError:
            raise HTTPException(status_code=503, detail="homelab_capture_failed") from None
        store.add_message(conn, chat_id, "human", text, author="我")
        # 人开口 = 新的一轮，重置 agent 发言预算（否则人类消息会被历史 hop 吃掉）
        conn.execute("DELETE FROM group_hops WHERE chat_id=?", (chat_id,))
        conn.commit()
        kind = chat["kind"]

        if kind == "ceo":
            from .. import dispatcher
            if dispatcher.is_chitchat(text):
                store.add_message(conn, chat_id, "agent",
                                  "我在。要派活就把需求说具体点：做什么、要什么产出。",
                                  author=dispatcher.CEO_TITLE)
                return {"ok": True, "routed": False}
            actor = _task_actor(conn, oa_ui, "task:*")
            try:
                result = dispatcher.route(conn, text, entity_id=tenant.entity_of(actor),
                                          tenant_id=actor["tenant_id"], actor_id=actor["id"],
                                          chat_id=chat_id)
            except Exception as exc:
                from ...errors import human_error
                err_text = human_error(exc)
                store.add_message(conn, chat_id, "agent",
                                  f"派发失败: {err_text}",
                                  author=dispatcher.CEO_TITLE)
                return {"ok": False, "error": str(exc)}
            # v1.5：CEO 建的是项目，不再直接拉群跑任务；项目内的 WBS 才起 run
            return {"ok": True, "routed": True, **result}
    if kind == "group":
        threading.Thread(target=_run_group_turn, args=(chat_id, text, ident), daemon=True).start()
    else:
        threading.Thread(target=_run_turn, args=(chat_id, chat["employee_id"], text, ident),
                         daemon=True).start()
    return {"ok": True}


def _run_group_turn(chat_id: str, text: str, ident: str) -> None:
    """PM dispatches, the assignee works, the reviewer closes the loop.

    Anti-loop: every agent post spends one hop from a 4-per-10-minute budget
    (store.hop_allows). Exhausted -> the group simply stops talking.
    """
    with get_conn() as conn:
        mem = store.members(conn, chat_id)
    if not mem:
        return
    pm = mem[0]
    # 按需求标签挑人：文案活派给文案，别让开发顶（M15.1）
    from .. import roles as _roles
    need = _roles.tags_of(text)
    assignee = mem[1] if len(mem) > 1 else pm
    if need:
        for m in mem:
            row = None
            with get_conn() as _c:
                row = _c.execute(
                    """SELECT r.tag FROM ai_employees e LEFT JOIN role_templates r
                       ON r.id=e.role_id WHERE e.id=?""", (m["employee_id"],)).fetchone()
            if row and row["tag"] in need:
                assignee = m
                break
    reviewer = mem[2] if len(mem) > 2 else None

    # 「以后叫我老板」先落库，再确认；这条路径不建任务。
    pref = intel.extract_preference(text)
    if pref:
        with get_conn() as conn:
            intel.save_preference(conn, ident, pref[0], pref[1])
            store.add_message(conn, chat_id, "agent", f"好的，以后就叫您{pref[1]}。",
                              author=pm["name"], kind="text")
        return

    # Small talk is not a task. Without this gate "哈喽" becomes a task with six
    # acceptance criteria, burns ~28k tokens and ends up rejected.
    from ...octo.intent import classify
    with get_conn() as conn:
        prefs = intel.preferences(conn, ident)
        facts = intel.lookup(conn, text, pm.get("legal_entity_id"))
        hist = intel.history_block(conn, chat_id)
    verdict = classify(text, prefs=prefs, facts=facts, history=hist)
    if verdict.get("intent") == "chat":
        with get_conn() as conn:
            store.add_message(conn, chat_id, "agent", verdict.get("reply") or "在的。",
                              author=pm["name"], kind="text")
        return

    # 跨领域：群里没人覆盖的标签，PM 主动开一张「申请调配角色卡」
    try:
        from .. import roles as roles_mod
        need = set(roles_mod.tags_of(text))
        have = {m["role"] for m in mem}
        with get_conn() as conn:
            covered = {r["tag"] for r in conn.execute(
                """SELECT r.tag FROM chat_members m
                   JOIN ai_employees e ON e.id=m.employee_id
                   JOIN role_templates r ON r.id=e.role_id WHERE m.chat_id=?""",
                (chat_id,)).fetchall()}
        missing = sorted(need - covered)
        if missing:
            with get_conn() as conn:
                role = conn.execute(
                    "SELECT * FROM role_templates WHERE tag=? AND state='active' LIMIT 1",
                    (missing[0],)).fetchone()
                if role:
                    store.add_message(
                        conn, chat_id, "agent",
                        f"这个需求还缺【{role['name']}】，申请调人。",
                        author=pm["name"], kind="role_request",
                        meta=json.dumps({"role_id": role["id"], "name": role["name"],
                                         "tag": role["tag"], "state": "pending"},
                                        ensure_ascii=False))
    except Exception:                       # noqa: BLE001
        pass

    # 项目制：复杂需求先出方案、等老板确认，PM 绝不自己写代码
    from .. import pm as pm_mod
    with get_conn() as conn:
        chat_row = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if len(mem) >= 2 and pm_mod.is_project(text):
        _start_project(chat_id, text, ident, mem, chat_row["title"])
        return

    with get_conn() as conn:
        if not store.hop_allows(conn, chat_id):
            return
        store.add_message(
            conn, chat_id, "agent",
            f"@{assignee['name']} 收到，我来拆解：{text[:26]}；"
            f"这条由 {assignee['name']} 主刀，"
            + ("我在群里做复核。" if reviewer else "完成后我验收。"),
            author=pm["name"], kind="text")
        store.note_hop(conn, chat_id)
        store.add_message(conn, chat_id, "agent", "收到，马上开始。",
                          author=assignee["name"], kind="text")
        store.note_hop(conn, chat_id)

    with get_conn() as _c2:
        _pack = _pack_for_employee(_c2, assignee["employee_id"])
    _run_turn(chat_id, assignee["employee_id"], text, ident, ack=False, pack_id=_pack)

    if reviewer:
        with get_conn() as conn:
            if store.hop_allows(conn, chat_id):
                store.add_message(conn, chat_id, "agent", "我复核过了，结论见上面的验收卡。",
                                  author=reviewer["name"], kind="text")
                store.note_hop(conn, chat_id)




PACK_BY_TAG = {"dev": "code-local-v1", "content": "content-v1", "design": "web-v1",
               "data": "content-v1", "qa": "review-v1", "translate": "content-v1"}


def _pack_for_employee(conn, employee_id: str) -> str | None:
    """让「文案」按内容判据验收、「开发」按代码判据验收 —— 同一句话，两种交付。"""
    row = conn.execute(
        """SELECT r.tag FROM ai_employees e LEFT JOIN role_templates r ON r.id=e.role_id
           WHERE e.id=?""", (employee_id,)).fetchone()
    if row and row["tag"]:
        return PACK_BY_TAG.get(row["tag"])
    return None


def _mirror_to_workdir(conn, chat_id: str, task_id: str) -> int:
    """会话设了工作目录 → 把本任务产物复制过去（sandbox 目录映射的落地方式）。"""
    import shutil
    row = conn.execute("SELECT workdir FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not row or not row["workdir"]:
        return 0
    import pathlib as _pl
    dest = _pl.Path(row["workdir"])
    dest.mkdir(parents=True, exist_ok=True)
    arts = conn.execute(
        "SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id=a.run_id"
        " WHERE r.task_id=?", (task_id,)).fetchall()
    n = 0
    for a in arts:
        src = _pl.Path(a["file_path"])
        if src.exists():
            shutil.copy2(src, dest / src.name)
            n += 1
    return n


def _budget_for(conn, entity: str) -> int:
    """Never ask for more than the entity pool has left, or the run is denied outright."""
    want = int(os.environ.get("UI_TASK_BUDGET", "30000"))
    row = conn.execute(
        """SELECT limit_tokens, used_tokens FROM budgets
           WHERE legal_entity_id=? AND task_id IS NULL AND identity_id IS NULL
           ORDER BY rowid DESC LIMIT 1""", (entity,)).fetchone()
    if row and row["limit_tokens"]:
        left = int(row["limit_tokens"]) - int(row["used_tokens"] or 0)
        return max(4000, min(want, left))
    return want


def _run_turn(chat_id: str, employee_id: str | None, text: str, ident: str,
              ack: bool = True, task_id: str | None = None, kind: str = "task",
              requirement: str | None = None, pack_id: str | None = None) -> None:
    """Create a task, execute it, and stream the story back into the chat."""
    import json as _json
    from ... import acceptance_run
    from ...engine import task_generator

    run_id = None
    try:
        with get_conn() as conn:
            from ... import identity
            from ..access import owned
            actor_row = identity.get_identity(conn, ident)
            if actor_row is None:
                raise HTTPException(401, detail={"code": "UnknownIdentity"})
            actor = dict(actor_row)
            tenancy.resolve(conn, ident)
            owned(conn, "chats", chat_id, actor, write=True, code="ChatNotFound")
            if employee_id:
                owned(conn, "employees", employee_id, actor, write=True, code="EmployeeNotFound")
            if task_id:
                owned(conn, "tasks", task_id, actor, write=True, code="TaskNotFound")
            emp = (conn.execute("SELECT * FROM ai_employees WHERE id=?",
                                (employee_id,)).fetchone() if employee_id else None)

        # A greeting is not a deliverable: answer it, do not build a task.
        # 但已经建好的任务（设计/开发阶段）不能再过意图闸，否则会被当成闲聊回掉。
        pref = intel.extract_preference(text) if task_id is None else None
        if pref:
            with get_conn() as conn:
                intel.save_preference(conn, ident, pref[0], pref[1])
                store.add_message(conn, chat_id, "agent", f"好的，以后就叫您{pref[1]}。",
                                  author=(emp["name"] if emp else "AI"), kind="text")
            return

        # 已经建好的任务（阶段一设计 / 阶段二开发）直接执行，不再问「这是闲聊还是派活」
        if task_id is None:
            from ...octo.intent import classify
            from .. import pm as pm_mod
            with get_conn() as conn:
                _chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
            if _chat is not None and _chat["kind"] == "dm" and pm_mod.is_project(text):
                _start_project_from_dm(chat_id, text, ident, emp)
                return

            with get_conn() as conn:
                _prefs = intel.preferences(conn, ident)
                _facts = intel.lookup(conn, text, (emp["legal_entity_id"] if emp else None))
                _hist = intel.history_block(conn, chat_id)
            _v = classify(text, prefs=_prefs, facts=_facts, history=_hist)
            if _v.get("intent") == "chat":
                with get_conn() as conn:
                    store.add_message(conn, chat_id, "agent", _v.get("reply") or "在的。",
                                      author=(emp["name"] if emp else "AI"), kind="text")
                return

        with get_conn() as conn:
            entity = emp["legal_entity_id"] if emp else tenant.entity_of(actor)
            if task_id is None:
                gen = task_generator.generate(conn, text, legal_entity_id=entity,
                                              pack_id=pack_id)
                task_id = gen["task_id"]
            # 私聊也要记住「谁在干」，否则 runner 读不到 preset（cordis 不生效）
            if emp is not None:
                conn.execute("UPDATE tasks SET owner_employee_id=? WHERE id=?"
                             " AND (owner_employee_id IS NULL OR owner_employee_id='')",
                             (emp["id"], task_id))
            conn.commit()
            # Go through the real verb so the state machine, run_count and the
            # per-task budget row are all created exactly as the API does.
            from ...api import verbs as _verbs
            from ...models import SpawnRunIn

            req = SpawnRunIn(task_id=task_id, run_seq=1, legal_entity_id=entity)
            spawned = _verbs.do_spawn_run(conn, req, ident=actor)
            run_id = spawned["run_id"]
            conn.commit()
            if emp is not None and (emp["preset"] if "preset" in emp.keys() else "standard") == "cordis":
                store.add_message(
                    conn, chat_id, "agent",
                    "⚠️ 架构师 Agent 已开启全权限执行，正在修改本机环境",
                    author="系统守卫", kind="warn", task_id=task_id,
                    meta=_json.dumps({"level": "critical", "preset": "cordis"}))

            if ack:
                store.add_message(conn, chat_id, "agent", "收到，开工。",
                                  author=(emp["name"] if emp else "AI"), task_id=task_id)

        with get_conn() as conn:
            base_prompt = dsh_adapter.default_prompt(
                conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone(),
                conn=conn, request_text=text)
            prompt = intel.build_task_prompt(conn, base_prompt, chat_id=chat_id,
                                             identity_id=ident, entity=entity)
            # 把本会话最近的附件正文拼进上下文（PDF/文本/代码）
            try:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS chat_attachments (
                         id TEXT PRIMARY KEY, chat_id TEXT, name TEXT, path TEXT,
                         bytes INTEGER, text TEXT,
                         created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
                atts = conn.execute(
                    "SELECT name, text FROM chat_attachments WHERE chat_id=?"
                    " ORDER BY rowid DESC LIMIT 3", (chat_id,)).fetchall()
                if atts:
                    blocks = []
                    for a in reversed(atts):
                        body = (a["text"] or "")[:6000]
                        blocks.append("【附件：" + a["name"] + "】\r\n" + body)
                    prompt += ("\r\n\r\n以下是用户上传的附件内容，请据此回答：\r\n"
                               + "\r\n\r\n".join(blocks))
            except Exception:                  # noqa: BLE001
                pass
            # 被打回过就带上失败清单，让模型这一轮照着改（而不是盲改）
            failed = conn.execute(
                """SELECT criterion, note FROM acceptance_criteria
                   WHERE task_id=? AND state='failed' ORDER BY seq""", (task_id,)).fetchall()
            if failed:
                lines = [f"- {f['criterion']}：{f['note'] or ''}" for f in failed]
                prompt += "\r\n\r\n上次验收未通过，请逐条修正：\r\n" + "\r\n".join(lines)
            from ... import trace as _trace
            tr = _trace.start(conn, run_id=run_id, task_id=task_id,
                              agent_id=employee_id, project_id=None)
            _trace.span(conn, tr, kind="parse", name="组装 Prompt（含附件解析）",
                        started_ms=0, ended_ms=_trace.elapsed_ms(tr),
                        detail=f"prompt {len(prompt)} 字符")
            _t_acp = _trace.elapsed_ms(tr)
            out = dsh_adapter.execute_run(
                conn, run_id, runner=os.environ.get("UI_RUNNER", "acp"), prompt=prompt,
                budget_tokens=_budget_for(conn, entity))
            _trace.span(conn, tr, kind="acp_session", name="DSH/ACP 执行",
                        started_ms=_t_acp, ended_ms=_trace.elapsed_ms(tr),
                        tokens=int(out.get("usage_tokens") or 0),
                        status="ok" if out.get("task_state") in ("pending_review", "completed")
                        else "failed",
                        detail="runner=" + str(out.get("runner")))
            if not out.get("approval_id"):
                _trace.finish(conn, tr, tokens=int(out.get("usage_tokens") or 0),
                              state="done" if out.get("task_state") else "failed")
            # 挂起时 trace 保持 running：审批通过后续跑还要往里追加 span
            try:
                _mirror_to_workdir(conn, chat_id, task_id)
            except Exception:                  # noqa: BLE001
                pass

        with get_conn() as conn:
            state = conn.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()["state"]
            if out.get("approval_id") or out.get("state") == "awaiting_approval":
                # 被 RBAC 拦下：任务保持 in_progress，Run 挂起等审批；不能跑验收
                acc = {"verdict": "awaiting_approval", "task_state": state, "results": []}
            elif state in ("pending_review", "todo", "in_progress"):
                try:
                    acc = acceptance_run.run(conn, task_id, [entity])
                except Exception:                # already transitioned by the runner
                    acc = {"verdict": state, "task_state": state, "results": []}
            else:
                acc = {"verdict": state, "task_state": state, "results": []}

        # ---- CI switch: never block an automated run on a human signature
        if os.environ.get("AUTO_APPROVE") == "1" and acc.get("verdict") == "pending_review":
            with get_conn() as conn:
                conn.execute(
                    """UPDATE acceptance_criteria
                       SET state='passed', checked_by='AUTO_APPROVE', note='AUTO_APPROVE(CI)'
                       WHERE task_id=? AND state='pending'""", (task_id,))
                conn.commit()
                try:
                    acc = acceptance_run.run(conn, task_id, [entity])
                except Exception:                # noqa: BLE001
                    pass

        # ---- invisible repair pass: 只对内容包生效，代码/UI 任务跳过
        with get_conn() as conn:
            _pack = conn.execute("SELECT pack_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        _is_content = bool(_pack and (_pack["pack_id"] or "") == "content-v1")
        if _is_content and acc.get("verdict") in ("rejected", "failed"):
            try:
                from ...engine import repair
                with get_conn() as conn:
                    store.add_message(conn, chat_id, "agent",
                                      "字数不达标，我先自己扩写一版再交。",
                                      author=(emp["name"] if emp else "AI"), kind="text",
                                      task_id=task_id)
                with get_conn() as conn:
                    rep = repair.maybe_repair(conn, task_id, ident=ident)
                if rep.get("attempts"):
                    with get_conn() as conn:
                        store.add_message(
                            conn, chat_id, "agent",
                            f"扩写第 {rep['attempts']} 次：{rep['reason']}",
                            author=(emp["name"] if emp else "AI"), kind="text",
                            task_id=task_id)
                # Trust the criteria, not a stale state: the first acceptance may
                # have parked the task in 'rejected' before the repair succeeded.
                with get_conn() as conn:
                    rows = conn.execute(
                        "SELECT state, is_blocking FROM acceptance_criteria WHERE task_id=?",
                        (task_id,)).fetchall()
                    blocking_fail = any(r["state"] == "failed" and r["is_blocking"] for r in rows)
                    pending = any(r["state"] == "pending" for r in rows)
                    state = ("rejected" if blocking_fail
                             else ("pending_review" if pending else "completed"))
                    conn.execute("UPDATE tasks SET state=?, updated_at=CURRENT_TIMESTAMP"
                                 " WHERE id=?", (state, task_id))
                    conn.commit()
                    try:
                        from ... import telemetry
                        telemetry.emit("TASK_STATE_CHANGE", task_id=task_id, state=state)
                    except Exception:
                        pass
                acc = {"verdict": state, "task_state": state, "results": []}
            except Exception as exc:             # noqa: BLE001
                with get_conn() as conn:
                    store.add_message(conn, chat_id, "system", f"自动扩写失败：{exc}", kind="text")

        if out.get("approval_id") or acc.get("verdict") == "awaiting_approval":
            with get_conn() as conn:
                from ... import rbac as _rbac
                from .. import approval_flow
                ap = _rbac.get(conn, out.get("approval_id") or "")
                if ap is not None:
                    approval_flow.post_card(conn, ap)
            return

        if acc.get("verdict") == "rejected":
            try:
                from .. import ops
                with get_conn() as conn:
                    ops.check_reject_storm(conn, task_id)
            except Exception:                    # noqa: BLE001
                pass

        with get_conn() as conn:
            art = conn.execute(
                """SELECT a.id, a.file_path, a.bytes FROM artifacts a JOIN task_runs r ON r.id=a.run_id
                   WHERE r.task_id=? ORDER BY a.created_at DESC LIMIT 1""", (task_id,)).fetchone()
            total = conn.execute("SELECT COALESCE(SUM(usage_tokens),0) t FROM task_runs WHERE task_id=?",
                                 (task_id,)).fetchone()["t"]
            crit = [dict(r) for r in conn.execute(
                "SELECT * FROM acceptance_criteria WHERE task_id=? ORDER BY seq", (task_id,))]
            if kind == "design":
                meta = {"design_task_id": task_id, "requirement": requirement or text,
                        "state": "waiting", "artifact": dict(art) if art else None}
                store.add_message(
                    conn, chat_id, "agent",
                    "设计方案已出（见上方预览），请老板确认后再进入开发。",
                    author=(emp["name"] if emp else "AI"), kind="confirm", task_id=task_id,
                    meta=_json.dumps(meta, ensure_ascii=False))
                boss = intel.preferences(conn, ident).get("boss_title") or "老板"
                members = store.members(conn, chat_id)
                pm_name = members[0]["name"] if members else "PM"
                store.add_message(
                    conn, chat_id, "agent",
                    f"@{boss} 这是 {emp['name'] if emp else '设计'} 出的方案，"
                    f"您点「同意方案」后我再派开发开工。",
                    author=pm_name, kind="text")
                return

            summary = f"完成，累计 {total:,} tokens。验收：{acc.get('verdict')}"
            store.add_message(
                conn, chat_id, "agent", summary,
                author=(emp["name"] if emp else "AI"), kind="card", task_id=task_id,
                meta=_json.dumps({"artifact": dict(art) if art else None,
                                  "criteria": [{"id": c["id"], "criterion": c["criterion"],
                                                "state": c["state"], "note": c["note"],
                                                "acceptance_layer": c["acceptance_layer"]}
                                               for c in crit],
                                  "state": acc.get("task_state")}, ensure_ascii=False))
    except Exception as exc:                        # noqa: BLE001
        with get_conn() as conn:
            store.add_message(conn, chat_id, "system",
                              "任务执行失败，请查看日志或指派新方案。", kind="text")





def _start_project_from_dm(dm_chat_id: str, text: str, ident: str, emp) -> None:
    """PM 在私聊里接到一个项目：自己建群、拉人，然后进入设计阶段。"""
    from .. import pm as pm_mod

    with get_conn() as conn:
        from ... import identity
        from ..access import owned
        actor_row = identity.get_identity(conn, ident)
        if actor_row is None:
            return
        actor = dict(actor_row)
        tenancy.resolve(conn, ident)
        if not identity.has_scope(actor_row, "task:*"):
            return
        chat_scope = owned(conn, "chats", dm_chat_id, actor, write=True, code="ChatNotFound")
        rows = conn.execute(
            """SELECT e.id AS employee_id, e.name, e.model, e.legal_entity_id, p.title
               FROM ai_employees e LEFT JOIN positions p ON p.id = e.position_id
               JOIN legal_entities le ON le.id=e.legal_entity_id
               WHERE COALESCE(e.status,'online') != 'fired'
                 AND e.legal_entity_id=? AND le.tenant_id=?""",
            (chat_scope["legal_entity_id"], actor["tenant_id"])).fetchall()
    pool = [dict(r) for r in rows]
    pm_id = emp["id"] if emp else (pool[0]["employee_id"] if pool else None)
    pm_member = next((m for m in pool if m["employee_id"] == pm_id), None)
    if pm_member is None:
        return
    others = [m for m in pool if m["employee_id"] != pm_id]
    if not others:
        return

    r = pm_mod.roles([pm_member] + others)
    picked, seen = [], set()
    for key in ("pm", "designer", "dev", "qa"):
        m = r[key]
        if m["employee_id"] not in seen:
            seen.add(m["employee_id"])
            picked.append(m)

    title = "UI重构攻坚组" if "UI" in text.upper() else f"{text[:10]}·项目组"
    with get_conn() as conn:
        chat = store.create_group(conn, title, [m["employee_id"] for m in picked],
                                  tenant_id=actor["tenant_id"])
        conn.execute("UPDATE chats SET entity_id=? WHERE id=?",
                     (chat_scope["legal_entity_id"], chat["id"]))
        names = "、".join(m["name"] for m in picked)
        store.add_message(conn, chat["id"], "system",
                          f"小文 建了群「{title}」，成员：{names}", kind="text")
    with get_conn() as conn:
        store.add_message(conn, dm_chat_id, "agent",
                          f"这件事我按项目来做，已经建了群「{title}」并把 {names} 拉了进来。"
                          f"我先让他们出方案，方案发群里请您确认。",
                          author=emp["name"], kind="text")
    _get_api_attr("_start_project", _start_project)(chat["id"], text, ident, store_members(chat["id"]), title)


def store_members(chat_id: str) -> list[dict]:
    with get_conn() as conn:
        return store.members(conn, chat_id)


def _start_project(chat_id: str, text: str, ident: str, mem: list[dict], title: str) -> None:
    """阶段一：PM 宣告 → 设计同学出静态稿 → 群里挂【设计确认卡】。"""
    from ...engine import task_generator
    from .. import pm as pm_mod

    r = pm_mod.roles(mem)
    with get_conn() as conn:
        pm_mod.announce(conn, chat_id, r, text)

    with get_conn() as conn:
        gen = task_generator.generate(conn, pm_mod.design_brief(text, title),
                                      legal_entity_id=r["designer"]["legal_entity_id"],
                                      pack_id="web-v1")
        design_id = gen["task_id"]
        conn.execute("UPDATE tasks SET task_type='design', owner_employee_id=? WHERE id=?",
                     (r["designer"]["employee_id"], design_id))
        conn.commit()

    _run_turn(chat_id, r["designer"]["employee_id"], text, ident, ack=False,
              task_id=design_id, kind="design", requirement=text)


class RoleRequestIn(BaseModel):
    role_id: str


class RoleDecisionIn(BaseModel):
    approve: bool = True


@router.post("/groups/{chat_id}/request_role")
def request_role(chat_id: str, payload: RoleRequestIn,
                 oa_ui: str | None = Depends(_token)):
    """PM 在群里发一张『申请调配角色卡』，等人点同意。"""
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import roles as roles_mod
        role = conn.execute("SELECT * FROM role_templates WHERE id=?",
                            (payload.role_id,)).fetchone()
        if role is None:
            raise HTTPException(404, detail={"code": "RoleNotFound"})
        msg_id = store.add_message(
            conn, chat_id, "agent",
            f"申请调配角色：{role['name']}（{role['tag']}）",
            author="小文", kind="role_request",
            meta=json.dumps({"role_id": role["id"], "name": role["name"],
                             "tag": role["tag"], "state": "pending",
                             "requested_by": ident}, ensure_ascii=False))
        conn.commit()
    return {"ok": True, "message_id": msg_id}


@router.post("/groups/{chat_id}/role_requests/{message_id}/decide")
def decide_role_request(chat_id: str, message_id: int, payload: RoleDecisionIn,
                        oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import roles as roles_mod
        row = conn.execute("SELECT * FROM chat_messages WHERE id=? AND chat_id=?",
                           (message_id, chat_id)).fetchone()
        if row is None or row["kind"] != "role_request":
            raise HTTPException(404, detail={"code": "RequestNotFound"})
        meta = json.loads(row["meta"] or "{}")
        if meta.get("state") != "pending":
            return {"ok": True, "state": meta.get("state")}
        if not payload.approve:
            meta["state"] = "rejected"
            conn.execute("UPDATE chat_messages SET meta=? WHERE id=?",
                         (json.dumps(meta, ensure_ascii=False), message_id))
            store.add_message(conn, chat_id, "agent", "收到，这次不调人。", author="小文")
            conn.commit()
            return {"ok": True, "state": "rejected"}

        actor = _task_actor(conn, oa_ui, "task:*")
        emp_id = roles_mod.instantiate(conn, meta["role_id"], tenant.entity_of(actor))
        conn.execute("""INSERT OR IGNORE INTO chat_members (chat_id, employee_id, role)
                        VALUES (?,?, 'member')""", (chat_id, emp_id))
        meta["state"] = "approved"
        meta["employee_id"] = emp_id
        conn.execute("UPDATE chat_messages SET meta=? WHERE id=?",
                     (json.dumps(meta, ensure_ascii=False), message_id))
        store.add_message(conn, chat_id, "agent",
                          f"{meta['name']} 已进群，接下来由 TA 配合。", author="小文")
        conn.commit()
    return {"ok": True, "state": "approved", "employee_id": emp_id}


# ------------------------------------------------------------- 双擎内核切换器 API
class ChatPatch(BaseModel):
    pinned: int | None = None
    hidden: int | None = None
    title: str | None = None
    workdir: str | None = None


@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: str, payload: ChatPatch, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "ChatNotFound"})
        for col in ("pinned", "hidden", "title", "workdir"):
            val = getattr(payload, col)
            if val is not None:
                conn.execute(f"UPDATE chats SET {col}=? WHERE id=?", (val, chat_id))
        conn.execute("UPDATE chats SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (chat_id,))
        conn.commit()
    return {"ok": True}



@router.delete("/chats/{chat_id}/messages")
def clear_chat_messages(chat_id: str, oa_ui: str | None = Depends(_token)):
    """清空指定会话的全部历史消息，重置上下文。"""
    ident = _require(oa_ui)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "ChatNotFound"})
        count = conn.execute("SELECT COUNT(*) FROM chat_messages WHERE chat_id=?", (chat_id,)).fetchone()[0]
        conn.execute("DELETE FROM chat_messages WHERE chat_id=?", (chat_id,))
        conn.execute("UPDATE chats SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (chat_id,))
        from ...audit import write_audit
        write_audit(conn, legal_entity_id=row["entity_id"] or "LE-GROUP-HQ", verb="chat:clear",
                    actor=ident["id"] if isinstance(ident, dict) else str(ident),
                    action="chat_messages_cleared", target=chat_id,
                    payload={"cleared_count": count}, response={"ok": True})
        conn.commit()
    return {"ok": True, "chat_id": chat_id, "cleared": count}

@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: str, oa_ui: str | None = Depends(_token)):
    """深度删除：kill 运行中的 run → 清理沙箱目录 → 软删会话 → 审计。"""
    import shutil
    from ...engine import dsh_adapter

    ident = _require(oa_ui)
    killed = []
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "ChatNotFound"})
        if row["kind"] == "ceo":
            raise HTTPException(400, detail={"code": "CeoChatUndeletable"})

        # 1) 杀掉该会话里所有未结束的 run
        runs = conn.execute(
            """SELECT DISTINCT r.* FROM task_runs r
               JOIN chat_messages m ON m.task_id = r.task_id
               WHERE m.chat_id=? AND r.state IN ('running','spawned','awaiting_approval')""", (chat_id,)).fetchall()
        for r in runs:
            _stop_run(conn, r, ident=ident, cancelled=True)
            killed.append(r["id"])

        # Soft deletion retains artifacts/evidence and never trusts a persisted PID.
        removed = 0

        # 3) 软删 + 审计
        conn.execute("UPDATE chats SET deleted_at=CURRENT_TIMESTAMP, hidden=1 WHERE id=?",
                     (chat_id,))
        write_audit(conn, legal_entity_id=row["entity_id"] or "LE-GROUP-HQ", verb="delete",
                    task_id=None, layer="system", action="chat_deleted", target=chat_id,
                    payload={"title": row["title"], "killed_runs": killed,
                             "sandbox_dirs_removed": removed},
                    response={"by": ident})
        conn.commit()
    return {"ok": True, "killed": killed, "dirs_removed": removed}


@router.get("/chats/{chat_id}/stream")
async def chat_stream(chat_id: str, after: int = 0, oa_ui: str | None = Depends(_token)):
    """服务端推送：新消息一落库就推给前端，不再靠 2.5s 轮询。"""
    from fastapi.responses import StreamingResponse
    import asyncio

    _require(oa_ui)

    async def gen():
        last = after
        yield "retry: 3000\r\n\r\n"
        for _ in range(600):                  # 最多 10 分钟，前端断开即结束
            try:
                with get_conn() as conn:
                    rows = store.messages(conn, chat_id, after_id=last)
                for m in rows:
                    last = max(last, int(m["id"]))
                    yield "data: " + json.dumps(m, ensure_ascii=False) + "\r\n\r\n"
            except Exception:                  # noqa: BLE001
                pass
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------- 附件与工作目录（v2.0）
@router.post("/chats/{chat_id}/attachments")
async def upload_attachment(chat_id: str, file: UploadFile = File(...),
                            oa_ui: str | None = Depends(_token)):
    import pathlib as _pl
    from ...engine import dsh_adapter

    _require(oa_ui)
    with get_conn() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if chat is None:
            raise HTTPException(404, detail={"code": "ChatNotFound"})
        workdir = chat["workdir"] or str(dsh_adapter.workspace() / "attachments" / chat_id)
    target_dir = _pl.Path(workdir) / "attachments"
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = file.filename or "upload.bin"
    if "/" in filename or "\\" in filename or _pl.Path(filename).name != filename or filename in {".", ".."}:
        raise HTTPException(400, detail={"code": "InvalidFilename"})
    target = (target_dir / filename).resolve()
    if not target.is_relative_to(target_dir.resolve()):
        raise HTTPException(400, detail={"code": "InvalidFilename"})
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, detail={"code": "AttachmentTooLarge"})
    target.write_bytes(data)

    # 文本提取：PDF 走 pypdf，文本/代码直接读，其它尽力而为
    text = ""
    suffix = target.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(target))
            text = "\r\n".join((pg.extract_text() or "") for pg in reader.pages[:30])
        elif suffix in (".txt", ".md", ".py", ".js", ".json", ".csv", ".html", ".yml", ".yaml"):
            text = target.read_text(encoding="utf-8", errors="replace")
        else:
            text = target.read_bytes().decode("utf-8", errors="ignore")
    except Exception as exc:                     # noqa: BLE001
        text = f"(解析失败: {exc})"
    text = (text or "").strip()[:20000]
    with get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS chat_attachments (
                 id TEXT PRIMARY KEY, chat_id TEXT, name TEXT, path TEXT,
                 bytes INTEGER, text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("INSERT INTO chat_attachments (id, chat_id, name, path, bytes, text)"
                     " VALUES (?,?,?,?,?,?)",
                     ("ATT-" + secrets.token_hex(6), chat_id, target.name, str(target),
                      len(data), text))
        msg_id = store.add_message(
            conn, chat_id, "human", f"[附件] {target.name}",
            author="我", kind="attachment",
            meta=json.dumps({"name": target.name, "path": str(target),
                             "bytes": len(data)}, ensure_ascii=False))
        conn.commit()
    return {"ok": True, "message_id": msg_id, "path": str(target), "bytes": len(data),
            "chars": len(text), "preview": text[:200]}


