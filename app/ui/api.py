"""BFF for the self-hosted console.

The browser never holds an HMAC secret: it authenticates once (identity id +
secret), gets an HttpOnly session cookie, and every later call is signed
server-side by this process. That is the "Session BFF" option the requirement
allowed, and it keeps keys out of the page.
"""
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
from pydantic import BaseModel

from ..audit import write_audit
from ..db import get_conn
from ..engine import dsh_adapter
from ..identity import ROLE_SCOPES  # noqa: F401  (kept for parity with the API)
from .. import tenant
from .. import tenancy
from .. import mcp as mcp_mod
from . import intel, store

router = APIRouter(prefix="/ui", tags=["ui"])

COOKIE = "oa_ui"
SESSION_TTL = 7 * 24 * 3600


def _secret() -> bytes:
    return (os.environ.get("UI_SESSION_SECRET")
            or os.environ.get("WECOM_TOKEN") or "dev-ui").encode()


def _sign(ident: str, exp: int) -> str:
    return hmac.new(_secret(), f"{ident}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def _make_cookie(ident: str) -> str:
    exp = int(time.time()) + SESSION_TTL
    return f"{ident}.{exp}.{_sign(ident, exp)}"


def _read_cookie(token: str | None) -> str | None:
    if not token or token.count(".") != 2:
        return None
    ident, exp, sig = token.split(".")
    if not exp.isdigit() or int(exp) < time.time():
        return None
    if not hmac.compare_digest(_sign(ident, int(exp)), sig):
        return None
    return ident


def _token(oa_ui: str | None = Cookie(default=None),
           authorization: str | None = Header(default=None),
           t: str | None = Query(default=None)) -> str | None:
    """Cookie in the browser, Bearer in the desktop shell, `?t=` for <iframe> previews.

    iframe 不能带自定义头，所以预览链接把同一张签名票据放进查询串；
    票据本身就是 HMAC 签名 + 过期时间，不额外增加可伪造面。
    """
    if authorization and isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    for cand in (oa_ui, t):
        if isinstance(cand, str) and cand:
            return cand
    return None


def _require(token: str | None) -> str:
    ident = _read_cookie(token)
    if ident is None:
        raise HTTPException(401, detail={"code": "NotLoggedIn"})
    return ident


def _tenant_of(conn, ident: str, header: str | None) -> str:
    try:
        return tenancy.resolve(conn, ident, header)
    except PermissionError:
        raise HTTPException(403, detail={"code": "TenantMismatch"}) from None


def _scoped_task(conn, task_id: str, tenant_id: str):
    row = conn.execute(
        """SELECT * FROM tasks WHERE id=?
           AND COALESCE(tenant_id,'TENANT-HQ')=?""", (task_id, tenant_id)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "TaskNotFound"})
    return row


def _scoped_run(conn, run_id: str, tenant_id: str):
    row = conn.execute(
        """SELECT r.*, COALESCE(t.tenant_id,'TENANT-HQ') AS task_tenant
           FROM task_runs r JOIN tasks t ON t.id=r.task_id
           WHERE r.id=? AND COALESCE(t.tenant_id,'TENANT-HQ')=?""",
        (run_id, tenant_id)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "RunNotFound"})
    return row


class LoginIn(BaseModel):
    identity_id: str
    secret: str


@router.post("/login")
def login(payload: LoginIn, response: Response):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM identities WHERE id=? AND state='active'",
                           (payload.identity_id,)).fetchone()
    if row is None or not hmac.compare_digest(row["secret"] or "", payload.secret):
        raise HTTPException(401, detail={"code": "BadCredentials"})
    token = _make_cookie(row["id"])
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        max_age=SESSION_TTL, path="/")
    return {"ok": True, "identity_id": row["id"], "name": row["name"], "role": row["role"],
            "tenant_id": row["tenant_id"] or tenancy.DEFAULT_TENANT, "token": token}


@router.get("/dev-login")
def dev_login(response: Response, as_id: str = "", tab: str = ""):
    """Screenshot/e2e helper. Off unless UI_DEV_LOGIN=1 in .env — never leave it on."""
    if os.environ.get("UI_DEV_LOGIN") != "1":
        raise HTTPException(404, detail={"code": "Disabled"})
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM identities WHERE id=? AND state='active'",
                           (as_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "UnknownIdentity"})
    response.set_cookie(COOKIE, _make_cookie(row["id"]), httponly=True, samesite="lax",
                        max_age=SESSION_TTL, path="/")
    response.status_code = 302
    response.headers["location"] = "/app/" + (f"?tab={tab}" if tab else "")
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        row = conn.execute(
            """SELECT i.*, e.name AS entity_name FROM identities i
               LEFT JOIN legal_entities e ON e.id = i.legal_entity_id WHERE i.id=?""",
            (ident,)).fetchone()
        tenant_name = tenancy.name_of(conn, row["tenant_id"] or tenancy.DEFAULT_TENANT)
    return {"identity_id": row["id"], "name": row["name"], "role": row["role"],
            "entity_id": row["legal_entity_id"], "entity_name": row["entity_name"],
            "tenant_id": row["tenant_id"] or tenancy.DEFAULT_TENANT,
            "tenant_name": tenant_name}


# ------------------------------------------------------------------ org tree
@router.get("/org")
def org(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        entities = [dict(r) for r in conn.execute(
            "SELECT * FROM legal_entities WHERE state='active' ORDER BY parent_entity_id, name")]
        positions = [dict(r) for r in conn.execute(
            "SELECT * FROM positions WHERE state='active'")]
        employees = [dict(r) for r in conn.execute(
            "SELECT * FROM ai_employees")]
    by_parent: dict[str, list] = {}
    for e in entities:
        by_parent.setdefault(e["parent_entity_id"] or "", []).append(e)

    def build(entity: dict) -> dict:
        kids = [build(x) for x in by_parent.get(entity["id"], [])]
        pos = [p for p in positions if p["legal_entity_id"] == entity["id"]]
        for p in pos:
            p["employees"] = [{"id": e["id"], "name": e["name"], "title": p["title"],
                               "model": e["model"], "status": e["status"],
                               "soul": e["soul"]}
                              for e in employees if e["position_id"] == p["id"]]
        return {**entity, "children": kids, "positions": pos}

    roots = [build(x) for x in by_parent.get("", [])]
    return {"roots": roots,
            "employees": [{"id": e["id"], "name": e["name"], "model": e["model"],
                           "status": e["status"], "entity_id": e["legal_entity_id"],
                           "position_id": e["position_id"]} for e in employees]}


class MemberIn(BaseModel):
    name: str
    title: str = "AI 员工"
    persona: str = ""
    model: str = "deepseek-chat"
    entity_id: str = "LE-GROUP-HQ"
    api_key: str | None = None
    base_url: str | None = None


@router.post("/members")
def add_member(payload: MemberIn, oa_ui: str | None = Depends(_token)):
    """Create position (if needed) + AI employee + signed identity, then a DM."""
    _require(oa_ui)
    slug = hashlib.sha1(payload.name.encode()).hexdigest()[:6].upper()
    pos_id = f"POS-{slug}"
    emp_id = f"AI-{slug}"
    ident_id = f"ID-{secrets.token_hex(8)}"
    with get_conn() as conn:
        store.ensure(conn)
        if conn.execute("SELECT 1 FROM positions WHERE id=?", (pos_id,)).fetchone() is None:
            conn.execute("""INSERT INTO positions (id, legal_entity_id, title, pack_id, state)
                            VALUES (?,?,?, 'content-v1', 'active')""",
                         (pos_id, payload.entity_id, payload.title))
        conn.execute("""INSERT INTO ai_employees
                        (id, legal_entity_id, name, position_id, model, soul, focus, status)
                        VALUES (?,?,?,?,?,?,?, 'online')""",
                     (emp_id, payload.entity_id, payload.name, pos_id, payload.model,
                      payload.persona, "按判据交付，不夸大、不越权"))
        conn.execute("""INSERT INTO identities
                        (id, name, role, legal_entity_id, secret, scopes, state, employee_id)
                        VALUES (?,?, 'agent', ?,?,?, 'active', ?)""",
                     (ident_id, payload.name, payload.entity_id, secrets.token_hex(16),
                      '["task:create","task:read","accept:judge"]', emp_id))
        conn.commit()
        chat = store.get_or_create_dm(conn, emp_id, payload.name, payload.entity_id)
    if payload.api_key:
        # per-employee model credentials live in memory only for now
        os.environ[f"EMPLOYEE_{emp_id}_API_KEY"] = payload.api_key
        if payload.base_url:
            os.environ[f"EMPLOYEE_{emp_id}_BASE_URL"] = payload.base_url
    return {"employee_id": emp_id, "identity_id": ident_id, "chat_id": chat["id"],
            "name": payload.name, "title": payload.title, "model": payload.model}


# ------------------------------------------------------------------ chats
def _ensure_chats(conn: sqlite3.Connection) -> None:
    store.ensure(conn)
    from . import dispatcher
    from . import roles as roles_mod
    roles_mod.ensure(conn)
    dispatcher.ensure_ceo_chat(conn)
    for e in conn.execute("SELECT * FROM ai_employees").fetchall():
        store.get_or_create_dm(conn, e["id"], e["name"], e["legal_entity_id"])


@router.get("/employees")
def employees(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT e.id, e.name, e.model, e.status, e.legal_entity_id, e.preset, p.title
               FROM ai_employees e LEFT JOIN positions p ON p.id = e.position_id
               ORDER BY e.name""").fetchall()
    return {"employees": [dict(r) for r in rows]}


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
        chat = store.create_group(conn, payload.name or "新群聊", payload.employee_ids)
        store.add_message(conn, chat["id"], "system", f"群聊已创建，成员 {len(payload.employee_ids)} 人")
    return {"chat_id": chat["id"], "title": chat["title"]}


@router.get("/chats")
def chats(oa_ui: str | None = Depends(_token),
          x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        _ensure_chats(conn)
        tid = _tenant_of(conn, ident, x_tenant_id)
        return {"chats": store.list_chats(conn, tenant_id=tid), "tenant_id": tid}


@router.get("/chats/{chat_id}/messages")
def chat_messages(chat_id: str, after: int = 0, oa_ui: str | None = Depends(_token),
                  x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        chat = conn.execute("SELECT tenant_id FROM chats WHERE id=?", (chat_id,)).fetchone()
        if chat is None or (chat["tenant_id"] or tenancy.DEFAULT_TENANT) != tid:
            raise HTTPException(404, detail={"code": "ChatNotFound"})
        return {"messages": store.messages(conn, chat_id, after_id=after)}


class MessageIn(BaseModel):
    text: str


@router.post("/chats/{chat_id}/messages")
def post_message(chat_id: str, payload: MessageIn, oa_ui: str | None = Depends(_token),
                 x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    text = payload.text.strip()
    if not text:
        raise HTTPException(400, detail={"code": "EmptyMessage"})
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if chat is None or (chat["tenant_id"] or tenancy.DEFAULT_TENANT) != tid:
            raise HTTPException(404, detail={"code": "ChatNotFound"})
        store.add_message(conn, chat_id, "human", text, author="我")
        # 人开口 = 新的一轮，重置 agent 发言预算（否则人类消息会被历史 hop 吃掉）
        conn.execute("DELETE FROM group_hops WHERE chat_id=?", (chat_id,))
        conn.commit()
        kind = chat["kind"]

        if kind == "ceo":
            from . import dispatcher
            if dispatcher.is_chitchat(text):
                store.add_message(conn, chat_id, "agent",
                                  "我在。要派活就把需求说具体点：做什么、要什么产出。",
                                  author=dispatcher.CEO_TITLE)
                return {"ok": True, "routed": False}
            result = dispatcher.route(conn, text, tenant_id=tid)
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
    from . import roles as _roles
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
    from ..octo.intent import classify
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
        from . import roles as roles_mod
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
    from . import pm as pm_mod
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
    from .. import acceptance_run
    from ..engine import task_generator

    run_id = None
    try:
        with get_conn() as conn:
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
            from ..octo.intent import classify
            from . import pm as pm_mod
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
            entity = emp["legal_entity_id"] if emp else "LE-GROUP-HQ"
            _ct = conn.execute("SELECT tenant_id FROM chats WHERE id=?", (chat_id,)).fetchone()
            tnt = (_ct["tenant_id"] if _ct and _ct["tenant_id"] else "TENANT-HQ")
            if task_id is None:
                gen = task_generator.generate(conn, text, legal_entity_id=entity,
                                              pack_id=pack_id, tenant_id=tnt)
                task_id = gen["task_id"]
            # 私聊也要记住「谁在干」，否则 runner 读不到 preset（cordis 不生效）
            if emp is not None:
                conn.execute("UPDATE tasks SET owner_employee_id=? WHERE id=?"
                             " AND (owner_employee_id IS NULL OR owner_employee_id='')",
                             (emp["id"], task_id))
            conn.commit()
            # Go through the real verb so the state machine, run_count and the
            # per-task budget row are all created exactly as the API does.
            from ..api import verbs as _verbs
            from ..models import SpawnRunIn

            req = SpawnRunIn(task_id=task_id, run_seq=1, legal_entity_id=entity)
            spawned = _verbs.do_spawn_run(
                conn, req, ident={"id": ident, "role": "ceo", "legal_entity_id": entity})
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
                conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
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
                        blocks.append("【附件：" + a["name"] + "】\n" + body)
                    prompt += ("\n\n以下是用户上传的附件内容，请据此回答：\n"
                               + "\n\n".join(blocks))
            except Exception:                  # noqa: BLE001
                pass
            # 被打回过就带上失败清单，让模型这一轮照着改（而不是盲改）
            failed = conn.execute(
                """SELECT criterion, note FROM acceptance_criteria
                   WHERE task_id=? AND state='failed' ORDER BY seq""", (task_id,)).fetchall()
            if failed:
                lines = [f"- {f['criterion']}：{f['note'] or ''}" for f in failed]
                prompt += "\n\n上次验收未通过，请逐条修正：\n" + "\n".join(lines)
            from .. import trace as _trace
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
                from ..engine import repair
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
                acc = {"verdict": state, "task_state": state, "results": []}
            except Exception as exc:             # noqa: BLE001
                with get_conn() as conn:
                    store.add_message(conn, chat_id, "system", f"自动扩写失败：{exc}", kind="text")

        if out.get("approval_id") or acc.get("verdict") == "awaiting_approval":
            with get_conn() as conn:
                from .. import rbac as _rbac
                from . import approval_flow
                ap = _rbac.get(conn, out.get("approval_id") or "")
                if ap is not None:
                    approval_flow.post_card(conn, ap)
            return

        if acc.get("verdict") == "rejected":
            try:
                from . import ops
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
    from . import pm as pm_mod

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT e.id AS employee_id, e.name, e.model, e.legal_entity_id, p.title
               FROM ai_employees e LEFT JOIN positions p ON p.id = e.position_id
               WHERE COALESCE(e.status,'online') != 'fired'""").fetchall()
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
        _dt = conn.execute("SELECT tenant_id FROM chats WHERE id=?", (dm_chat_id,)).fetchone()
        chat = store.create_group(conn, title, [m["employee_id"] for m in picked],
                                  tenant_id=(_dt["tenant_id"] if _dt else "TENANT-HQ"))
        names = "、".join(m["name"] for m in picked)
        store.add_message(conn, chat["id"], "system",
                          f"小文 建了群「{title}」，成员：{names}", kind="text")
    with get_conn() as conn:
        store.add_message(conn, dm_chat_id, "agent",
                          f"这件事我按项目来做，已经建了群「{title}」并把 {names} 拉了进来。"
                          f"我先让他们出方案，方案发群里请您确认。",
                          author=emp["name"], kind="text")
    _start_project(chat["id"], text, ident, store_members(chat["id"]), title)


def store_members(chat_id: str) -> list[dict]:
    with get_conn() as conn:
        return store.members(conn, chat_id)


def _start_project(chat_id: str, text: str, ident: str, mem: list[dict], title: str) -> None:
    """阶段一：PM 宣告 → 设计同学出静态稿 → 群里挂【设计确认卡】。"""
    from ..engine import task_generator
    from . import pm as pm_mod

    r = pm_mod.roles(mem)
    with get_conn() as conn:
        pm_mod.announce(conn, chat_id, r, text)

    with get_conn() as conn:
        _ct = conn.execute("SELECT tenant_id FROM chats WHERE id=?", (chat_id,)).fetchone()
        tnt = (_ct["tenant_id"] if _ct and _ct["tenant_id"] else "TENANT-HQ")
        gen = task_generator.generate(conn, pm_mod.design_brief(text, title),
                                      legal_entity_id=r["designer"]["legal_entity_id"],
                                      pack_id="web-v1", tenant_id=tnt)
        design_id = gen["task_id"]
        conn.execute("UPDATE tasks SET task_type='design', owner_employee_id=? WHERE id=?",
                     (r["designer"]["employee_id"], design_id))
        conn.commit()

    _run_turn(chat_id, r["designer"]["employee_id"], text, ident, ack=False,
              task_id=design_id, kind="design", requirement=text)


class DesignIn(BaseModel):
    note: str | None = None


def _design_message(conn, task_id: str):
    return conn.execute(
        "SELECT * FROM chat_messages WHERE task_id=? AND kind='confirm' ORDER BY id DESC LIMIT 1",
        (task_id,)).fetchone()


@router.post("/designs/{task_id}/approve")
def approve_design(task_id: str, payload: DesignIn | None = None,
                   oa_ui: str | None = Depends(_token),
                   x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        _scoped_task(conn, task_id, tid)
        msg = _design_message(conn, task_id)
        if msg is None:
            raise HTTPException(404, detail={"code": "NoDesignCard"})
        meta = json.loads(msg["meta"] or "{}")
        if meta.get("state") == "approved":
            return {"ok": True, "already": True}
        meta["state"] = "approved"
        conn.execute("UPDATE chat_messages SET meta=? WHERE id=?",
                     (json.dumps(meta, ensure_ascii=False), msg["id"]))
        store.add_message(conn, msg["chat_id"], "human", "同意方案，开始开发", author="我")
        members = store.members(conn, msg["chat_id"])
        pm_name = members[0]["name"] if members else "PM"
        store.add_message(conn, msg["chat_id"], "agent",
                          "收到，方案已确认。我这就派开发进入实施阶段。",
                          author=pm_name, kind="text")
        conn.commit()
        chat_id = msg["chat_id"]
        requirement = meta.get("requirement") or ""
    threading.Thread(target=_dev_phase, args=(chat_id, task_id, requirement, ident),
                     daemon=True).start()
    return {"ok": True}


@router.post("/designs/{task_id}/reject")
def reject_design(task_id: str, payload: DesignIn | None = None,
                  oa_ui: str | None = Depends(_token),
                  x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        _scoped_task(conn, task_id, tid)
        msg = _design_message(conn, task_id)
        if msg is None:
            raise HTTPException(404, detail={"code": "NoDesignCard"})
        meta = json.loads(msg["meta"] or "{}")
        meta["state"] = "rejected"
        if payload and payload.note:
            meta["note"] = payload.note
        conn.execute("UPDATE chat_messages SET meta=? WHERE id=?",
                     (json.dumps(meta, ensure_ascii=False), msg["id"]))
        store.add_message(conn, msg["chat_id"], "agent",
                          "方案已驳回，我让设计同学按您的意见重做一版。",
                          author="小文", kind="text")
        conn.commit()
    return {"ok": True}


def _dev_phase(chat_id: str, design_id: str, requirement: str, ident: str) -> None:
    """阶段二：老板点头后才建开发任务。"""
    from ..engine import task_generator
    from . import pm as pm_mod

    with get_conn() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        mem = store.members(conn, chat_id)
        r = pm_mod.roles(mem)
        _ct = conn.execute("SELECT tenant_id FROM chats WHERE id=?", (chat_id,)).fetchone()
        tnt = (_ct["tenant_id"] if _ct and _ct["tenant_id"] else "TENANT-HQ")
        gen = task_generator.generate(conn, pm_mod.dev_brief(requirement),
                                      legal_entity_id=r["dev"]["legal_entity_id"],
                                      pack_id="web-v1", tenant_id=tnt)
        dev_id = gen["task_id"]
        conn.execute("UPDATE tasks SET owner_employee_id=? WHERE id=?",
                     (r["dev"]["employee_id"], dev_id))
        conn.commit()
        store.add_message(conn, chat_id, "agent",
                          f"开发任务已建：{dev_id}。{r['dev']['name']} 开始实施。",
                          author=r["pm"]["name"], kind="text")
    _run_turn(chat_id, r["dev"]["employee_id"], requirement, ident, ack=False,
              task_id=dev_id, kind="task", requirement=requirement)


# ------------------------------------------------------------------ task view
@router.get("/tasks/{task_id}")
def task_view(task_id: str, oa_ui: str | None = Depends(_token),
              x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        t = _scoped_task(conn, task_id, tid)
        crit = [dict(r) for r in conn.execute(
            "SELECT * FROM acceptance_criteria WHERE task_id=? ORDER BY seq", (task_id,))]
        runs = [dict(r) for r in conn.execute(
            "SELECT * FROM task_runs WHERE task_id=? ORDER BY rowid DESC", (task_id,))]
        stream = []
        if runs:
            stream = store.stream(conn, runs[0]["id"], limit=60)
        owner = None
        if t["owner_employee_id"]:
            owner = conn.execute(
                """SELECT e.id, e.name, e.preset FROM ai_employees e
                   WHERE e.id = ?""", (t["owner_employee_id"],)).fetchone()
        if owner is None:
            row = conn.execute("SELECT chat_id FROM chat_messages WHERE task_id=? LIMIT 1",
                               (task_id,)).fetchone()
            if row:
                owner = conn.execute(
                    """SELECT e.id, e.name, e.preset FROM ai_employees e
                       JOIN chats ch ON ch.employee_id = e.id
                       WHERE ch.id = ? AND ch.kind = 'dm'""", (row["chat_id"],)).fetchone()
    return {"task": dict(t), "criteria": crit, "runs": runs, "stream": stream,
            "owner": dict(owner) if owner else None}


class ActionIn(BaseModel):
    action: str          # accept | reject | abort
    note: str | None = None


@router.post("/runs/{run_id}/kill")
def kill_run(run_id: str, payload: DesignIn | None = None, oa_ui: str | None = Depends(_token),
             x_tenant_id: str | None = Header(default=None)):
    """紧急熔断：直接杀掉底层 DSH 进程树，run 记 failed，写审计。"""
    ident = _require(oa_ui)
    import subprocess
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        run = _scoped_run(conn, run_id, tid)
        pid = run["pid"]
        killed = False
        if pid:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=15)
                killed = True
            except Exception:                # noqa: BLE001
                killed = False
        conn.execute("UPDATE task_runs SET state='failed', exit_code=-9 WHERE id=?", (run_id,))
        if run["task_id"]:
            from .. import state_machine
            try:
                state_machine.transition(conn, run["task_id"], "run_failed",
                                         actor=ident, reason="kill switch")
            except Exception:                # noqa: BLE001  已在终态或不允许流转
                pass
        write_audit(conn, legal_entity_id=run["legal_entity_id"], verb="kill",
                    task_id=run["task_id"], run_id=run_id, layer="system",
                    action="run_killed", target=run_id,
                    payload={"pid": pid, "killed": killed}, response={"by": ident})
        conn.commit()
    return {"ok": True, "killed": killed, "pid": pid}


@router.post("/tasks/{task_id}/action")
def task_action(task_id: str, payload: ActionIn, oa_ui: str | None = Depends(_token),
                x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        row = _scoped_task(conn, task_id, tid)
        if payload.action == "accept":
            from ..acceptance import l1_manual
            # 人工判据要求证据；老板点「验收通过」时，自动附上产物片段，
            # 免得因为没有 snippet 直接 500（真实业务闭环踩到）
            snippet = None
            art = conn.execute(
                """SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id=a.run_id
                   WHERE r.task_id=? ORDER BY a.created_at DESC LIMIT 1""", (task_id,)).fetchone()
            if art and art["file_path"]:
                try:
                    snippet = open(art["file_path"], encoding="utf-8", errors="replace").read(200)
                except OSError:
                    snippet = None
            for c in conn.execute(
                    """SELECT * FROM acceptance_criteria WHERE task_id=? AND check_type='human_check'
                       AND state='pending'""", (task_id,)).fetchall():
                l1_manual.apply(conn, c["id"], passed=True, checked_by=ident,
                                note=payload.note or "UI 验收通过",
                                evidence_snippet=snippet)
            __import__("app.acceptance_run", fromlist=["run"]).run(
                conn, task_id, [row["legal_entity_id"]])
        elif payload.action == "abort":
            conn.execute("UPDATE tasks SET state='cancelled', updated_at=CURRENT_TIMESTAMP"
                         " WHERE id=?", (task_id,))
        elif payload.action != "reject":
            raise HTTPException(400, detail={"code": "UnknownAction"})
        conn.commit()
        state = conn.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()["state"]
    return {"ok": True, "state": state}


class UiRetryIn(BaseModel):
    runner: str | None = None


def _ui_retry_run(run_id: str, prompt: str, runner: str, session_id: str | None) -> None:
    with get_conn() as conn:
        try:
            dsh_adapter.execute_run(conn, run_id, prompt=prompt, runner=runner,
                                    resume_session=session_id)
        except Exception as exc:                       # noqa: BLE001
            from ..errors import human_error
            conn.execute("UPDATE task_runs SET state='failed', error=?,"
                         " ended_at=CURRENT_TIMESTAMP WHERE id=?",
                         (human_error(exc), run_id))
            conn.commit()


@router.post("/tasks/{task_id}/retry")
def ui_retry_task(task_id: str, payload: UiRetryIn | None = None,
                  oa_ui: str | None = Depends(_token),
                  x_tenant_id: str | None = Header(default=None)):
    """一键重试：failed / rejected -> 同一会话新 run（不重跑整段 prompt）。"""
    ident = _require(oa_ui)
    from .. import state_machine
    from ..audit import new_id
    from ..api.tasks import _retry_prompt
    payload = payload or UiRetryIn()
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        t = _scoped_task(conn, task_id, tid)
        if t["state"] not in ("failed", "rejected"):
            raise HTTPException(400, detail={"code": "NotRetryable", "state": t["state"]})
        prev = conn.execute(
            """SELECT * FROM task_runs WHERE task_id=? AND session_id IS NOT NULL
               ORDER BY run_seq DESC LIMIT 1""", (task_id,)).fetchone()
        session_id = prev["session_id"] if prev else None
        run_seq = conn.execute(
            "SELECT COALESCE(MAX(run_seq),0)+1 FROM task_runs WHERE task_id=?",
            (task_id,)).fetchone()[0]
        runner = (payload.runner or os.environ.get("RUNNER") or "mock").lower()
        run_id = new_id("RUN")
        conn.execute(
            """INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id, tenant_id,
                                     state, runner, session_id, actor_identity)
               VALUES (?,?,?,?,?, 'spawned', ?, ?, ?)""",
            (run_id, task_id, run_seq, t["legal_entity_id"],
             t["tenant_id"] or "TENANT-HQ", runner, session_id, ident))
        state_machine.transition(conn, task_id, "retry", actor=ident,
                                 payload={"run_seq": run_seq, "resumed": bool(session_id)})
        conn.execute("""UPDATE acceptance_criteria SET state='pending', checked_by=NULL,
                        note=NULL WHERE task_id=? AND state!='na'""", (task_id,))
        prompt = _retry_prompt(conn, task_id)
        write_audit(conn, legal_entity_id=t["legal_entity_id"], verb="retry", task_id=task_id,
                    run_id=run_id, action="ui_retry", target=run_id,
                    payload={"run_seq": run_seq, "session_id": session_id, "by": ident},
                    response={"resumed": bool(session_id)})
        conn.commit()
    threading.Thread(target=_ui_retry_run,
                     args=(run_id, prompt, runner, session_id), daemon=True).start()
    return {"ok": True, "run_id": run_id, "resumed": bool(session_id)}


# ------------------------------------------------------------------ live feed
@router.get("/live/{run_id}")
def live(run_id: str, after: int = 0, oa_ui: str | None = Depends(_token),
         x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        try:
            run = _scoped_run(conn, run_id, tid)
        except HTTPException:
            run = None
        ev = store.stream(conn, run_id, after_id=after) if run else []
    return {"run": dict(run) if run else None, "stream": ev}


# ------------------------------------------------------------------ 角色库（M15）
class RoleIn(BaseModel):
    name: str
    tag: str
    prompt: str = ""
    model: str = "deepseek-chat"
    skills: str = ""
    entity_id: str = "LE-GROUP-HQ"
    spawn: bool = True          # 建完立刻实例化成员工，出现在组织架构里


@router.get("/roles")
def list_roles(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from . import roles as roles_mod
        roles_mod.ensure(conn)
        return {"roles": roles_mod.list_roles(conn)}


@router.post("/roles")
def create_role(payload: RoleIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    if not payload.name.strip() or not payload.tag.strip():
        raise HTTPException(400, detail={"code": "BadRole"})
    with get_conn() as conn:
        from . import roles as roles_mod
        roles_mod.ensure(conn)
        role = roles_mod.create_role(conn, name=payload.name.strip(), tag=payload.tag.strip(),
                                     prompt=payload.prompt, model=payload.model,
                                     skills=payload.skills)
        emp_id = roles_mod.instantiate(conn, role["id"], payload.entity_id) if payload.spawn else None
    return {"ok": True, "role": role, "employee_id": emp_id}


# ---------------------------------------------------- PM 申请调人（M15 模块三）
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
        from . import roles as roles_mod
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
        from . import roles as roles_mod
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

        emp_id = roles_mod.instantiate(conn, meta["role_id"])
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


# ------------------------------------------------------------------ 项目（v1.5）
@router.get("/projects")
def list_projects(oa_ui: str | None = Depends(_token), limit: int = 50,
                  x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import pipeline
        pipeline.ensure(conn)
        tid = _tenant_of(conn, ident, x_tenant_id)
        rows = conn.execute(
            """SELECT p.*, (SELECT COUNT(*) FROM project_stages s
                            WHERE s.project_id=p.id AND s.state='done') AS done_stages
               FROM projects p WHERE COALESCE(p.tenant_id,?)=?
               ORDER BY p.created_at DESC LIMIT ?""",
            (tenancy.DEFAULT_TENANT, tid, limit)).fetchall()
    return {"projects": [dict(r) for r in rows], "tenant_id": tid}


class ProjectPatch(BaseModel):
    use_llm_docs: int | None = None
    state: str | None = None


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, payload: ProjectPatch,
                  oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import pipeline
        pipeline.ensure(conn)
        if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        if payload.use_llm_docs is not None:
            conn.execute("UPDATE projects SET use_llm_docs=? WHERE id=?",
                         (1 if payload.use_llm_docs else 0, project_id))
        if payload.state:
            conn.execute("UPDATE projects SET state=? WHERE id=?", (payload.state, project_id))
        conn.commit()
        p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return {"ok": True, "project": dict(p)}


@router.get("/projects/{project_id}")
def project_detail(project_id: str, oa_ui: str | None = Depends(_token),
                   x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import pipeline
        pipeline.ensure(conn)
        tid = _tenant_of(conn, ident, x_tenant_id)
        p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is None or (p["tenant_id"] or tenancy.DEFAULT_TENANT) != tid:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        stages = [dict(r) for r in conn.execute(
            "SELECT * FROM project_stages WHERE project_id=? ORDER BY seq", (project_id,))]
        gates = [dict(r) for r in conn.execute(
            "SELECT * FROM stage_gates WHERE project_id=? ORDER BY rowid", (project_id,))]
        raw = [dict(r) for r in conn.execute(
            "SELECT * FROM stage_deliverables WHERE project_id=? ORDER BY rowid", (project_id,))]
        # 按 (stage, kind) 只留最新一条 —— 重跑阶段会留下旧记录
        seen: dict[tuple, dict] = {}
        for d in raw:
            seen[(d["stage_key"], d["kind"])] = d
        dels = list(seen.values())
        wbs = [dict(r) for r in conn.execute(
            "SELECT * FROM wbs_items WHERE project_id=? ORDER BY code", (project_id,))]
        ws = conn.execute("SELECT * FROM workshops WHERE project_id=? LIMIT 1",
                          (project_id,)).fetchone()
        turns = [dict(r) for r in conn.execute(
            "SELECT * FROM workshop_turns WHERE workshop_id=? ORDER BY round, rowid",
            (ws["id"],))] if ws else []
        tl = pipeline.timeline(conn, project_id)
    return {"project": dict(p), "stages": stages, "gates": gates, "deliverables": dels,
            "wbs": wbs, "workshop": dict(ws) if ws else None, "turns": turns,
            "timeline": tl}


# ------------------------------------------------------- Tool ACL（v1.5 战役二）
class ToolCallIn(BaseModel):
    role_tag: str
    tool: str
    sql: str | None = None


@router.post("/tools/call")
def tool_call(payload: ToolCallIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    from .. import tools_acl
    with get_conn() as conn:
        try:
            tools_acl.check(conn, role_tag=payload.role_tag, tool=payload.tool,
                            sql=payload.sql, target=payload.tool)
        except PermissionError as exc:
            raise HTTPException(403, detail={"code": "ToolDenied", "message": str(exc)})
    return {"ok": True, "role": payload.role_tag, "tool": payload.tool}


# ------------------------------------------------------- 会话管理（v2.0 深度删除）
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


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: str, oa_ui: str | None = Depends(_token)):
    """深度删除：kill 运行中的 run → 清理沙箱目录 → 软删会话 → 审计。"""
    import shutil
    from ..engine import dsh_adapter

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
            """SELECT r.id, r.pid FROM task_runs r
               JOIN chat_messages m ON m.task_id = r.task_id
               WHERE m.chat_id=? AND r.state IN ('running','spawned')""", (chat_id,)).fetchall()
        for r in runs:
            if r["pid"]:
                try:
                    import subprocess
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(r["pid"])],
                                   capture_output=True, timeout=10)
                except Exception:            # noqa: BLE001
                    pass
            conn.execute("UPDATE task_runs SET state='failed', exit_code=-9 WHERE id=?",
                         (r["id"],))
            killed.append(r["id"])

        # 2) 清理沙箱目录（该会话任务的工作区）
        removed = 0
        tasks = {t["task_id"] for t in conn.execute(
            "SELECT DISTINCT task_id FROM chat_messages WHERE chat_id=? AND task_id IS NOT NULL",
            (chat_id,)).fetchall()}
        for tid in tasks:
            for base in (dsh_adapter.workspace() / "projects", dsh_adapter.workspace()):
                for p in base.glob(f"*/{tid}"):
                    if p.is_dir() and str(p).startswith(str(dsh_adapter.workspace())):
                        shutil.rmtree(p, ignore_errors=True)
                        removed += 1

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


class RolePatch(BaseModel):
    name: str | None = None
    prompt: str | None = None
    model: str | None = None
    skills: str | None = None
    tools: str | None = None
    deliverable: str | None = None
    industry: str | None = None


@router.patch("/roles/{role_id}")
def patch_role(role_id: str, payload: RolePatch, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from . import roles as roles_mod
        roles_mod.ensure(conn)
        try:
            role = roles_mod.update_role(conn, role_id, **payload.model_dump(exclude_none=True))
        except ValueError:
            raise HTTPException(404, detail={"code": "RoleNotFound"})
    return {"ok": True, "role": role}


# ------------------------------------------------- 实时流（SSE，v2.0）
@router.get("/chats/{chat_id}/stream")
async def chat_stream(chat_id: str, after: int = 0, oa_ui: str | None = Depends(_token)):
    """服务端推送：新消息一落库就推给前端，不再靠 2.5s 轮询。"""
    from fastapi.responses import StreamingResponse
    import asyncio

    _require(oa_ui)

    async def gen():
        last = after
        yield "retry: 3000\n\n"
        for _ in range(600):                  # 最多 10 分钟，前端断开即结束
            try:
                with get_conn() as conn:
                    rows = store.messages(conn, chat_id, after_id=last)
                for m in rows:
                    last = max(last, int(m["id"]))
                    yield "data: " + json.dumps(m, ensure_ascii=False) + "\n\n"
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
    from ..engine import dsh_adapter

    _require(oa_ui)
    with get_conn() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if chat is None:
            raise HTTPException(404, detail={"code": "ChatNotFound"})
        workdir = chat["workdir"] or str(dsh_adapter.workspace() / "attachments" / chat_id)
    target_dir = _pl.Path(workdir) / "attachments"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (file.filename or "upload.bin")
    data = await file.read()
    target.write_bytes(data)

    # 文本提取：PDF 走 pypdf，文本/代码直接读，其它尽力而为
    text = ""
    suffix = target.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(target))
            text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:30])
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


@router.delete("/roles/{role_id}")
def delete_role(role_id: str, oa_ui: str | None = Depends(_token)):
    """软删角色：归档模板，员工保留（历史产出仍可追溯）。"""
    ident = _require(oa_ui)
    with get_conn() as conn:
        from . import roles as roles_mod
        roles_mod.ensure(conn)
        row = conn.execute("SELECT name FROM role_templates WHERE id=?", (role_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "RoleNotFound"})
        conn.execute("UPDATE role_templates SET state='archived' WHERE id=?", (role_id,))
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="role", task_id=None,
                    layer="system", action="role_deleted", target=role_id,
                    payload={"name": row["name"]}, response={"by": ident})
        conn.commit()
    return {"ok": True}


# ------------------------------------------------------- 链路追踪（v2.2）
@router.get("/runs/{run_id}/trace")
def run_trace(run_id: str, oa_ui: str | None = Depends(_token),
              x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        _scoped_run(conn, run_id, tid)
        from .. import trace as tr
        data = tr.load(conn, run_id)
    if data is None:
        raise HTTPException(404, detail={"code": "TraceNotFound"})
    return data


@router.get("/traces/spans/{span_id}/audit")
def span_audit(span_id: str, oa_ui: str | None = Depends(_token),
               x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        row = conn.execute(
            """SELECT COALESCE(t.tenant_id,'TENANT-HQ') AS tenant_id
               FROM trace_spans s JOIN agent_traces tr ON tr.trace_id=s.trace_id
               JOIN task_runs r ON r.id=tr.run_id JOIN tasks t ON t.id=r.task_id
               WHERE s.id=?""", (span_id,)).fetchone()
        if row is None or row["tenant_id"] != tid:
            raise HTTPException(404, detail={"code": "TraceNotFound"})
        from .. import trace as tr
        rows = tr.audit_for_span(conn, span_id)
    return {"audit": rows}


# ------------------------------------------------------------ RBAC（v2.3）
class ResourceIn(BaseModel):
    id: str
    kind: str
    name: str
    path: str | None = None
    parent_id: str | None = None


class GrantIn(BaseModel):
    agent_id: str
    resource_id: str
    permission: str = "read"


class ApprovalIn(BaseModel):
    approve: bool = True


@router.get("/rbac/resources")
def list_resources(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import rbac
        rbac.ensure(conn)
        rows = conn.execute("SELECT * FROM resources ORDER BY kind, name").fetchall()
        perms = conn.execute("SELECT * FROM agent_permissions ORDER BY rowid DESC").fetchall()
    return {"resources": [dict(r) for r in rows], "permissions": [dict(r) for r in perms]}


@router.post("/rbac/resources")
def create_resource(payload: ResourceIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import rbac
        rid = rbac.upsert_resource(conn, rid=payload.id, kind=payload.kind,
                                   name=payload.name, path=payload.path,
                                   parent_id=payload.parent_id)
    return {"ok": True, "resource_id": rid}


@router.post("/rbac/grant")
def grant_permission(payload: GrantIn, oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import rbac
        rbac.grant(conn, payload.agent_id, payload.resource_id, payload.permission,
                   granted_by=ident)
    return {"ok": True}


@router.post("/rbac/check")
def check_permission(payload: GrantIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import rbac
        try:
            rbac.require(conn, payload.agent_id, payload.resource_id, payload.permission)
        except PermissionError as exc:
            raise HTTPException(403, detail={"code": "PermissionDenied", "message": str(exc)})
    return {"ok": True}


@router.get("/rbac/approvals")
def list_approvals(oa_ui: str | None = Depends(_token),
                   x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        from .. import rbac
        rows = [x for x in rbac.pending(conn)
                if (x.get("tenant_id") or "TENANT-HQ") == tid]
        return {"pending": rows}


@router.post("/rbac/approvals/{approval_id}")
def decide_approval(approval_id: str, payload: ApprovalIn,
                    oa_ui: str | None = Depends(_token),
                    x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    from . import approval_flow, rbac
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        ap = rbac.get(conn, approval_id)
        if ap is None or (ap["tenant_id"] or "TENANT-HQ") != tid:
            raise HTTPException(404, detail={"code": "ApprovalNotFound"})
    try:
        return approval_flow.decide(approval_id, payload.approve, by=ident)
    except ValueError:
        raise HTTPException(404, detail={"code": "ApprovalNotFound"})


class HighRiskIn(BaseModel):
    agent_id: str
    tool: str
    args: dict | None = None


@router.post("/rbac/guard")
def guard_tool(payload: HighRiskIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import rbac
        return rbac.guard_tool(conn, agent_id=payload.agent_id, tool=payload.tool,
                               args=payload.args)


# ------------------------------------------------------- 审批中心（v2.3）
@router.get("/approvals")
def ui_approvals(limit: int = 50, oa_ui: str | None = Depends(_token),
                 x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        from . import approval_flow
        return approval_flow.list_all(conn, limit, tenant_id=tid)


@router.post("/approvals/{approval_id}")
def ui_decide_approval(approval_id: str, payload: ApprovalIn,
                       oa_ui: str | None = Depends(_token),
                       x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    from . import approval_flow, rbac
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        ap = rbac.get(conn, approval_id)
        if ap is None or (ap["tenant_id"] or "TENANT-HQ") != tid:
            raise HTTPException(404, detail={"code": "ApprovalNotFound"})
    try:
        return approval_flow.decide(approval_id, payload.approve, by=ident)
    except ValueError:
        raise HTTPException(404, detail={"code": "ApprovalNotFound"})


@router.post("/rbac/revoke")
def revoke_permission(payload: GrantIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import rbac
        n = rbac.revoke(conn, payload.agent_id, payload.resource_id,
                        payload.permission or None)
    return {"ok": True, "revoked": n}


# ------------------------------------------------------- 资源目录（v2.3）
class DrillIn(BaseModel):
    resource_id: str = "RES-FIN"
    agent_id: str = "AI-QUANT-01"
    mode: str = "auto"
    text: str | None = None


@router.get("/resources")
def ui_resources(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import resources as res_mod
        return res_mod.ensure_demo(conn)


@router.post("/resources/seed")
def ui_seed_resources(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import resources as res_mod
        return res_mod.ensure_demo(conn)


@router.post("/resources/drill")
def ui_drill(payload: DrillIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import resources as res_mod
        try:
            return res_mod.start_drill(conn, resource_id=payload.resource_id,
                                       agent_id=payload.agent_id, mode=payload.mode,
                                       text=payload.text)
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "DrillRejected", "message": str(exc)})


# ------------------------------------------------------- FinOps（v2.4）
@router.get("/finops")
def ui_finops(days: int = 30, oa_ui: str | None = Depends(_token),
              x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        from .. import finops
        return finops.overview(conn, days=days, tenant_id=tid)


# ------------------------------------------------------- 离线评测（v2.4）
class BaselineIn(BaseModel):
    run_id: str
    name: str | None = None


class EvalRunIn(BaseModel):
    runner: str = "mock_acp"
    prompt_mode: str = "stored"
    set_ids: list[str] | None = None
    name: str | None = None


def _eval_thread(batch_id: str, set_ids: list[str], runner: str,
                 prompt_mode: str = "stored") -> None:
    with get_conn() as conn:
        try:
            from .. import evals
            evals.execute_regression(conn, batch_id, set_ids=set_ids, runner=runner,
                                     prompt_mode=prompt_mode)
        except Exception as exc:                 # noqa: BLE001
            conn.execute("UPDATE eval_runs SET state='failed', detail_json=?,"
                         " ended_at=CURRENT_TIMESTAMP WHERE id=?",
                         (json.dumps({"error": str(exc)}, ensure_ascii=False), batch_id))
            conn.commit()


@router.get("/evals")
def ui_evals(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import evals
        return {"sets": evals.list_sets(conn), "batches": evals.list_batches(conn)}


@router.post("/evals/baselines")
def ui_eval_baseline(payload: BaselineIn, oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import evals
        try:
            return {"ok": True, "set": evals.create_from_run(conn, payload.run_id,
                                                             name=payload.name, by=ident)}
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "BaselineRejected", "message": str(exc)})


@router.post("/evals/regress")
def ui_eval_regress(payload: EvalRunIn, oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import evals
        try:
            bid = evals.start_regression(conn, set_ids=payload.set_ids,
                                         runner=payload.runner, prompt_mode=payload.prompt_mode,
                                         name=payload.name, by=ident)
            sets = payload.set_ids or [r["id"] for r in conn.execute(
                "SELECT id FROM eval_sets ORDER BY rowid DESC LIMIT 8").fetchall()]
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "EvalRejected", "message": str(exc)})
    threading.Thread(target=_eval_thread,
                     args=(bid, sets, payload.runner, payload.prompt_mode),
                     daemon=True).start()
    return {"ok": True, "batch_id": bid, "sets": len(sets), "runner": payload.runner,
            "prompt_mode": payload.prompt_mode}


@router.post("/evals/{set_id}/run")
def ui_eval_run(set_id: str, payload: EvalRunIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    payload.set_ids = [set_id]
    return ui_eval_regress(payload, oa_ui)


@router.get("/evals/batches/{batch_id}")
def ui_eval_batch(batch_id: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import evals
        return evals.batch_detail(conn, batch_id)


# ------------------------------------------------------- 组织经验（v2.4）
class ExperienceQuery(BaseModel):
    text: str
    entity: str | None = None


@router.get("/experiences")
def ui_experiences(kind: str | None = None, state: str | None = None,
                   oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import experience
        return {"experiences": experience.list_all(conn, kind=kind, state=state),
                "stats": experience.stats(conn)}


class ExperienceIn(BaseModel):
    title: str
    content: str
    tags: str | list[str] | None = None
    kind: str = "decision"
    mandatory: bool = False
    redline_terms: str | list[str] | None = None


@router.post("/experiences")
def ui_experience_create(payload: ExperienceIn, oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    title, content = payload.title.strip(), payload.content.strip()
    if not title or not content:
        raise HTTPException(400, detail={"code": "TitleAndContentRequired"})
    if payload.kind not in ("rejection", "consensus", "decision"):
        raise HTTPException(400, detail={"code": "BadKind"})
    tags = payload.tags if isinstance(payload.tags, list) else [
        x.strip() for x in (payload.tags or "").split(",") if x.strip()]
    with get_conn() as conn:
        from .. import experience
        e = experience.add(conn, kind=payload.kind, title=title, content=content,
                           tags=tags, source_type="manual",
                           source_id="manual:" + ident, mandatory=payload.mandatory,
                           redline_terms=payload.redline_terms)
        if payload.mandatory:
            e = experience.review(conn, e["id"], "approve", reviewer=ident,
                                  note="董事长手写公司红线，强制注入")
    return {"ok": True, "experience": e}


class ExperienceReviewIn(BaseModel):
    action: str
    note: str | None = None


@router.post("/experiences/{exp_id}/review")
def ui_experience_review(exp_id: str, payload: ExperienceReviewIn,
                         oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import experience
        try:
            exp = experience.review(conn, exp_id, payload.action, reviewer=ident,
                                    note=payload.note)
        except KeyError:
            raise HTTPException(404, detail={"code": "ExperienceNotFound"}) from None
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "BadAction", "message": str(exc)}) from None
    return {"ok": True, "experience": exp}


@router.post("/experiences/preview")
def ui_experience_preview(payload: ExperienceQuery, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import experience
        return experience.context_for(conn, payload.text, entity=payload.entity, record=False)


# ------------------------------------------------------------------ v3.1 项目重试 / 闸门复核
@router.post("/projects/{project_id}/gates/{gate_id}/approve")
def approve_project_gate(project_id: str, gate_id: str, oa_ui: str | None = Depends(_token),
                         x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import pipeline
        pipeline.ensure(conn)
        tid = _tenant_of(conn, ident, x_tenant_id)
        p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is None or (p["tenant_id"] or tenancy.DEFAULT_TENANT) != tid:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        gate = conn.execute("SELECT * FROM stage_gates WHERE id=? AND project_id=?",
                            (gate_id, project_id)).fetchone()
        if gate is None:
            raise HTTPException(404, detail={"code": "GateNotFound"})
        if gate["state"] not in ("vetoed", "failed"):
            raise HTTPException(400, detail={"code": "GateNotPending", "state": gate["state"]})
        conn.execute("""UPDATE stage_gates SET state='passed',
                        note=COALESCE(note,'') || '｜董事长复核通过', updated_at=CURRENT_TIMESTAMP
                        WHERE id=?""", (gate_id,))
        left = conn.execute("SELECT COUNT(*) FROM stage_gates WHERE project_id=? AND state='vetoed'",
                            (project_id,)).fetchone()[0]
        if not left:
            conn.execute("UPDATE projects SET state='active' WHERE id=?", (project_id,))
        pending = conn.execute(
            "SELECT COUNT(*) FROM stage_gates WHERE project_id=? AND stage_key=? AND state!='passed'",
            (project_id, gate["stage_key"])).fetchone()[0]
        if not pending:
            conn.execute("""UPDATE project_stages SET state='done', ended_at=CURRENT_TIMESTAMP
                            WHERE project_id=? AND key=? AND state!='done'""",
                         (project_id, gate["stage_key"]))
        write_audit(conn, legal_entity_id=p["legal_entity_id"], verb="project", task_id=None,
                    layer="system", action="project_gate_approved", target=project_id,
                    payload={"gate_id": gate_id, "gate": gate["name"], "by": ident},
                    response={"ok": True})
        conn.commit()
    return {"ok": True, "project_active": not left}


def _ui_project_retry(project_id: str, requirement: str, keys: list[str]) -> None:
    from .. import pipeline
    from ..errors import human_error
    with get_conn() as conn:
        try:
            pipeline.run_project(conn, project_id, requirement, keys=keys)
            store.add_message(conn, "C-CEO", "agent", "项目已重试，剩余阶段已继续推进。",
                              author="CEO 调度台")
        except Exception as exc:                       # noqa: BLE001
            store.add_message(conn, "C-CEO", "agent",
                              f"项目 {project_id} 重试暂停：{human_error(exc)}",
                              author="CEO 调度台")
        conn.commit()


@router.post("/projects/{project_id}/retry")
def retry_project(project_id: str, oa_ui: str | None = Depends(_token),
                  x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import pipeline
        pipeline.ensure(conn)
        tid = _tenant_of(conn, ident, x_tenant_id)
        p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is None or (p["tenant_id"] or tenancy.DEFAULT_TENANT) != tid:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        veto = conn.execute("SELECT COUNT(*) FROM stage_gates WHERE project_id=? AND state='vetoed'",
                            (project_id,)).fetchone()[0]
        if veto:
            raise HTTPException(400, detail={"code": "GateNotPassed"})
        rows = conn.execute("SELECT key,state FROM project_stages WHERE project_id=? ORDER BY seq",
                            (project_id,)).fetchall()
        keys = [r["key"] for r in rows if r["state"] != "done"]
        if not keys:
            raise HTTPException(400, detail={"code": "ProjectAlreadyDone"})
        conn.execute("UPDATE projects SET state='active' WHERE id=?", (project_id,))
        write_audit(conn, legal_entity_id=p["legal_entity_id"], verb="project", task_id=None,
                    layer="system", action="project_retry", target=project_id,
                    payload={"from": keys[0], "keys": keys, "by": ident}, response={"ok": True})
        conn.commit()
        requirement = p["requirement"]
    threading.Thread(target=_ui_project_retry, args=(project_id, requirement, keys),
                     daemon=True).start()
    return {"ok": True, "from_stage": keys[0], "stages": keys}


# ------------------------------------------------------------------ v3.1 SMTP
class SmtpIn(BaseModel):
    host: str = ""
    port: int = 587
    user: str = ""
    password: str | None = None
    from_addr: str = ""
    secure: bool = False


class SmtpTestIn(BaseModel):
    to: str


@router.get("/smtp")
def ui_smtp_settings(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import mailer
        return mailer.smtp_settings(conn)


@router.put("/smtp")
def ui_smtp_save(payload: SmtpIn, oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        from .. import mailer
        out = mailer.save_smtp(conn, host=payload.host, port=payload.port,
                               user=payload.user, password=payload.password,
                               from_addr=payload.from_addr, secure=payload.secure)
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="settings", task_id=None,
                    layer="system", action="smtp_saved", target="smtp",
                    payload={"host": out["host"], "port": out["port"], "user": out["user"],
                             "from": out["from_addr"], "secure": out["secure"],
                             "pass_set": out["pass_set"], "by": ident},
                    response={"ok": True})
        conn.commit()
    return out


@router.post("/smtp/test")
def ui_smtp_test(payload: SmtpTestIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import mailer
        cfg = mailer.smtp_settings(conn)
        rows = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key,value FROM settings WHERE key='smtp_pass'")}
    if not cfg["host"]:
        return {"ok": False, "error": "还没有配置 SMTP 服务器"}
    try:
        mid = mailer.send_mail(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                               password=rows.get("smtp_pass", ""),
                               from_addr=cfg["from_addr"], to=payload.to,
                               subject="AgentOS SMTP 测试",
                               body="这是一封来自 AgentOS V3.1 的 SMTP 连通性测试邮件。",
                               secure=cfg["secure"])
        return {"ok": True, "message_id": mid, "to": payload.to}
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}


# ------------------------------------------------------------------ v3.0 MCP 工具扩展
class McpServerIn(BaseModel):
    name: str
    url: str = ""
    api_key: str = ""
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    transport: str = "http"


class McpPatchIn(BaseModel):
    name: str | None = None
    url: str | None = None
    api_key: str | None = None
    auth_header: str | None = None
    auth_scheme: str | None = None
    enabled: int | None = None


class McpBindIn(BaseModel):
    server_ids: list[str]


@router.get("/mcp")
def ui_mcp_servers(oa_ui: str | None = Depends(_token),
                   x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        return {"servers": mcp_mod.list_servers(conn, tid), "tenant_id": tid}


@router.post("/mcp")
def ui_mcp_add(payload: McpServerIn, oa_ui: str | None = Depends(_token),
               x_tenant_id: str | None = Header(default=None)):
    ident = _require(oa_ui)
    if not payload.name.strip() or not payload.url.strip():
        raise HTTPException(400, detail={"code": "NameAndUrlRequired"})
    with get_conn() as conn:
        tid = _tenant_of(conn, ident, x_tenant_id)
        row = mcp_mod.add_server(conn, name=payload.name, url=payload.url,
                                 api_key=payload.api_key,
                                 auth_header=payload.auth_header,
                                 auth_scheme=payload.auth_scheme,
                                 transport=payload.transport, tenant_id=tid, actor=ident)
    row["api_key"] = ""
    row["has_key"] = bool(payload.api_key)
    return {"ok": True, "server": row}


@router.patch("/mcp/{sid}")
def ui_mcp_patch(sid: str, payload: McpPatchIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        try:
            row = mcp_mod.update_server(conn, sid, **payload.model_dump(exclude_none=True))
        except KeyError:
            raise HTTPException(404, detail={"code": "McpServerNotFound"}) from None
    row["api_key"] = ""
    return {"ok": True, "server": row}


@router.delete("/mcp/{sid}")
def ui_mcp_delete(sid: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        mcp_mod.delete_server(conn, sid)
    return {"ok": True}


@router.post("/mcp/{sid}/test")
def ui_mcp_test(sid: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM mcp_servers WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "McpServerNotFound"})
        try:
            result = mcp_mod.probe(dict(row))
            mcp_mod.record_probe(conn, sid, result, None)
            return {"ok": True, "tools": result["tools"], "tool_names": result["tool_names"],
                    "server": result["server"]}
        except Exception as exc:                    # noqa: BLE001
            mcp_mod.record_probe(conn, sid, None, str(exc))
            return {"ok": False, "error": str(exc)[:500]}


@router.get("/mcp/bindings/{agent_id}")
def ui_mcp_bindings(agent_id: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        mcp_mod.ensure(conn)
        return {"agent_id": agent_id, "server_ids": mcp_mod.bindings(conn, agent_id)}


@router.put("/mcp/bindings/{agent_id}")
def ui_mcp_bind(agent_id: str, payload: McpBindIn, oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM ai_employees WHERE id=?", (agent_id,)).fetchone() is None:
            raise HTTPException(404, detail={"code": "EmployeeNotFound"})
        ids = mcp_mod.set_bindings(conn, agent_id, payload.server_ids)
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="settings", task_id=None,
                    layer="system", action="mcp_bound", target=agent_id,
                    payload={"servers": ids, "actor": ident}, response={"ok": True})
        conn.commit()
    return {"ok": True, "agent_id": agent_id, "server_ids": ids}


@router.post("/mcp/seed")
def ui_mcp_seed(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        mcp_mod.seed_demo(conn)
        return {"ok": True, "servers": mcp_mod.list_servers(conn)}
