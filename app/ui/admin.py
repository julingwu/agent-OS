"""v1.1 管理面：群组/成员、员工、设置、文件库、知识库、告警、定时任务。

设计约束（董事长原话）：
- 任何移除/解雇都要写审计（member_kicked / agent_fired）。
- 一律软删除，禁止物理删除，历史产出要能追溯到人。
- 解雇时立刻释放其 token 预算并停掉在跑的 run。
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets
import sqlite3
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


@router.get("/settings")
def get_settings(token: str | None = Auth):
    _require(token)
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    vals = {r["key"]: r["value"] for r in rows}
    return {"default_model": vals.get("default_model") or os.environ.get("LLM_MODEL", "deepseek-chat"),
            "default_api_base": vals.get("default_api_base") or os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1"),
            "theme": vals.get("theme") or "dark",
            "api_key_set": bool(os.environ.get("DEEPSEEK_API_KEY"))}


@router.put("/settings")
def put_settings(payload: SettingsIn, token: str | None = Auth):
    who = _require(token)
    with get_conn() as conn:
        for k, v in payload.model_dump().items():
            if v is None:
                continue
            conn.execute("INSERT INTO settings (key,value) VALUES (?,?)"
                         " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="admin", action="settings_changed",
                    target="settings", layer="system", payload=payload.model_dump(),
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


# --------------------------------------------------------------- 文件库

@router.get("/files")
def files(token: str | None = Auth, limit: int = 100, include_legacy: int = 0,
          x_tenant_id: str | None = Header(default=None)):
    """文件库。默认只列**当前工作区**（LOCALAPPDATA 下的 workspace）的产物；
    旧仓库工作区仅作历史归档，需要时传 include_legacy=1。"""
    _require(token)
    from ..engine import dsh_adapter
    root = str(dsh_adapter.workspace())
    like = root.rstrip("\\") + "%"
    where = "COALESCE(a.state,'active')='active'"
    args: list = []
    if not include_legacy:
        where += " AND a.file_path LIKE ?"
        args.append(like)
    with get_conn() as conn:
        from .api import _tenant_of
        tid = _tenant_of(conn, _require(token), x_tenant_id)
        rows = conn.execute(
            f"""SELECT a.id, a.file_path, a.bytes, a.created_at, r.task_id, t.title,
                       t.legal_entity_id, t.tenant_id
                FROM artifacts a JOIN task_runs r ON r.id=a.run_id
                JOIN tasks t ON t.id=r.task_id
                WHERE {where} AND COALESCE(t.tenant_id,'TENANT-HQ')=?
                ORDER BY a.created_at DESC LIMIT ?""", (*args, tid, limit)).fetchall()
    out, seen = [], set()
    for r in rows:
        p = pathlib.Path(r["file_path"])
        key = str(p).lower()
        if key in seen:                 # 同一路径只留最新一条（重跑会产生重复记录）
            continue
        seen.add(key)
        out.append({**dict(r), "name": p.name, "exists": p.exists(),
                    "size": p.stat().st_size if p.exists() else 0})
    return {"files": out}


@router.get("/files/{artifact_id}/download")
def download(artifact_id: str, token: str | None = Auth,
             x_tenant_id: str | None = Header(default=None)):
    ident = _require(token)
    from fastapi.responses import FileResponse
    with get_conn() as conn:
        from .api import _tenant_of
        tid = _tenant_of(conn, ident, x_tenant_id)
        row = conn.execute(
            """SELECT a.* FROM artifacts a JOIN task_runs r ON r.id=a.run_id
               JOIN tasks t ON t.id=r.task_id
               WHERE a.id=? AND COALESCE(t.tenant_id,'TENANT-HQ')=?""",
            (artifact_id, tid)).fetchone()
    if row is None or not pathlib.Path(row["file_path"]).exists():
        raise HTTPException(404, detail={"code": "FileNotFound"})
    return FileResponse(row["file_path"], filename=pathlib.Path(row["file_path"]).name)


PREVIEW_TYPES = {
    ".html": "text/html", ".htm": "text/html", ".css": "text/css",
    ".js": "text/javascript", ".json": "application/json", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".md": "text/markdown", ".txt": "text/plain",
}

# 产物里的 HTML 是模型写的，给它一个「不透明来源 + 断网」的环境：
# 能跑 JS、能渲染，但拿不到控制台的 Cookie/Token，也发不出请求。
PREVIEW_CSP = ("sandbox allow-scripts allow-popups; "
               "default-src * data: blob: 'unsafe-inline'; "
               "connect-src 'none'; frame-ancestors 'self'")


@router.get("/files/{artifact_id}/preview")
def preview(artifact_id: str, token: str | None = Auth,
            x_tenant_id: str | None = Header(default=None)):
    ident = _require(token)
    from fastapi.responses import FileResponse
    with get_conn() as conn:
        from .api import _tenant_of
        tid = _tenant_of(conn, ident, x_tenant_id)
        row = conn.execute(
            """SELECT a.* FROM artifacts a JOIN task_runs r ON r.id=a.run_id
               JOIN tasks t ON t.id=r.task_id
               WHERE a.id=? AND COALESCE(t.tenant_id,'TENANT-HQ')=?""",
            (artifact_id, tid)).fetchone()
    if row is None:
        raise HTTPException(404, detail={"code": "FileNotFound"})
    path = pathlib.Path(row["file_path"])
    if not path.exists():
        raise HTTPException(404, detail={"code": "FileGone"})
    mt = PREVIEW_TYPES.get(path.suffix.lower(), "text/plain")
    headers = {"Content-Security-Policy": PREVIEW_CSP, "X-Content-Type-Options": "nosniff"}
    return FileResponse(str(path), media_type=mt, headers=headers)


@router.delete("/files/{artifact_id}")
def delete_file(artifact_id: str, token: str | None = Auth,
                x_tenant_id: str | None = Header(default=None)):
    who = _require(token)
    with get_conn() as conn:
        from .api import _tenant_of
        tid = _tenant_of(conn, who, x_tenant_id)
        row = conn.execute(
            """SELECT a.id FROM artifacts a JOIN task_runs r ON r.id=a.run_id
               JOIN tasks t ON t.id=r.task_id
               WHERE a.id=? AND COALESCE(t.tenant_id,'TENANT-HQ')=?""",
            (artifact_id, tid)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "FileNotFound"})
        conn.execute("UPDATE artifacts SET state='deleted' WHERE id=?", (artifact_id,))
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="admin", action="file_deleted",
                    target=artifact_id, layer="system", payload={}, response={"by": who})
        conn.commit()
    return {"ok": True}


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
    _require(token)
    with get_conn() as conn:
        _ensure_unassigned(conn)
        conn.commit()
        rows = conn.execute(
            """SELECT e.id, e.name, e.type, e.parent_entity_id,
                      (SELECT COUNT(*) FROM ai_employees a WHERE a.legal_entity_id = e.id
                        AND COALESCE(a.status,'online') <> 'fired') AS headcount
               FROM legal_entities e WHERE COALESCE(e.state,'active')='active'
               ORDER BY CASE e.type WHEN 'group_hq' THEN 0 ELSE 1 END, e.name""").fetchall()
    return {"departments": [dict(r) for r in rows]}


@router.post("/departments")
def create_department(payload: DeptIn, token: str | None = Auth):
    ident = _require(token)
    import hashlib
    did = "LE-" + hashlib.sha1(payload.name.encode()).hexdigest()[:6].upper()
    with get_conn() as conn:
        conn.execute("""INSERT OR IGNORE INTO legal_entities
                        (id, name, type, parent_entity_id, governance_mode, pack_policy)
                        VALUES (?,?, 'department', ?, 'subsidiary', 'follow_group')""",
                     (did, payload.name, payload.entity_id))
        write_audit(conn, legal_entity_id=payload.entity_id, verb="dept", task_id=None,
                    layer="system", action="dept_created", target=did,
                    payload={"name": payload.name}, response={"by": ident})
        conn.commit()
    return {"ok": True, "department_id": did}


@router.patch("/departments/{dept_id}")
def rename_department(dept_id: str, payload: DeptPatch, token: str | None = Auth):
    ident = _require(token)
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM legal_entities WHERE id=?", (dept_id,)).fetchone() is None:
            raise HTTPException(404, detail={"code": "DeptNotFound"})
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
        row = conn.execute("SELECT * FROM legal_entities WHERE id=?", (dept_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "DeptNotFound"})
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
