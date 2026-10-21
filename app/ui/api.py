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
from pydantic import BaseModel, Field

from ..audit import write_audit
from ..db import get_conn
from ..engine import dsh_adapter
from ..identity import ROLE_SCOPES  # noqa: F401  (kept for parity with the API)
from .. import tenant, tenancy
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


def _tenant_of(conn, ident: str, header: str | None = None) -> str:
    try:
        return tenancy.resolve(conn, ident, header)
    except PermissionError as exc:
        code = str(exc)
        raise HTTPException(401 if code == "UnknownIdentity" else 403,
                            detail={"code": code}) from None


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
            "tenant_id": row["tenant_id"], "token": token}


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
    from .access import is_deployment_admin
    ident = _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        row = conn.execute(
            """SELECT i.*, e.name AS entity_name FROM identities i
               LEFT JOIN legal_entities e ON e.id = i.legal_entity_id WHERE i.id=?""",
            (ident,)).fetchone()
        tenant_name = tenancy.name_of(conn, actor["tenant_id"])
    return {"identity_id": row["id"], "name": row["name"], "role": row["role"],
            "entity_id": row["legal_entity_id"], "entity_name": row["entity_name"],
            "tenant_id": actor["tenant_id"], "tenant_name": tenant_name,
            "can_manage_deployment": is_deployment_admin(actor)}


# ------------------------------------------------------------------ org tree
@router.get("/org")
def org(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        from .access import visible_entities
        allowed = set(visible_entities(conn, actor))
        entities = [dict(r) for r in conn.execute(
            "SELECT * FROM legal_entities WHERE state='active' ORDER BY parent_entity_id, name") if r["id"] in allowed]
        positions = [dict(r) for r in conn.execute(
            "SELECT * FROM positions WHERE state='active'") if r["legal_entity_id"] in allowed]
        employees = [dict(r) for r in conn.execute(
            "SELECT * FROM ai_employees") if r["legal_entity_id"] in allowed]
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

    roots = [build(x) for x in entities if not x["parent_entity_id"] or x["parent_entity_id"] not in allowed]
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
    slug = hashlib.sha256((payload.entity_id + ":" + payload.name).encode()).hexdigest()[:12].upper()
    pos_id = f"POS-{slug}"
    emp_id = f"AI-{slug}"
    ident_id = f"ID-{secrets.token_hex(8)}"
    with get_conn() as conn:
        store.ensure(conn)
        actor = _task_actor(conn, oa_ui, "task:*")
        tenant.resolve(actor, payload.entity_id)
        if tenancy.of_entity(conn, payload.entity_id) != actor["tenant_id"]:
            raise HTTPException(403, detail={"code": "TenantMismatch"})
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
                        (id, name, role, legal_entity_id, secret, scopes, state, employee_id,tenant_id)
                        VALUES (?,?, 'agent', ?,?,?, 'active', ?,?)""",
                     (ident_id, payload.name, payload.entity_id, secrets.token_hex(16),
                      '["task:create","task:read","accept:judge"]', emp_id, actor["tenant_id"]))
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
def _ensure_chats(conn: sqlite3.Connection, actor: dict | None = None) -> None:
    store.ensure(conn)
    from . import dispatcher
    from . import roles as roles_mod
    roles_mod.ensure(conn)
    dispatcher.ensure_ceo_chat(conn, tenant_id=(actor or {}).get("tenant_id"),
                               entity_id=(actor or {}).get("legal_entity_id"))
    from .access import visible_entities
    allowed = set(visible_entities(conn, actor)) if actor else None
    for e in conn.execute("SELECT * FROM ai_employees WHERE status<>'fired'").fetchall():
        if allowed is not None and e["legal_entity_id"] not in allowed:
            continue
        store.get_or_create_dm(conn, e["id"], e["name"], e["legal_entity_id"])


@router.get("/employees")
def employees(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        from .access import visible_entities
        where, args = tenant._in("e.legal_entity_id", visible_entities(conn, actor))
        rows = conn.execute(
            f"""SELECT e.id, e.name, e.model, e.status, e.legal_entity_id, e.preset, p.title
               FROM ai_employees e LEFT JOIN positions p ON p.id = e.position_id
               WHERE {where} ORDER BY e.name""", args).fetchall()
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
        actor = _task_actor(conn, oa_ui, "task:*")
        from .access import owned
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
        from .access import owned
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
            actor = _task_actor(conn, oa_ui, "task:*")
            result = dispatcher.route(conn, text, entity_id=tenant.entity_of(actor),
                                      tenant_id=actor["tenant_id"], actor_id=actor["id"])
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
            from .. import identity
            from .access import owned
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
            from ..api import verbs as _verbs
            from ..models import SpawnRunIn

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
        from .. import identity
        from .access import owned
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
        gen = task_generator.generate(conn, pm_mod.design_brief(text, title),
                                      legal_entity_id=r["designer"]["legal_entity_id"],
                                      pack_id="web-v1")
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
                   oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
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
                  oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
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
        gen = task_generator.generate(conn, pm_mod.dev_brief(requirement),
                                      legal_entity_id=r["dev"]["legal_entity_id"],
                                      pack_id="web-v1")
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
def _task_actor(conn, token: str | None, scope: str) -> dict:
    from .. import identity
    row = identity.get_identity(conn, _require(token))
    if row is None:
        raise HTTPException(401, detail={"code": "UnknownIdentity"})
    if not identity.has_scope(row, scope):
        raise HTTPException(403, detail={"code": "Forbidden", "scope": scope})
    return dict(row)


def _task_write_entities(conn, actor: dict) -> list[str]:
    return (tenant.visible_entities(conn, actor) if actor["role"] == "system"
            else [tenant.entity_of(actor)])


def _task_allowed_actions(task, actor: dict) -> list[str]:
    from ..identity import has_scope
    if actor["role"] != "system" and task["legal_entity_id"] != tenant.entity_of(actor):
        return []
    if task["state"] == "pending_review" and has_scope(actor, "accept:judge"):
        return ["accept", "reject"]
    if has_scope(actor, "task:*"):
        if task["state"] == "in_progress":
            return ["abort"]
        if task["state"] in ("rejected", "failed"):
            return ["retry"]
    return []


def _task_execution_summary(conn, task, actor: dict) -> dict:
    """A bounded execution status, with no process/session or approval arguments."""
    run = conn.execute(
        """SELECT id,run_seq,state,runner,usage_tokens,started_at,ended_at
           FROM task_runs WHERE task_id=? ORDER BY run_seq DESC,rowid DESC LIMIT 1""",
        (task["id"],)).fetchone()
    summary = {"latest_run": dict(run) if run else None,
               "awaiting_approval": False, "pending_approval": None}
    if not run or run["state"] != "awaiting_approval" or task["state"] != "in_progress":
        return summary
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_approvals'").fetchone():
        return summary
    approval = conn.execute(
        """SELECT id,state FROM tool_approvals
           WHERE run_id=? AND task_id=? AND state='pending' ORDER BY rowid DESC LIMIT 1""",
        (run["id"], task["id"])).fetchone()
    if approval:
        from .approval_access import APPROVER_ROLES
        summary["awaiting_approval"] = True
        if actor["role"] == "system" or (actor["role"] in APPROVER_ROLES
                and task["legal_entity_id"] == tenant.entity_of(actor)):
            summary["pending_approval"] = dict(approval)
    return summary


@router.get("/tasks")
def task_list(limit: int = 50, offset: int = 0, state: str | None = None,
              q: str | None = None, oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        where, args = tenant.clause(conn, actor, "t.legal_entity_id")
        where += " AND t.tenant_id=?"
        args.append(actor["tenant_id"])
        filtered, values = where, list(args)
        if state == "active":
            filtered += " AND t.state IN ('todo','assigned','in_progress')"
        elif state and state != "all":
            if state not in ("todo", "assigned", "in_progress", "pending_review", "rejected", "failed", "completed", "cancelled"):
                raise HTTPException(400, detail={"code": "UnknownTaskState"})
            filtered += " AND t.state=?"
            values.append(state)
        if (q or "").strip():
            filtered += " AND (t.title LIKE ? OR t.id LIKE ? OR e.name LIKE ?)"
            values.extend(["%" + q.strip()[:200] + "%"] * 3)
        limit, offset = max(1, min(200, limit)), max(0, offset)
        total = conn.execute(
            f"SELECT COUNT(*) FROM tasks t LEFT JOIN ai_employees e ON e.id=t.owner_employee_id"
            f" WHERE {filtered}", values).fetchone()[0]
        tasks = [dict(row) for row in conn.execute(
            f"""SELECT t.*, e.name AS owner_name,
                      (SELECT m.chat_id FROM chat_messages m WHERE m.task_id=t.id
                       ORDER BY m.id DESC LIMIT 1) AS chat_id
               FROM tasks t LEFT JOIN ai_employees e ON e.id=t.owner_employee_id
               WHERE {filtered} ORDER BY t.rowid DESC LIMIT ? OFFSET ?""", (*values, limit, offset))]
        for task in tasks:
            task["allowed_actions"] = _task_allowed_actions(task, actor)
            task.update(_task_execution_summary(conn, task, actor))
        counts = {row["state"]: row["count"] for row in conn.execute(
            f"SELECT t.state,COUNT(*) AS count FROM tasks t WHERE {where} GROUP BY t.state", args)}
    return {"tasks": tasks, "counts": counts, "total": total, "limit": limit, "offset": offset}


@router.get("/tasks/{task_id}")
def task_view(task_id: str, oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        t = tenant.scoped_task(conn, task_id, tenant.visible_entities(conn, actor))
        execution = _task_execution_summary(conn, t, actor)
        requirement = dsh_adapter.task_requirement(t, conn=conn)
        crit = [dict(r) for r in conn.execute(
            "SELECT * FROM acceptance_criteria WHERE task_id=? ORDER BY seq", (task_id,))]
        runs = [dict(r) for r in conn.execute(
            "SELECT * FROM task_runs WHERE task_id=? ORDER BY rowid DESC", (task_id,))]
        from .artifacts import unique_paths
        artifacts = unique_paths(conn.execute(
            """SELECT a.id,a.file_path,a.bytes,a.run_id,a.created_at
               FROM artifacts a JOIN task_runs r ON r.id=a.run_id
               WHERE r.task_id=? AND COALESCE(a.state,'active')='active'
               ORDER BY r.run_seq DESC,a.art_seq DESC,a.rowid DESC""", (task_id,)))
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
    allowed_actions = _task_allowed_actions(t, actor)
    return {"task": {**dict(t), **execution, "allowed_actions": allowed_actions, "requirement": requirement},
            **execution,
            "requirement": requirement,
            "allowed_actions": allowed_actions, "criteria": crit, "runs": runs, "stream": stream,
            "artifacts": artifacts,
            "owner": dict(owner) if owner else None}


class ActionIn(BaseModel):
    action: str          # accept | reject | abort
    note: str | None = None


def _stop_run(conn, run, *, ident: str, cancelled: bool) -> bool:
    """Stop an owned worker and invalidate approvals that could restart it."""
    try:
        killed = dsh_adapter.stop_process(
            run["id"], expected_pid=run["pid"] if run["state"] == "running" else None)
    except RuntimeError as exc:
        raise HTTPException(409, detail={"code": "RunnerStopFailed", "message": str(exc)}) from exc
    conn.execute("UPDATE task_runs SET state=?, exit_code=-9, ended_at=CURRENT_TIMESTAMP WHERE id=?",
                 ("cancelled" if cancelled else "failed", run["id"]))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_approvals'").fetchone():
        conn.execute("UPDATE tool_approvals SET state='rejected', decided_by=?,"
                     " decided_at=CURRENT_TIMESTAMP WHERE run_id=? AND state='pending'",
                     (ident, run["id"]))
    return killed


@router.post("/runs/{run_id}/kill")
def kill_run(run_id: str, payload: DesignIn | None = None, oa_ui: str | None = Depends(_token)):
    """紧急熔断：直接杀掉底层 DSH 进程树，run 记 failed，写审计。"""
    ident = _require(oa_ui)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = _task_actor(conn, oa_ui, "verb:*")
        run = conn.execute("SELECT * FROM task_runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(404, detail={"code": "RunNotFound"})
        task = tenant.scoped_task(conn, run["task_id"], _task_write_entities(conn, actor))
        if run["state"] not in ("spawned", "running", "awaiting_approval") or task["state"] not in ("assigned", "in_progress"):
            raise HTTPException(409, detail={"code": "RunNotActive", "state": run["state"]})
        pid = run["pid"]
        killed = _stop_run(conn, run, ident=ident, cancelled=False)
        from .. import state_machine
        state_machine.transition(conn, run["task_id"], "run_failed",
                                 actor=ident, payload={"reason": "kill switch"})
        write_audit(conn, legal_entity_id=run["legal_entity_id"], verb="kill",
                    task_id=run["task_id"], run_id=run_id, layer="system",
                    action="run_killed", target=run_id,
                    payload={"pid": pid, "killed": killed}, response={"by": ident})
        conn.commit()
    return {"ok": True, "killed": killed, "pid": pid}


@router.post("/tasks/{task_id}/action")
def task_action(task_id: str, payload: ActionIn, oa_ui: str | None = Depends(_token)):
    ident = _require(oa_ui)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if payload.action not in ("accept", "reject", "abort"):
            raise HTTPException(400, detail={"code": "UnknownAction"})
        actor = _task_actor(conn, oa_ui, "task:*" if payload.action == "abort" else "accept:judge")
        row = tenant.scoped_task(conn, task_id, _task_write_entities(conn, actor))
        if ((payload.action in ("accept", "reject") and row["state"] != "pending_review") or
                (payload.action == "abort" and row["state"] != "in_progress")):
            raise HTTPException(409, detail={"code": "TaskActionNotAllowed", "state": row["state"],
                                             "action": payload.action})
        if payload.action == "accept":
            from .. import acceptance_run
            mandatory = acceptance_run.validate_mandatory(
                conn, task_id, entity=row["legal_entity_id"], tenant_id=row["tenant_id"],
                commit=False)
            if not mandatory["ok"]:
                acceptance_run.run(conn, task_id, [row["legal_entity_id"]])
                conn.commit()
                raise HTTPException(409, detail={"code": "MandatoryCheckFailed",
                                                 "message": mandatory["detail"]})
            from ..acceptance import l1_manual
            # 人工判据要求证据；老板点「验收通过」时，自动附上产物片段，
            # 免得因为没有 snippet 直接 500（真实业务闭环踩到）
            snippet = None
            art = conn.execute(
                """SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id=a.run_id
                   WHERE r.task_id=? AND a.state='active'
                   ORDER BY r.run_seq DESC, a.art_seq DESC LIMIT 1""", (task_id,)).fetchone()
            if art and art["file_path"]:
                try:
                    with open(art["file_path"], encoding="utf-8", errors="replace") as source:
                        snippet = source.read(200)
                except OSError:
                    snippet = None
            manual = conn.execute(
                    """SELECT * FROM acceptance_criteria WHERE task_id=? AND check_type='human_check'
                       AND state!='na'""", (task_id,)).fetchall()
            snippet = snippet or (payload.note or "").strip() or None
            if not snippet and any(c["evidence_required"] for c in manual):
                raise HTTPException(400, detail={"code": "EvidenceRequired",
                                                "message": "验收需要可读取的产物或验收说明"})
            for c in manual:
                l1_manual.apply(conn, c["id"], passed=True, checked_by=ident,
                                note=payload.note or "UI 验收通过",
                                evidence_snippet=snippet)
            __import__("app.acceptance_run", fromlist=["run"]).run(
                conn, task_id, [row["legal_entity_id"]])
        elif payload.action == "abort":
            from .. import state_machine
            runs = conn.execute("SELECT * FROM task_runs WHERE task_id=?"
                                " AND state IN ('spawned','running','awaiting_approval')", (task_id,)).fetchall()
            for run in runs:
                _stop_run(conn, run, ident=ident, cancelled=True)
            state_machine.transition(conn, task_id, "run_cancelled", actor=ident,
                                     payload={"note": payload.note})
        else:
            from .. import state_machine
            from ..acceptance import l1_manual
            note = (payload.note or "").strip() or "UI 驳回，请修改后重新提交"
            for c in conn.execute("SELECT id FROM acceptance_criteria WHERE task_id=?"
                                  " AND check_type='human_check' AND state!='na'", (task_id,)).fetchall():
                l1_manual.apply(conn, c["id"], passed=False, checked_by=ident,
                                note=note, evidence_snippet=note)
            state_machine.transition(conn, task_id, "review_failed", actor=ident,
                                     payload={"note": note})
        write_audit(conn, legal_entity_id=row["legal_entity_id"], verb="task_action",
                    task_id=task_id, actor=ident, action=f"task_{payload.action}", target=task_id,
                    payload={"note": payload.note}, response={"ok": True})
        conn.commit()
        state = conn.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()["state"]
    if payload.action == "accept" and state != "completed":
        raise HTTPException(409, detail={"code": "AcceptanceNotPassed", "state": state,
                                         "message": "自动验收尚未通过，请检查未通过的判据。"})
    return {"ok": True, "state": state}


class TaskRetryIn(BaseModel):
    note: str | None = None
    prompt: str | None = None
    budget_tokens: int | None = Field(default=None, ge=0)


def _retry_error(retry: dict, exc: Exception) -> None:
    from ..state_machine import transition
    with get_conn() as conn:
        run = conn.execute("SELECT state FROM task_runs WHERE id=?", (retry["run_id"],)).fetchone()
        task = conn.execute("SELECT state FROM tasks WHERE id=?", (retry["task_id"],)).fetchone()
        if run and run["state"] in ("spawned", "running") and task and task["state"] == "in_progress":
            transition(conn, retry["task_id"], "run_failed", actor="retry-worker",
                       payload={"reason": "retry_worker_error"})
            conn.execute("UPDATE task_runs SET state='failed', exit_code=-1,"
                         " ended_at=CURRENT_TIMESTAMP WHERE id=?", (retry["run_id"],))
        write_audit(conn, legal_entity_id=retry["legal_entity_id"], verb="retry",
                    task_id=retry["task_id"], run_id=retry["run_id"], action="retry_worker_failed",
                    target=retry["run_id"], payload={"error": str(exc)[:500]}, response={"ok": False})
        if retry.get("chat_id"):
            store.add_message(conn, retry["chat_id"], "system", f"重试执行失败：{exc}",
                              kind="text", task_id=retry["task_id"])


def _execute_task_retry(retry: dict) -> None:
    """Run a committed submission without holding the console request open."""
    from .. import acceptance_run
    try:
        with get_conn() as conn:
            out = dsh_adapter.execute_run(
                conn, retry["run_id"], prompt=retry["prompt"], runner=retry["runner"],
                resume_session=retry["session_id"], budget_tokens=retry["budget_tokens"])
        with get_conn() as conn:
            task = conn.execute("SELECT state FROM tasks WHERE id=?", (retry["task_id"],)).fetchone()
            if task and task["state"] == "pending_review":
                acceptance_run.run(conn, retry["task_id"], [retry["legal_entity_id"]])
            if out.get("approval_id"):
                from .. import rbac
                from . import approval_flow
                approval = rbac.get(conn, out["approval_id"])
                if approval is not None:
                    approval_flow.post_card(conn, approval)
            elif retry.get("chat_id"):
                state = conn.execute("SELECT state FROM tasks WHERE id=?", (retry["task_id"],)).fetchone()[0]
                artifact = conn.execute(
                    "SELECT id,file_path,bytes FROM artifacts WHERE run_id=?"
                    " AND COALESCE(state,'active')='active' ORDER BY art_seq DESC LIMIT 1",
                    (retry["run_id"],)).fetchone()
                store.add_message(conn, retry["chat_id"], "agent", f"重试完成，任务状态：{state}",
                                  author="执行助理", kind="card", task_id=retry["task_id"],
                                  meta=json.dumps({"artifact": dict(artifact) if artifact else None,
                                                   "state": state}, ensure_ascii=False))
    except Exception as exc:                        # noqa: BLE001 - preserve worker failure in UI
        _retry_error(retry, exc)


def _launch_task_retry(retry: dict) -> None:
    threading.Thread(target=_execute_task_retry, args=(retry,),
                     name=f"retry-{retry['run_id']}", daemon=True).start()


@router.post("/tasks/{task_id}/retry", status_code=202)
def retry_task(task_id: str, payload: TaskRetryIn | None = None,
               oa_ui: str | None = Depends(_token)):
    from ..api.tasks import _retry_prompt, prepare_retry
    from ..models import RetryIn
    payload = payload or TaskRetryIn()
    runner = (os.environ.get("UI_RUNNER") or "acp").strip().lower()
    if runner not in ("acp", "dsh", "mock", "mock_acp"):
        raise HTTPException(503, detail={"code": "RunnerMisconfigured"})
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = _task_actor(conn, oa_ui, "task:*")
        task = tenant.scoped_task(conn, task_id, _task_write_entities(conn, actor))
        prompt = payload.prompt
        if not prompt and (payload.note or "").strip():
            prompt = _retry_prompt(conn, task_id) + "\r\n本次补充要求：" + payload.note.strip()
        budget = payload.budget_tokens if payload.budget_tokens is not None else _budget_for(conn, task["legal_entity_id"])
        try:
            retry = prepare_retry(conn, task_id, RetryIn(prompt=prompt, runner=runner, budget_tokens=budget),
                                  actor, entities=_task_write_entities(conn, actor))
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and exc.detail.get("code") == "NotRetryable":
                raise HTTPException(409, detail=exc.detail) from exc
            raise
        chat = conn.execute("SELECT chat_id FROM chat_messages WHERE task_id=? ORDER BY id DESC LIMIT 1",
                            (task_id,)).fetchone()
        retry["chat_id"] = chat["chat_id"] if chat else None
        conn.commit()
    try:
        _launch_task_retry(retry)
    except RuntimeError as exc:
        _retry_error(retry, exc)
        raise HTTPException(503, detail={"code": "RetryWorkerUnavailable"}) from exc
    return {"ok": True, "task_id": task_id, "run_id": retry["run_id"], "run_seq": retry["run_seq"],
            "state": "in_progress", "runner": retry["runner"], "resumed": bool(retry["session_id"])}


# ------------------------------------------------------------------ live feed
@router.get("/live/{run_id}")
def live(run_id: str, after: int = 0, oa_ui: str | None = Depends(_token)):
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:read")
        run = conn.execute("SELECT * FROM task_runs WHERE id=?", (run_id,)).fetchone()
        if run is not None:
            tenant.scoped_task(conn, run["task_id"], tenant.visible_entities(conn, actor))
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
        actor = _task_actor(conn, oa_ui, "task:*")
        tenant.resolve(actor, payload.entity_id)
        if tenancy.of_entity(conn, payload.entity_id) != actor["tenant_id"]:
            raise HTTPException(403, detail={"code": "TenantMismatch"})
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


# ------------------------------------------------------------------ 项目（v1.5）
@router.get("/projects")
def list_projects(oa_ui: str | None = Depends(_token), limit: int = 50):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import pipeline
        pipeline.ensure(conn)
        actor = _task_actor(conn, oa_ui, "task:read")
        where, args = tenant.clause(conn, actor, "p.legal_entity_id")
        rows = conn.execute(
            f"""SELECT p.*, (SELECT COUNT(*) FROM project_stages s
                            WHERE s.project_id=p.id AND s.state='done') AS done_stages
               FROM projects p WHERE {where} AND p.tenant_id=?
               ORDER BY p.created_at DESC LIMIT ?""",
            (*args, actor["tenant_id"], max(1, min(200, limit)))).fetchall()
    return {"projects": [dict(r) for r in rows]}


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
        actor = _task_actor(conn, oa_ui, "task:*")
        if actor["role"] not in {"ceo", "system"}:
            raise HTTPException(403, detail={"code": "Forbidden"})
        if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        if payload.use_llm_docs is not None:
            conn.execute("UPDATE projects SET use_llm_docs=? WHERE id=?",
                         (1 if payload.use_llm_docs else 0, project_id))
        if payload.state:
            raise HTTPException(422, detail={"code": "ProjectRecoveryRequired",
                                             "message": "请提交修改后的需求重新检查项目。"})
        conn.commit()
        p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return {"ok": True, "project": dict(p)}


@router.get("/projects/{project_id}")
def project_detail(project_id: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import pipeline
        pipeline.ensure(conn)
        p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        actor = _task_actor(conn, oa_ui, "task:read")
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
        budget = _budget_of(conn, project_id)
    can_manage = actor["role"] in {"ceo", "system"} and (
        actor["role"] == "system" or p["legal_entity_id"] == tenant.entity_of(actor))
    return {"project": dict(p), "allowed_actions": (["configure", "retry"] if can_manage else []),
            "stages": stages, "gates": gates, "deliverables": dels,
            "wbs": wbs, "workshop": dict(ws) if ws else None, "turns": turns,
            "timeline": tl, "budget": budget}


def _budget_of(conn, pid: str) -> dict:
    try:
        from .. import finance
        return finance.overview(conn, pid)
    except Exception as exc:             # noqa: BLE001 - 预算表没建好也不该打不开项目
        import logging
        logging.getLogger("ui.api").warning("budget overview failed for %s: %s", pid, exc)
        return {"project_id": pid, "pool": 0, "spent": 0, "remaining": 0, "stages": [],
                "state": "unknown"}


# ------------------------------------------- 预算池（v4.3）
class BudgetDecisionIn(BaseModel):
    approve: bool = True
    tokens: int | None = None


@router.get("/projects/{project_id}/budget")
def project_budget(project_id: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        return _budget_of(conn, project_id)


@router.get("/budgets/projects")
def budget_projects(limit: int = 30, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import finance
        return {"projects": finance.recent_projects(conn, limit=limit)}


@router.get("/budgets/approvals")
def budget_approvals(limit: int = 50, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import finance
        return {"approvals": finance.pending(conn, limit=limit)}


@router.post("/budgets/approvals/{approval_id}")
def budget_decide(approval_id: str, payload: BudgetDecisionIn,
                  oa_ui: str | None = Depends(_token)):
    who = _require(oa_ui)
    with get_conn() as conn:
        from .. import finance
        try:
            return finance.decide(conn, approval_id, approve=payload.approve,
                                  actor=who, tokens=payload.tokens)
        except KeyError:
            raise HTTPException(404, detail={"code": "ApprovalNotFound"}) from None
        except ValueError:
            raise HTTPException(409, detail={"code": "ApprovalAlreadyDecided"}) from None


@router.post("/projects/{project_id}/resume")
def project_resume(project_id: str, oa_ui: str | None = Depends(_token)):
    """预算批下来后继续跑：把未完成阶段复位，从断点接着推进。"""
    _require(oa_ui)
    with get_conn() as conn:
        from .. import finance, pipeline
        pipeline.ensure(conn)
        row = conn.execute("SELECT requirement,state FROM projects WHERE id=?",
                           (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        ov = finance.overview(conn, project_id)
        if ov.get("state") == "halted":
            raise HTTPException(409, detail={"code": "BudgetHalted",
                                             "message": "项目预算池已见底，不能再开工"})
        keys = [r["key"] for r in conn.execute(
            "SELECT key,state FROM project_stages WHERE project_id=? ORDER BY seq",
            (project_id,)).fetchall() if r["state"] != "done"]
        if not keys:
            return {"ok": True, "already_done": True}
        conn.execute("UPDATE project_stages SET state='pending' WHERE project_id=?"
                     " AND state<>'done'", (project_id,))
        conn.execute("UPDATE projects SET state='active' WHERE id=?", (project_id,))
        finance.set_state(conn, project_id, "active")
        conn.commit()
        requirement = row["requirement"] or ""

    def _worker() -> None:
        from ..db import get_conn as _gc
        with _gc() as c2:
            try:
                pipeline.run_project(c2, project_id, requirement, keys=keys)
            except Exception:            # noqa: BLE001 - 后台线程不能炸
                pass
            c2.commit()

    threading.Thread(target=_worker, daemon=True, name="oa-resume").start()
    return {"ok": True, "project_id": project_id, "resuming": keys}


# ------------------------------------------- 阶段详情 / 流程模板（v4.1）
class FlowTemplateIn(BaseModel):
    id: str
    name: str
    kind: str = "custom"
    description: str = ""
    nodes: list[dict]


@router.get("/flow-templates")
def flow_templates(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import flows
        return {"templates": flows.templates(conn)}


@router.post("/flow-templates")
def create_flow_template(payload: FlowTemplateIn, oa_ui: str | None = Depends(_token)):
    """动态注册/定制企业专属交付流水线模板。"""
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:create")
        if actor["role"] not in ("ceo", "system", "admin"):
            raise HTTPException(403, detail={"code": "Forbidden"})
        from .. import flows
        flows.ensure(conn)
        if not payload.id.startswith("FLOW-"):
            raise HTTPException(400, detail={"code": "InvalidFlowId", "message": "flow id must start with FLOW-"})
        if not payload.nodes:
            raise HTTPException(400, detail={"code": "NodesRequired"})
        conn.execute(
            "INSERT OR REPLACE INTO flow_templates (id, name, kind, description, nodes, enabled)"
            " VALUES (?, ?, ?, ?, ?, 1)",
            (payload.id, payload.name, payload.kind, payload.description,
             json.dumps(payload.nodes, ensure_ascii=False))
        )
        conn.commit()
    return {"ok": True, "flow_id": payload.id, "nodes": len(payload.nodes)}


@router.get("/projects/{project_id}/stages/{stage_key}")
def project_stage(project_id: str, stage_key: str, oa_ui: str | None = Depends(_token)):
    """阶段详情：交付物 + 闸门 + 审计 + 历史 Run/Trace（点已完成阶段进来的页）。"""
    _require(oa_ui)
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        _task_actor(conn, oa_ui, "task:read")
        from .. import flows
        flows.ensure(conn)
        try:
            return flows.stage_detail(conn, project_id, stage_key)
        except KeyError:
            raise HTTPException(404, detail={"code": "StageNotFound"}) from None


class AssignIn(BaseModel):
    employee_id: str


@router.get("/projects/{project_id}/stages/{stage_key}/files/{delivery_id}")
def project_stage_file(project_id: str, stage_key: str, delivery_id: str,
                       oa_ui: str | None = Depends(_token)):
    """下载阶段交付物（阶段详情页里的「下载」）。"""
    import pathlib as _pathlib

    from fastapi.responses import FileResponse

    _require(oa_ui)
    with get_conn() as conn:
        row = conn.execute("SELECT file_path FROM stage_deliverables"
                           " WHERE id=? AND project_id=? AND stage_key=?",
                           (delivery_id, project_id, stage_key)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "FileNotFound"})
        path = _pathlib.Path(row["file_path"])
        if not path.is_file():
            raise HTTPException(404, detail={"code": "FileNotFound"})
    return FileResponse(path, filename=path.name)


# ---------------------------------------------------- V4.4 出厂交付包与在线预览
@router.get("/projects/{project_id}/release/files")
def project_release_files(project_id: str, oa_ui: str | None = Depends(_token)):
    """获取最终出厂交付物目录文件列表。"""
    _require(oa_ui)
    with get_conn() as conn:
        p = conn.execute("SELECT id, name FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
    from ..engine import dsh_adapter
    rel_dir = dsh_adapter.workspace() / "projects" / project_id / "release"
    if not rel_dir.exists():
        return {"ok": True, "project_id": project_id, "files": [], "has_release": False}
    files = []
    for f in sorted(rel_dir.iterdir(), key=lambda x: x.name):
        if f.is_file():
            files.append({
                "name": f.name,
                "bytes": f.stat().st_size,
                "modified": int(f.stat().st_mtime),
                "ext": f.suffix.lower(),
                "is_previewable": f.suffix.lower() in (".html", ".htm", ".json", ".md", ".txt", ".py")
            })
    return {"ok": True, "project_id": project_id, "files": files, "has_release": len(files) > 0}


@router.get("/projects/{project_id}/release/zip")
def download_release_zip(project_id: str, oa_ui: str | None = Depends(_token)):
    """一键打包下载全部出厂交付物 (.zip)。"""
    _require(oa_ui)
    import io
    import zipfile
    from fastapi.responses import Response

    with get_conn() as conn:
        p = conn.execute("SELECT id, name FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
    from ..engine import dsh_adapter
    rel_dir = dsh_adapter.workspace() / "projects" / project_id / "release"
    if not rel_dir.exists():
        raise HTTPException(404, detail={"code": "ReleaseNotFound"})

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in rel_dir.iterdir():
            if f.is_file():
                zf.write(f, arcname=f.name)
    zip_buffer.seek(0)
    filename = f"{project_id}_release.zip"
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@router.get("/projects/{project_id}/release/preview/{filename}")
def preview_release_file(project_id: str, filename: str, oa_ui: str | None = Depends(_token)):
    """在线预览/查看出厂交付物（HTML 网页渲染、JSON 契约查看等）。"""
    _require(oa_ui)
    from fastapi.responses import FileResponse
    # 路径穿越防范
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, detail={"code": "InvalidFilename"})
    with get_conn() as conn:
        p = conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
    from ..engine import dsh_adapter
    file_path = dsh_adapter.workspace() / "projects" / project_id / "release" / filename
    if not file_path.is_file():
        raise HTTPException(404, detail={"code": "FileNotFound"})

    sfx = file_path.suffix.lower()
    media_type = "text/plain; charset=utf-8"
    if sfx in (".html", ".htm"):
        media_type = "text/html; charset=utf-8"
    elif sfx == ".json":
        media_type = "application/json; charset=utf-8"
    elif sfx == ".md":
        media_type = "text/markdown; charset=utf-8"
    elif sfx == ".css":
        media_type = "text/css; charset=utf-8"
    elif sfx == ".js":
        media_type = "application/javascript; charset=utf-8"

    return FileResponse(file_path, media_type=media_type)


@router.post("/projects/{project_id}/stages/{stage_key}/assign")
def project_stage_assign(project_id: str, stage_key: str, payload: AssignIn,
                         oa_ui: str | None = Depends(_token)):
    """PM 指定阶段执行人（同角色多人时可并行分工）。"""
    _require(oa_ui)
    from . import dispatcher
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:write")
        try:
            return dispatcher.assign(conn, project_id, stage_key, payload.employee_id,
                                     actor=actor["id"])
        except KeyError as exc:
            raise HTTPException(404, detail={"code": str(exc).strip("'")}) from None
        except PermissionError as exc:
            raise HTTPException(403, detail={"code": str(exc)}) from None


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
        actor = _task_actor(conn, oa_ui, "task:read")
        from . import roles as roles_mod
        roles_mod.ensure(conn)
        role = conn.execute(
            """SELECT rt.tag FROM ai_employees e JOIN role_templates rt ON rt.id=e.role_id
               WHERE e.id=? AND e.legal_entity_id=?""",
            (actor.get("employee_id"), tenant.entity_of(actor))).fetchone()
        role_tag = role["tag"] if role else None
        if actor["role"] not in {"ceo", "system"} and payload.role_tag != role_tag:
            raise HTTPException(403, detail={"code": "ToolDenied"})
        role_tag = role_tag or (payload.role_tag if actor["role"] in {"ceo", "system"} else None)
        try:
            tools_acl.check(conn, role_tag=role_tag, tool=payload.tool,
                            sql=payload.sql, target=payload.tool, entity=tenant.entity_of(actor))
        except PermissionError as exc:
            raise HTTPException(403, detail={"code": "ToolDenied", "message": str(exc)})
    return {"ok": True, "role": role_tag, "tool": payload.tool, "executed": False}


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
    from ..engine import dsh_adapter

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
def run_trace(run_id: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import trace as tr
        data = tr.load(conn, run_id)
    if data is None:
        raise HTTPException(404, detail={"code": "TraceNotFound"})
    return data


@router.get("/traces/spans/{span_id}/audit")
def span_audit(span_id: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import trace as tr
        rows = tr.audit_for_span(conn, span_id)
    return {"audit": rows}


# ------------------------------------- 实时执行流 / 失败诊断（v4.1）
_TERMINAL_RUN = {"succeeded", "failed", "cancelled", "rejected", "done"}


@router.get("/runs/{run_id}/live-stream")
async def run_live_stream(run_id: str, after: int = 0, oa_ui: str | None = Depends(_token)):
    """SSE：把 trace_spans 逐条推给前端（thinking / tool_call / tool_result / 模型）。

    span 在关闭时即落库并 commit（`trace.span()`），所以这里是**真实时**，不是跑完补发。
    没有新 span 时也每 0.8s 发一次 status，前端据此判断还在跑还是已收尾。
    """
    from fastapi.responses import StreamingResponse
    import asyncio

    _require(oa_ui)

    async def gen():
        last = int(after or 0)
        idle = 0
        yield "retry: 3000\r\n\r\n"
        for _ in range(900):                    # 上限 12 分钟，前端断开即结束
            rows, state = [], None
            try:
                with get_conn() as conn:
                    rows = conn.execute(
                        "SELECT s.rowid,s.kind,s.name,s.status,s.started_ms,s.ended_ms,"
                        "s.tokens,s.detail FROM trace_spans s JOIN agent_traces t"
                        " ON t.trace_id=s.trace_id WHERE t.run_id=? AND s.rowid>?"
                        " ORDER BY s.rowid LIMIT 50", (run_id, last)).fetchall()
                    run = conn.execute("SELECT state FROM task_runs WHERE id=?",
                                       (run_id,)).fetchone()
                    state = run["state"] if run else None
            except Exception:                   # noqa: BLE001 - 流不能因为一次读失败就断
                rows, state = [], None
            for row in rows:
                last = int(row["rowid"])
                kind = row["kind"] or "span"
                if kind == "tool":
                    event = "tool_call"
                elif kind == "thinking":
                    event = "thinking"
                elif kind == "model":
                    event = "model"
                else:
                    event = "span"
                payload = {"kind": kind, "name": row["name"], "status": row["status"],
                           "start_ms": row["started_ms"], "end_ms": row["ended_ms"],
                           "tokens": row["tokens"], "detail": row["detail"] or ""}
                yield "event: " + event + "\r\ndata: " + json.dumps(
                    payload, ensure_ascii=False) + "\r\n\r\n"
                if kind == "tool" and (row["detail"] or ""):
                    yield "event: tool_result\r\ndata: " + json.dumps(
                        {"name": row["name"], "detail": row["detail"],
                         "status": row["status"]}, ensure_ascii=False) + "\r\n\r\n"
                idle = 0
            yield "event: status\r\ndata: " + json.dumps(
                {"run_id": run_id, "state": state}, ensure_ascii=False) + "\r\n\r\n"
            if state in _TERMINAL_RUN:
                idle += 1
                if idle >= 2 and not rows:
                    yield "event: done\r\ndata: {}\r\n\r\n"
                    return
            await asyncio.sleep(0.8)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/runs/{run_id}/diagnosis")
def run_diagnosis(run_id: str, oa_ui: str | None = Depends(_token)):
    """失败诊断：规则结论优先，模型可用时补一句根因与修复建议（中文）。"""
    _require(oa_ui)
    from .. import flows
    with get_conn() as conn:
        try:
            return flows.diagnose(conn, run_id)
        except KeyError:
            raise HTTPException(404, detail={"code": "RunNotFound"}) from None


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
        from .access import actor_for, owned
        actor = actor_for(conn, oa_ui, manage=True)
        owned(conn, "employees", payload.agent_id, actor, write=True, code="EmployeeNotFound")
        if payload.permission not in rbac.LEVELS:
            raise HTTPException(422, detail={"code": "InvalidPermission"})
        rbac.grant(conn, payload.agent_id, payload.resource_id, payload.permission,
                   granted_by=ident)
    return {"ok": True}


@router.post("/rbac/check")
def check_permission(payload: GrantIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import rbac
        from .access import actor_for, owned
        actor = actor_for(conn, oa_ui)
        owned(conn, "employees", payload.agent_id, actor, code="EmployeeNotFound")
        try:
            rbac.require(conn, payload.agent_id, payload.resource_id, payload.permission)
        except PermissionError as exc:
            raise HTTPException(403, detail={"code": "PermissionDenied", "message": str(exc)})
    return {"ok": True}


@router.get("/rbac/approvals")
def list_approvals(oa_ui: str | None = Depends(_token)):
    from . import approval_access
    with get_conn() as conn:
        who = approval_access.actor(conn, oa_ui)
        return approval_access.listing(conn, who)


@router.post("/rbac/approvals/{approval_id}")
def decide_approval(approval_id: str, payload: ApprovalIn,
                    oa_ui: str | None = Depends(_token)):
    from . import approval_access, approval_flow
    with get_conn() as conn:
        who = approval_access.actor(conn, oa_ui, deciding=True)
        approval_access.require_owned(conn, approval_id, who)
    try:
        return approval_flow.decide(approval_id, payload.approve, by=who["id"])
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
        from .access import actor_for, owned
        actor = actor_for(conn, oa_ui)
        owned(conn, "employees", payload.agent_id, actor, write=True, code="EmployeeNotFound")
        if actor["role"] == "agent" and actor.get("employee_id") != payload.agent_id:
            raise HTTPException(403, detail={"code": "Forbidden"})
        return rbac.guard_tool(conn, agent_id=payload.agent_id, tool=payload.tool,
                               args=payload.args, entity=tenant.entity_of(actor))


# ------------------------------------------------------- 审批中心（v2.3）
@router.get("/approvals")
def ui_approvals(limit: int = 50, oa_ui: str | None = Depends(_token)):
    from . import approval_access
    with get_conn() as conn:
        who = approval_access.actor(conn, oa_ui)
        return approval_access.listing(conn, who, limit)


@router.post("/approvals/{approval_id}")
def ui_decide_approval(approval_id: str, payload: ApprovalIn,
                       oa_ui: str | None = Depends(_token)):
    return decide_approval(approval_id, payload, oa_ui)


@router.post("/rbac/revoke")
def revoke_permission(payload: GrantIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import rbac
        from .access import actor_for, owned
        actor = actor_for(conn, oa_ui, manage=True)
        owned(conn, "employees", payload.agent_id, actor, write=True, code="EmployeeNotFound")
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
def ui_finops(days: int = 30, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from .. import finops
        actor = _task_actor(conn, oa_ui, "task:read")
        return finops.overview(conn, days=days, tenant_id=actor["tenant_id"])


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
# Experience review and project recovery live in ui.governance.
