"""v1.1 管理面：群组/成员、员工、设置、文件库、知识库、告警、定时任务。

设计约束（董事长原话）：
- 任何移除/解雇都要写审计（member_kicked / agent_fired）。
- 一律软删除，禁止物理删除，历史产出要能追溯到人。
- 解雇时立刻释放其 token 预算并停掉在跑的 run。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import sqlite3
import time
import uuid

from fastapi import APIRouter, Cookie, Depends, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel

from ..audit import write_audit
from ..db import get_conn
from . import intel, store

router = APIRouter(prefix="/ui", tags=["ui-admin"])


def _ident(oa_ui: str | None = Cookie(default=None),
           authorization: str | None = Header(default=None),
           t: str | None = Query(default=None)) -> str | None:
    """Same dual auth as api.py: cookie in the browser, Bearer in the shell."""
    from .api import _token
    return _token(oa_ui, authorization, t)


def _require(token: str | None) -> str:
    from .api import _require as _r
    return _r(token)


Auth = Depends(_ident)


def _row_for(conn: sqlite3.Connection, chat_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "ChatNotFound"})
    return row


# --------------------------------------------------------------- 群组管理

class MembersIn(BaseModel):
    employee_ids: list[str]


@router.get("/groups/{chat_id}/members")
def group_members(chat_id: str, token: str | None = Auth):
    _require(token)
    with get_conn() as conn:
        _row_for(conn, chat_id)
        rows = conn.execute(
            """SELECT m.employee_id, m.role, e.name, e.model, e.status, p.title
               FROM chat_members m JOIN ai_employees e ON e.id=m.employee_id
               LEFT JOIN positions p ON p.id=e.position_id
               WHERE m.chat_id=? AND COALESCE(m.state,'active')='active'
               ORDER BY CASE m.role WHEN 'pm' THEN 0 WHEN 'reviewer' THEN 2 ELSE 1 END""",
            (chat_id,)).fetchall()
    return {"members": [dict(r) for r in rows]}


@router.post("/groups/{chat_id}/members")
def invite_members(chat_id: str, payload: MembersIn, token: str | None = Auth):
    who = _require(token)
    with get_conn() as conn:
        _row_for(conn, chat_id)
        from .access import actor_for, owned
        actor = actor_for(conn, token, manage=True)
        owned(conn, "chats", chat_id, actor, write=True, code="ChatNotFound")
        # Validate the complete batch before adding any membership.
        for emp_id in payload.employee_ids:
            owned(conn, "employees", emp_id, actor, write=True, code="EmployeeNotFound")
        added = []
        for emp_id in payload.employee_ids:
            emp = conn.execute("SELECT * FROM ai_employees WHERE id=?", (emp_id,)).fetchone()
            if emp is None or (emp["status"] or "online") == "fired":
                continue
            existing = conn.execute(
                "SELECT * FROM chat_members WHERE chat_id=? AND employee_id=?",
                (chat_id, emp_id)).fetchone()
            if existing and (existing["state"] or "active") == "active":
                continue
            if existing:
                conn.execute("UPDATE chat_members SET state='active' WHERE chat_id=? AND employee_id=?",
                             (chat_id, emp_id))
            else:
                role = "reviewer" if conn.execute(
                    "SELECT COUNT(*) n FROM chat_members WHERE chat_id=? AND COALESCE(state,'active')='active'",
                    (chat_id,)).fetchone()["n"] >= 2 else "member"
                conn.execute("INSERT INTO chat_members (chat_id, employee_id, role, state)"
                             " VALUES (?,?,?,'active')", (chat_id, emp_id, role))
            added.append(emp_id)
        conn.commit()
        if added:
            write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="admin", action="members_invited",
                        target=chat_id, layer="system",
                        payload={"employee_ids": added}, response={"by": who})
            conn.commit()
    return {"ok": True, "added": added}


@router.delete("/groups/{chat_id}/members/{employee_id}")
def kick_member(chat_id: str, employee_id: str, token: str | None = Auth):
    who = _require(token)
    with get_conn() as conn:
        _row_for(conn, chat_id)
        row = conn.execute("SELECT * FROM chat_members WHERE chat_id=? AND employee_id=?",
                           (chat_id, employee_id)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "NotMember"})
        conn.execute("UPDATE chat_members SET state='removed' WHERE chat_id=? AND employee_id=?",
                     (chat_id, employee_id))
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="admin", action="member_kicked",
                    target=employee_id, layer="system",
                    payload={"chat_id": chat_id, "role": row["role"]}, response={"by": who})
        conn.commit()
        store.add_message(conn, chat_id, "system", "有成员被移出群聊。", kind="text")
    return {"ok": True}


@router.post("/groups/{chat_id}/leave")
def leave_group(chat_id: str, token: str | None = Auth):
    who = _require(token)
    with get_conn() as conn:
        chat = _row_for(conn, chat_id)
        members = store.members(conn, chat_id)
        is_pm_owner = bool(members) and members[0]["role"] == "pm"
        if is_pm_owner or chat["kind"] == "group" and len(members) <= 1:
            conn.execute("UPDATE chats SET title=? WHERE id=?",
                         (chat["title"] + "（已解散）", chat_id))
            conn.execute("UPDATE chat_members SET state='removed' WHERE chat_id=?", (chat_id,))
            action = "group_dissolved"
            store.add_message(conn, chat_id, "system", "群聊已解散。", kind="text")
        else:
            conn.execute("UPDATE chats SET kind='left' WHERE id=?", (chat_id,))
            action = "group_left"
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="admin", action=action,
                    target=chat_id, layer="system", payload={}, response={"by": who})
        conn.commit()
    return {"ok": True, "action": action}


# --------------------------------------------------------------- 员工管理

class EmployeePatch(BaseModel):
    name: str | None = None
    preset: str | None = None          # standard | cordis（创造模式）
    soul: str | None = None
    model: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    focus: str | None = None


@router.patch("/employees/{employee_id}")
def edit_employee(employee_id: str, payload: EmployeePatch, token: str | None = Auth):
    who = _require(token)
    with get_conn() as conn:
        emp = conn.execute("SELECT * FROM ai_employees WHERE id=?", (employee_id,)).fetchone()
        if emp is None:
            raise HTTPException(404, detail={"code": "EmployeeNotFound"})
        fields, values = [], []
        if payload.preset is not None and payload.preset not in ("standard", "cordis"):
            raise HTTPException(400, detail={"code": "BadPreset"})
        for col in ("name", "soul", "model", "focus"):
            if col == "preset":
                continue
            val = getattr(payload, col)
            if val is not None:
                fields.append(f"{col}=?")
                values.append(val)
        if payload.preset is not None:
            fields.append("preset=?")
            values.append(payload.preset)
        if payload.api_key is not None:
            fields.append("api_key=?")
            values.append(payload.api_key)
        if payload.api_base is not None:
            fields.append("api_base=?")
            values.append(payload.api_base)
        if fields:
            values.append(employee_id)
            conn.execute(f"UPDATE ai_employees SET {', '.join(fields)} WHERE id=?", values)
            write_audit(conn, legal_entity_id=emp["legal_entity_id"], verb="admin",
                        action="agent_edited", target=employee_id, layer="system",
                        payload={k: v for k, v in payload.model_dump().items()
                                 if k != "api_key" and v is not None},
                        response={"by": who, "api_key_changed": payload.api_key is not None})
            conn.commit()
    return {"ok": True}


@router.post("/employees/{employee_id}/fire")
def fire_employee(employee_id: str, token: str | None = Auth):
    """软删除 + 释放预算 + 停掉在跑的 run。绝不 DELETE。"""
    who = _require(token)
    with get_conn() as conn:
        emp = conn.execute("SELECT * FROM ai_employees WHERE id=?", (employee_id,)).fetchone()
        if emp is None:
            raise HTTPException(404, detail={"code": "EmployeeNotFound"})
        entity = emp["legal_entity_id"]

        conn.execute("UPDATE ai_employees SET status='fired' WHERE id=?", (employee_id,))
        ident_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM identities WHERE employee_id=?", (employee_id,))]
        for iid in ident_ids:
            conn.execute("UPDATE identities SET state='inactive' WHERE id=?", (iid,))
        # 预算：把上限压到已用量，等于立刻释放剩余额度
        released = conn.execute(
            "UPDATE budgets SET limit_tokens=used_tokens WHERE identity_id IN"
            " (SELECT id FROM identities WHERE employee_id=?)", (employee_id,)).rowcount
        # 在跑的 run：标记取消（进程随 ACP 超时回收，这里不杀 python 自己）
        stopped = conn.execute(
            """UPDATE task_runs SET state='cancelled', exit_code=-9
               WHERE state IN ('spawned','running') AND task_id IN
                 (SELECT id FROM tasks WHERE owner_employee_id=? OR legal_entity_id=?)""",
            (employee_id, entity)).rowcount
        # 群成员身份也停掉
        conn.execute("UPDATE chat_members SET state='removed' WHERE employee_id=?", (employee_id,))
        conn.execute("UPDATE ai_employees SET focus=? WHERE id=?",
                     (f"fired at {who}", employee_id))
        write_audit(conn, legal_entity_id=entity, verb="admin", action="agent_fired",
                    target=employee_id, layer="system",
                    payload={"name": emp["name"], "budgets_released": released,
                             "runs_stopped": stopped, "identities": ident_ids},
                    response={"by": who})
        conn.commit()
    return {"ok": True, "budgets_released": released, "runs_stopped": stopped}


# --------------------------------------------------------------- 设置 / 账号

class SettingsIn(BaseModel):
    default_model: str | None = None
    default_api_base: str | None = None
    theme: str | None = None
    # v4.2.1：单阶段 token 预算（老项目兜底，界面已改为弹性系数）
    flow_stage_budget: int | None = None
    # v4.3：动态财务引擎的兜底安全锁
    budget_elastic: float | None = None
    project_budget_cap: int | None = None
    budget_pause_ratio: float | None = None


@router.get("/settings")
def get_settings(token: str | None = Auth):
    _require(token)
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    vals = {r["key"]: r["value"] for r in rows}
    from .. import flows, finance
    with get_conn() as conn:
        budget = flows.stage_budget(conn)
        fin = finance.config(conn)
    return {"default_model": vals.get("default_model") or os.environ.get("LLM_MODEL", "deepseek-chat"),
            "default_api_base": vals.get("default_api_base") or os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1"),
            "theme": vals.get("theme") or "dark",
            "flow_stage_budget": budget,
            "flow_stage_budget_source": ("settings" if vals.get("flow_stage_budget")
                                          else "env"),
            "budget_elastic": fin["elastic"],
            "project_budget_cap": fin["cap"],
            "budget_pause_ratio": fin["pause_ratio"],
            "api_key_set": bool(os.environ.get("DEEPSEEK_API_KEY"))}


@router.put("/settings")
def put_settings(payload: SettingsIn, token: str | None = Auth):
    who = _require(token)
    data = payload.model_dump()
    budget = data.pop("flow_stage_budget", None)
    elastic = data.pop("budget_elastic", None)
    cap = data.pop("project_budget_cap", None)
    pause_ratio = data.pop("budget_pause_ratio", None)
    with get_conn() as conn:
        if budget is not None:
            from .. import flows
            try:
                flows.set_stage_budget(conn, int(budget))
            except (TypeError, ValueError):
                raise HTTPException(400, detail={"code": "InvalidFlowBudget"}) from None
        if any(v is not None for v in (elastic, cap, pause_ratio)):
            from .. import finance
            try:
                finance.set_config(conn, elastic=elastic, cap=cap, pause_ratio=pause_ratio)
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, detail={"code": str(exc)}) from None
        for k, v in data.items():
            if v is None:
                continue
            conn.execute("INSERT INTO settings (key,value) VALUES (?,?)"
                         " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="admin", action="settings_changed",
                    target="settings", layer="system", payload={**data,
                                                                "flow_stage_budget": budget},
                    response={"by": who})
        conn.commit()
    return {"ok": True}


class SecretIn(BaseModel):
    secret: str


@router.post("/account/secret")
def change_secret(payload: SecretIn, token: str | None = Auth):
    who = _require(token)
    if len(payload.secret) < 16:
        raise HTTPException(400, detail={"code": "SecretTooShort"})
    with get_conn() as conn:
        conn.execute("UPDATE identities SET secret=? WHERE id=?", (payload.secret, who))
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="admin", action="secret_rotated",
                    target=who, layer="system", payload={}, response={})
        conn.commit()
    return {"ok": True}


@router.get("/account")
def account(token: str | None = Auth):
    who = _require(token)
    with get_conn() as conn:
        row = conn.execute("SELECT id,name,role,legal_entity_id,state FROM identities WHERE id=?",
                           (who,)).fetchone()
        prefs = intel.preferences(conn, who)
    return {**dict(row), "preferences": prefs}

class PreferencesIn(BaseModel):
    preferences: dict[str, str]


@router.put("/account/preferences")
def update_preferences(payload: PreferencesIn, token: str | None = Auth):
    who = _require(token)
    with get_conn() as conn:
        intel.clear_preferences(conn, who)
        for k, v in payload.preferences.items():
            if k and v and str(v).strip():
                intel.save_preference(conn, who, k.strip(), str(v).strip())
        prefs = intel.preferences(conn, who)
    return {"ok": True, "preferences": prefs}


@router.delete("/account/preferences/{key}")
def delete_preference(key: str, token: str | None = Auth):
    who = _require(token)
    with get_conn() as conn:
        intel.delete_preference(conn, who, key)
        prefs = intel.preferences(conn, who)
    return {"ok": True, "preferences": prefs}


# --------------------------------------------------------------- 文件库

@router.get("/files")
def files(token: str | None = Auth, limit: int = 100, include_legacy: int = 0):
    """文件库。默认只列**当前工作区**（LOCALAPPDATA 下的 workspace）的产物；
    旧仓库工作区仅作历史归档，需要时传 include_legacy=1。"""
    _require(token)
    from ..engine import dsh_adapter
    from .. import tenant
    from .api import _task_actor
    from .artifacts import unique_paths
    root = str(dsh_adapter.workspace())
    like = root.rstrip("\\") + "%"
    where = "COALESCE(a.state,'active')='active'"
    args: list = []
    if not include_legacy:
        where += " AND a.file_path LIKE ?"
        args.append(like)
    with get_conn() as conn:
        actor = _task_actor(conn, token, "task:read")
        scoped, scoped_args = tenant.clause(conn, actor, "t.legal_entity_id")
        where += " AND " + scoped
        args.extend(scoped_args)
        where += " AND t.tenant_id=?"
        args.append(actor["tenant_id"])
        rows = conn.execute(
            f"""SELECT a.id, a.file_path, a.bytes, a.created_at, r.task_id, t.title,
                       t.legal_entity_id
                FROM artifacts a JOIN task_runs r ON r.id=a.run_id
                JOIN tasks t ON t.id=r.task_id
                WHERE {where}
                ORDER BY r.run_seq DESC,a.art_seq DESC,a.rowid DESC""", args).fetchall()
    rows = sorted(unique_paths(rows), key=lambda row: row["created_at"] or "", reverse=True)
    out = []
    for r in rows[:max(1, min(500, limit))]:
        p = pathlib.Path(r["file_path"])
        out.append({**dict(r), "name": p.name, "exists": p.exists(),
                    "size": p.stat().st_size if p.exists() else 0})
    return {"files": out, "total": len(rows)}


def _scoped_artifact(conn, artifact_id: str, token: str | None = None, *, actor=None):
    """Authenticate the current identity and authorize the artifact's task first."""
    from .. import tenant
    from .api import _task_actor
    if actor is None:
        actor = _task_actor(conn, token, "task:read")
    scoped, args = tenant.clause(conn, actor, "t.legal_entity_id")
    row = conn.execute(
        "SELECT a.* FROM artifacts a JOIN task_runs r ON r.id=a.run_id"
        " JOIN tasks t ON t.id=r.task_id"
        " WHERE a.id=? AND COALESCE(a.state,'active')='active' AND t.tenant_id=? AND " + scoped,
        [artifact_id, actor["tenant_id"], *args]).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "FileNotFound"})
    return row


@router.get("/files/{artifact_id}/download")
def download(artifact_id: str, token: str | None = Auth):
    from fastapi.responses import FileResponse
    with get_conn() as conn:
        row = _scoped_artifact(conn, artifact_id, token)
    if not pathlib.Path(row["file_path"]).exists():
        raise HTTPException(404, detail={"code": "FileNotFound"})
    return FileResponse(row["file_path"], filename=pathlib.Path(row["file_path"]).name)


PREVIEW_TYPES = {
    ".html": "text/html", ".htm": "text/html", ".css": "text/css",
    ".js": "text/javascript", ".json": "application/json", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    # Chromium does not render text/markdown in an iframe; serve readable text.
    ".md": "text/plain", ".txt": "text/plain",
}

# 产物里的 HTML 是模型写的，给它一个「不透明来源 + 断网」的环境：
# 能跑 JS、能渲染，但拿不到控制台的 Cookie/Token，也发不出请求。
PREVIEW_CSP = ("sandbox allow-scripts; default-src 'none'; "
               "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
               "img-src data: blob:; font-src data: blob:; media-src data: blob:; "
               "connect-src 'none'; frame-src 'none'; object-src 'none'; "
               "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
PREVIEW_TICKET_TTL = 120


def _preview_signature(payload: str) -> str:
    from .api import _secret
    return hmac.new(_secret(), b"artifact-preview-v1\0" + payload.encode("ascii"), hashlib.sha256).hexdigest()


def _make_preview_ticket(artifact_id: str, identity_id: str) -> tuple[str, int]:
    expires = int(time.time()) + PREVIEW_TICKET_TTL
    raw = json.dumps({"artifact": artifact_id, "identity": identity_id, "expires": expires},
                     separators=(",", ":"), sort_keys=True).encode()
    payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"artifact-preview.v1.{payload}.{_preview_signature(payload)}", expires


def _preview_ticket_actor(conn, artifact_id: str, ticket: str) -> dict:
    from .. import identity
    try:
        if len(ticket) > 2048:
            raise ValueError("oversized ticket")
        namespace, version, payload, signature = ticket.split(".")
        if namespace != "artifact-preview" or version != "v1" or not hmac.compare_digest(signature, _preview_signature(payload)):
            raise ValueError("invalid signature")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if not isinstance(claims, dict):
            raise ValueError("invalid claims")
        expires = claims.get("expires")
        if (claims.get("artifact") != artifact_id or not isinstance(claims.get("identity"), str)
                or type(expires) is not int or expires <= time.time()):
            raise ValueError("expired or mismatched ticket")
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(401, detail={"code": "InvalidPreviewTicket"}) from None
    actor = identity.get_identity(conn, claims["identity"])
    if actor is None:
        raise HTTPException(401, detail={"code": "UnknownIdentity"})
    if not identity.has_scope(actor, "task:read"):
        raise HTTPException(403, detail={"code": "Forbidden", "scope": "task:read"})
    return dict(actor)


def _preview_ident(oa_ui: str | None = Cookie(default=None),
                   authorization: str | None = Header(default=None)) -> str | None:
    """A preview session can come from a header/cookie, never its document URL."""
    from .api import _token
    return _token(oa_ui, authorization, None)


@router.post("/files/{artifact_id}/preview-ticket")
def preview_ticket(artifact_id: str, token: str | None = Depends(_preview_ident)):
    from .api import _task_actor
    with get_conn() as conn:
        actor = _task_actor(conn, token, "task:read")
        _scoped_artifact(conn, artifact_id, actor=actor)
        ticket, expires = _make_preview_ticket(artifact_id, actor["id"])
    return {"ticket": ticket, "expires_at": expires}

PREVIEW_CLOSE_SCRIPT = """<script>
window.addEventListener('keydown', function (event) {
  if (event.key === 'Escape' && !event.isComposing) {
    event.preventDefault();
    window.parent.postMessage({kind: 'agent-os-preview-close'}, '*');
  }
}, true);
</script>"""


def _embedded_preview(path: pathlib.Path, media_type: str) -> str:
    """Supply an Escape listener inside the opaque frame without parent access.

    Text is escaped and SVG uses image context. The message carries no content
    or credentials; the parent must check this frame's WindowProxy as source.
    """
    import base64
    import html
    import re

    maximum = 10 * 1024 * 1024
    with path.open("rb") as source:
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        content = "<p>文件较大，请下载后查看。</p>"
    elif media_type.startswith("image/"):
        encoded = base64.b64encode(raw).decode("ascii")
        content = (f'<img src="data:{media_type};base64,{encoded}" '
                   f'alt="{html.escape(path.name, quote=True)}" '
                   'style="display:block;max-width:100%;height:auto;margin:auto">')
    elif media_type == "text/html":
        page = raw.decode("utf-8-sig", errors="replace")
        # Register before artifact scripts while retaining the document mode.
        doctype = re.match(r"\s*<!doctype[^>]*>", page, flags=re.IGNORECASE)
        if doctype:
            return page[:doctype.end()] + PREVIEW_CLOSE_SCRIPT + page[doctype.end():]
        return PREVIEW_CLOSE_SCRIPT + page
    else:
        content = "<pre>" + html.escape(raw.decode("utf-8-sig", errors="replace")) + "</pre>"
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='referrer' content='no-referrer'>"
            "<style>body{margin:20px;color:#172033;background:#fff;font:14px/1.65 ui-monospace,monospace}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0}</style>"
            + PREVIEW_CLOSE_SCRIPT + "</head><body>" + content + "</body></html>")


@router.get("/files/{artifact_id}/preview")
def preview(artifact_id: str, token: str | None = Depends(_preview_ident), embed: bool = False,
            ticket: str | None = None, t: str | None = Query(default=None, include_in_schema=False)):
    from fastapi.responses import FileResponse, HTMLResponse
    if t is not None:
        raise HTTPException(400, detail={"code": "UnsafePreviewCredential"})
    with get_conn() as conn:
        actor = _preview_ticket_actor(conn, artifact_id, ticket) if ticket is not None else None
        row = _scoped_artifact(conn, artifact_id, token, actor=actor)
    path = pathlib.Path(row["file_path"])
    if not path.exists():
        raise HTTPException(404, detail={"code": "FileGone"})
    mt = PREVIEW_TYPES.get(path.suffix.lower(), "text/plain")
    headers = {"Content-Security-Policy": PREVIEW_CSP, "X-Content-Type-Options": "nosniff"}
    if embed:
        headers["Cache-Control"] = "no-store"
        return HTMLResponse(_embedded_preview(path, mt), headers=headers)
    return FileResponse(str(path), media_type=mt, headers=headers)


@router.delete("/files/{artifact_id}")
def delete_file(artifact_id: str, token: str | None = Auth):
    from .. import tenant
    from .api import _task_actor, _task_write_entities
    from .artifacts import path_key
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actor = _task_actor(conn, token, "task:*")
        row = conn.execute("SELECT a.*,r.task_id FROM artifacts a JOIN task_runs r ON r.id=a.run_id"
                           " WHERE a.id=?", (artifact_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "FileNotFound"})
        task = tenant.scoped_task(conn, row["task_id"], _task_write_entities(conn, actor))
        key = path_key(row["file_path"])
        same = [a for a in conn.execute(
            "SELECT a.id,a.file_path FROM artifacts a JOIN task_runs r ON r.id=a.run_id"
            " WHERE r.task_id=? AND COALESCE(a.state,'active')='active'", (row["task_id"],))
                if path_key(a["file_path"]) == key]
        aliases = [a["id"] for a in same]
        conn.executemany("UPDATE artifacts SET state='deleted' WHERE id=?", [(a,) for a in aliases])
        # 物理路径：同一份文件可能被多次登记；黑板与阶段交付物里也会各留一条
        all_paths = {str(a["file_path"]) for a in same if a["file_path"]}
        # 若别的任务（可能是别的租户）还在引用同一路径，这份物理文件与共享记录必须保留
        shared_keys = {path_key(o["file_path"]) for o in conn.execute(
            "SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id=a.run_id"
            " WHERE COALESCE(a.state,'active')='active' AND r.task_id<>?",
            (row["task_id"],)).fetchall() if o["file_path"]}
        kept_shared = {p for p in all_paths if path_key(p) in shared_keys}
        victims = all_paths - kept_shared
        purged = {"project_artifacts": 0, "stage_deliverables": 0}
        for pth in victims:
            for table in purged:
                try:
                    purged[table] += conn.execute(
                        f"DELETE FROM {table} WHERE file_path=?", (pth,)).rowcount or 0
                except sqlite3.OperationalError:
                    pass
        write_audit(conn, legal_entity_id=task["legal_entity_id"], task_id=task["id"],
                    verb="admin", action="file_deleted", target=artifact_id, layer="system",
                    payload={"file_path": row["file_path"], "artifact_ids": aliases},
                    response={"by": actor["id"], "deleted": len(aliases),
                              "kept_shared": len(kept_shared)})
        conn.commit()

    # 真正落盘删除：只允许删工作区 / data 目录内的文件，避免误伤系统文件
    from ..engine import dsh_adapter
    ws_root = pathlib.Path(dsh_adapter.workspace()).resolve()
    data_root = (pathlib.Path(__file__).resolve().parents[2] / "data").resolve()
    removed = 0
    for pth in victims:
        p = pathlib.Path(pth)
        try:
            p.resolve().relative_to(ws_root)
        except ValueError:
            try:
                p.resolve().relative_to(data_root)
            except ValueError:
                continue          # 不在允许范围内，跳过
        try:
            if p.is_file():
                p.unlink()
                removed += 1
        except OSError:
            pass
    return {"ok": True, "deleted": len(aliases), "files_removed": removed,
            "purged": purged}


# --------------------------------------------------------------- 知识库

@router.get("/kb")
def list_kb(token: str | None = Auth, entity: str | None = None):
    _require(token)
    with get_conn() as conn:
        sql = "SELECT id,name,bytes,legal_entity_id,created_at,state FROM kb_documents"
        args: list = []
        if entity:
            sql += " WHERE legal_entity_id=?"
            args.append(entity)
        sql += " ORDER BY created_at DESC"
        rows = [dict(r) for r in conn.execute(sql, args)]
    return {"documents": rows}


@router.post("/kb")
async def upload_kb(file: UploadFile = File(...), entity: str = "LE-GROUP-HQ",
                    token: str | None = Auth):
    who = _require(token)
    root = pathlib.Path("data/kb") / entity
    root.mkdir(parents=True, exist_ok=True)
    kid = "KB-" + uuid.uuid4().hex[:12]
    dest = root / f"{kid}_{pathlib.Path(file.filename or 'doc.txt').name}"
    raw = await file.read()
    dest.write_bytes(raw)
    excerpt = _extract_text(dest)
    with get_conn() as conn:
        conn.execute("""INSERT INTO kb_documents (id, legal_entity_id, name, path, bytes, excerpt)
                        VALUES (?,?,?,?,?,?)""",
                     (kid, entity, file.filename or dest.name, str(dest), len(raw), excerpt[:1500]))
        write_audit(conn, legal_entity_id=entity, verb="admin", action="kb_uploaded",
                    target=kid, layer="system", payload={"name": file.filename, "bytes": len(raw)},
                    response={"by": who})
        conn.commit()
    return {"ok": True, "id": kid, "bytes": len(raw)}


@router.delete("/kb/{doc_id}")
def delete_kb(doc_id: str, token: str | None = Auth):
    who = _require(token)
    with get_conn() as conn:
        conn.execute("UPDATE kb_documents SET state='deleted' WHERE id=?", (doc_id,))
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="admin", action="kb_deleted",
                    target=doc_id, layer="system", payload={}, response={"by": who})
        conn.commit()
    return {"ok": True}


def _extract_text(path: pathlib.Path) -> str:
    try:
        if path.suffix.lower() == ".pdf":
            from ..docs import pdf_text
            return pdf_text(str(path))
        if path.suffix.lower() in (".docx", ".txt", ".md"):
            if path.suffix.lower() == ".docx":
                from ..docs import docx_text
                return docx_text(str(path))
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:                     # noqa: BLE001
        return f"(无法解析：{exc})"
    return ""


# --------------------------------------------------- 组织架构增删改（v2.0）
class DeptIn(BaseModel):
    name: str
    entity_id: str = "LE-GROUP-HQ"


class DeptPatch(BaseModel):
    name: str


UNASSIGNED = "LE-UNASSIGNED"


def _ensure_unassigned(conn) -> None:
    conn.execute("""INSERT OR IGNORE INTO legal_entities (id, name, type, parent_entity_id)
                    VALUES (?, '未分配', 'department', 'LE-GROUP-HQ')""", (UNASSIGNED,))


@router.get("/departments")
def departments(token: str | None = Auth):
    from .access import actor_for, is_deployment_admin, visible_entities
    with get_conn() as conn:
        actor = actor_for(conn, token)
        if is_deployment_admin(actor):
            _ensure_unassigned(conn)
            conn.commit()
        allowed = set(visible_entities(conn, actor))
        rows = conn.execute(
            """SELECT e.id, e.name, e.type, e.parent_entity_id,
                      (SELECT COUNT(*) FROM ai_employees a WHERE a.legal_entity_id = e.id
                        AND COALESCE(a.status,'online') <> 'fired') AS headcount
               FROM legal_entities e WHERE COALESCE(e.state,'active')='active'
               ORDER BY CASE e.type WHEN 'group_hq' THEN 0 ELSE 1 END, e.name""").fetchall()
    return {"departments": [dict(r) for r in rows if r["id"] in allowed]}


def _department_scope(conn, identifier, token):
    from .access import actor_for
    actor = actor_for(conn, token)
    row = conn.execute("SELECT * FROM legal_entities WHERE id=? AND tenant_id=?",
                       (identifier, actor["tenant_id"])).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "DeptNotFound"})
    return row


@router.post("/departments")
def create_department(payload: DeptIn, token: str | None = Auth):
    ident = _require(token)
    import hashlib
    did = "LE-" + hashlib.sha1(payload.name.encode()).hexdigest()[:6].upper()
    with get_conn() as conn:
        parent = _department_scope(conn, payload.entity_id, token)
        conn.execute("""INSERT OR IGNORE INTO legal_entities
                        (id, name, type, parent_entity_id, governance_mode, pack_policy, tenant_id)
                        VALUES (?,?, 'department', ?, 'subsidiary', 'follow_group', ?)""",
                     (did, payload.name, payload.entity_id, parent["tenant_id"]))
        write_audit(conn, legal_entity_id=payload.entity_id, verb="dept", task_id=None,
                    layer="system", action="dept_created", target=did,
                    payload={"name": payload.name}, response={"by": ident})
        conn.commit()
    return {"ok": True, "department_id": did}


@router.patch("/departments/{dept_id}")
def rename_department(dept_id: str, payload: DeptPatch, token: str | None = Auth):
    ident = _require(token)
    with get_conn() as conn:
        _department_scope(conn, dept_id, token)
        conn.execute("UPDATE legal_entities SET name=? WHERE id=?", (payload.name, dept_id))
        write_audit(conn, legal_entity_id=dept_id, verb="dept", task_id=None, layer="system",
                    action="dept_renamed", target=dept_id, payload={"name": payload.name},
                    response={"by": ident})
        conn.commit()
    return {"ok": True}


@router.delete("/departments/{dept_id}")
def delete_department(dept_id: str, token: str | None = Auth):
    """删部门：员工先转「未分配」，再软删部门，全程留痕。"""
    ident = _require(token)
    with get_conn() as conn:
        row = _department_scope(conn, dept_id, token)
        if row["type"] == "group_hq":
            raise HTTPException(400, detail={"code": "CannotDeleteGroup"})
        _ensure_unassigned(conn)
        moved = conn.execute(
            "UPDATE ai_employees SET legal_entity_id=? WHERE legal_entity_id=?",
            (UNASSIGNED, dept_id)).rowcount
        conn.execute("UPDATE legal_entities SET state='archived' WHERE id=?", (dept_id,))
        write_audit(conn, legal_entity_id=dept_id, verb="dept", task_id=None, layer="system",
                    action="dept_deleted", target=dept_id,
                    payload={"moved_employees": moved}, response={"by": ident})
        conn.commit()
    return {"ok": True, "moved": moved}


class BatchFilesDeleteIn(BaseModel):
    ids: list[str]


@router.post("/files/batch-delete")
def batch_delete_files(payload: BatchFilesDeleteIn, token: str | None = Auth):
    _require(token)
    deleted_count = 0
    errors = []
    for aid in payload.ids:
        try:
            delete_file(aid, token=token)
            deleted_count += 1
        except Exception as e:
            errors.append(f"{aid}: {str(e)}")
    return {"ok": True, "deleted": deleted_count, "errors": errors}


@router.post("/files/purge-missing")
def purge_missing_files(token: str | None = Auth):
    who = _require(token)
    import pathlib
    purged_ids = []
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT id, file_path FROM artifacts WHERE COALESCE(state,'active')='active'").fetchall()
        for r in rows:
            p = pathlib.Path(r["file_path"])
            if not p.exists():
                conn.execute("UPDATE artifacts SET state='deleted' WHERE id=?", (r["id"],))
                try:
                    conn.execute("DELETE FROM project_artifacts WHERE file_path=?", (r["file_path"],))
                    conn.execute("DELETE FROM stage_deliverables WHERE file_path=?", (r["file_path"],))
                except Exception:
                    pass
                purged_ids.append(r["id"])
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="admin", action="files_purged_missing",
                    target="artifacts", layer="system", payload={"count": len(purged_ids)},
                    response={"by": who, "purged": len(purged_ids)})
        conn.commit()
    return {"ok": True, "purged": len(purged_ids), "purged_ids": purged_ids}
