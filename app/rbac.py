"""RBAC + 高危工具审批 + 断点续跑（v2.3）。

resources        : 资源节点（部门目录 / 项目 / 文件）
agent_permissions: agent_id × resource_id → read|write|admin
tool_approvals   : 待审批请求；run_id / session_id 让「批准」能接回同一条会话

v2.3 的三条关键语义：
  1. 资源目录是**真实目录**（workspace/resources/<RES>），任务工作区里用
     `shared/` junction 挂进来，Agent 的写操作会带绝对/相对路径 —— 运行时按
     最长前缀命中资源，再按 read/write 判权。
  2. 越权不是「静默失败」：拦下来的那一刻生成 AP 单，并同时记录 run_id、
     session_id、被拦工具与目标；Run 停在 awaiting_approval，任务状态不动。
  3. 批准后由调用方用同一个 run_id + session_id 走 ACP `session/resume`，
     prompt 只带「继续刚才那一步」，不重放前文 —— 省 Token，也从断点继续。
"""
from __future__ import annotations

import sqlite3

import json
import os
import pathlib
import secrets
import threading

from .audit import write_audit

DDL = """
CREATE TABLE IF NOT EXISTS resources (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,              -- department | project | file
  name TEXT NOT NULL,
  path TEXT,
  parent_id TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_permissions (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  permission TEXT NOT NULL,        -- read | write | admin
  granted_by TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(agent_id, resource_id, permission)
);

CREATE TABLE IF NOT EXISTS tool_approvals (
  id TEXT PRIMARY KEY,
  agent_id TEXT,
  tool TEXT NOT NULL,
  args TEXT,
  state TEXT DEFAULT 'pending',    -- pending | approved | rejected
  tenant_id TEXT DEFAULT 'TENANT-HQ',
  reason TEXT,
  decided_by TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  decided_at TIMESTAMP
);
"""

# v2.3 增量列：老库直接 ALTER，不重建表
COLUMNS = {
    "run_id": "TEXT",
    "task_id": "TEXT",
    "tenant_id": "TEXT DEFAULT 'TENANT-HQ'",
    "chat_id": "TEXT",
    "session_id": "TEXT",
    "resource_id": "TEXT",
    "resume_prompt": "TEXT",
    "risk": "TEXT DEFAULT 'high'",
    "target": "TEXT",
    "pushed_at": "TIMESTAMP",
    "push_channel": "TEXT",
    "push_result": "TEXT",
    "consumed_at": "TIMESTAMP",
    "applied_at": "TIMESTAMP",
    "applied_path": "TEXT",
    "applied_sha256": "TEXT",
    "applied_bytes": "INTEGER DEFAULT 0",
    "parked_at_ms": "INTEGER DEFAULT 0",
    "decided_at_ms": "INTEGER DEFAULT 0",
}

LEVELS = {"read": 1, "write": 2, "admin": 3}
HIGH_RISK = {"db_exec", "file_delete", "shell_exec"}

WRITE_TOOLS = {"write", "edit", "write_file", "create_file", "apply_patch", "patch",
               "delete", "move", "remove"}
READ_TOOLS = {"read", "read_file", "view", "cat", "glob", "grep", "list", "ls"}


def ensure(conn) -> None:
    conn.executescript(DDL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tool_approvals)")}
    for name, decl in COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE tool_approvals ADD COLUMN {name} {decl}")
    try:
        conn.execute("""UPDATE tool_approvals SET tenant_id = COALESCE(
                          (SELECT t.tenant_id FROM tasks t WHERE t.id = tool_approvals.task_id),
                          tenant_id, 'TENANT-HQ')
                        WHERE COALESCE(tenant_id,'') = '' OR tenant_id IS NULL""")
    except sqlite3.OperationalError:
        pass
    conn.commit()


# ------------------------------------------------------------------ 资源/授权
def upsert_resource(conn, *, rid: str, kind: str, name: str, path: str | None = None,
                    parent_id: str | None = None) -> str:
    ensure(conn)
    conn.execute("""INSERT INTO resources (id, kind, name, path, parent_id)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, name=excluded.name,
                        path=excluded.path, parent_id=excluded.parent_id""",
                 (rid, kind, name, path, parent_id))
    conn.commit()
    return rid


def grant(conn, agent_id: str, resource_id: str, permission: str,
          granted_by: str = "system") -> None:
    ensure(conn)
    if permission not in LEVELS:
        raise ValueError("bad permission")
    cur = conn.execute("""INSERT OR IGNORE INTO agent_permissions
                    (id, agent_id, resource_id, permission, granted_by)
                    VALUES (?,?,?,?,?)""",
                 ("PM-" + secrets.token_hex(6), agent_id, resource_id, permission, granted_by))
    conn.commit()
    if not cur.rowcount:
        return                       # 幂等 seed 不该每次刷审计
    write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="rbac", task_id=None,
                layer="system", action="permission_granted", target=resource_id,
                payload={"agent": agent_id, "permission": permission}, response={})
    conn.commit()


def revoke(conn, agent_id: str, resource_id: str, permission: str | None = None) -> int:
    ensure(conn)
    if permission:
        cur = conn.execute("DELETE FROM agent_permissions WHERE agent_id=? AND resource_id=?"
                           " AND permission=?", (agent_id, resource_id, permission))
    else:
        cur = conn.execute("DELETE FROM agent_permissions WHERE agent_id=? AND resource_id=?",
                           (agent_id, resource_id))
    conn.commit()
    return int(cur.rowcount or 0)


def has(conn, agent_id: str, resource_id: str, need: str) -> bool:
    ensure(conn)
    rows = conn.execute(
        "SELECT permission FROM agent_permissions WHERE agent_id=? AND resource_id=?",
        (agent_id, resource_id)).fetchall()
    mine = max((LEVELS.get(r["permission"], 0) for r in rows), default=0)
    return mine >= LEVELS.get(need, 99)


def require(conn, agent_id: str, resource_id: str, need: str = "read",
            *, entity: str = "LE-GROUP-HQ") -> None:
    """越权即抛错 + 审计。"""
    if has(conn, agent_id, resource_id, need):
        return
    write_audit(conn, legal_entity_id=entity, verb="rbac", task_id=None, layer="system",
                action="permission_denied", target=resource_id,
                payload={"agent": agent_id, "need": need}, response={"ok": False})
    conn.commit()
    raise PermissionError(f"PermissionDenied: {agent_id} -> {resource_id} ({need})")


def resource_for_path(conn, path: str | None, *, base: pathlib.Path | None = None):
    """最长前缀命中：`.../RES-FIN/a/b.md` → RES-FIN；不在任何资源下 → None。

    junction（任务工作区里的 shared/）会被 realpath 还原成真实目录，所以
    Agent 用相对路径写 `shared/RES-FIN/x.md` 也算命中。
    """
    if not path:
        return None
    ensure(conn)
    p = pathlib.Path(str(path))
    if not p.is_absolute() and base is not None:
        p = pathlib.Path(base) / p
    try:
        real = os.path.normcase(os.path.realpath(str(p)))
    except OSError:
        real = os.path.normcase(str(p))
    best = None
    for row in conn.execute("SELECT * FROM resources WHERE path IS NOT NULL AND path<>''"):
        try:
            root = os.path.normcase(os.path.realpath(str(row["path"])))
        except OSError:
            root = os.path.normcase(str(row["path"]))
        if real == root or real.startswith(root.rstrip("\\/") + os.sep):
            if best is None or len(root) > len(str(best["path"])):
                best = row
    return best


def action_for_tool(tool: str) -> str | None:
    t = (tool or "").lower()
    if t in WRITE_TOOLS:
        return "write"
    if t in READ_TOOLS:
        return "read"
    return None


# ------------------------------------------------------------------ 高危工具审批
def guard_tool(conn, *, agent_id: str, tool: str, args: dict | None = None,
               entity: str = "LE-GROUP-HQ", run_id: str | None = None,
               task_id: str | None = None, session_id: str | None = None,
               chat_id: str | None = None, resource_id: str | None = None,
               target: str | None = None, reason: str = "high-risk tool",
               resume_prompt: str | None = None, risk: str = "high",
               push: bool = True, force: bool = False) -> dict:
    """生成审批单并（异步）推送。返回 approval_id，调用方负责挂起 Run。

    普通工具直接放行；只有 HIGH_RISK 或资源越权（force=True）才建单。
    """
    ensure(conn)
    if not force and tool not in HIGH_RISK:
        return {"needs_approval": False}
    aid = "AP-" + secrets.token_hex(6)
    trow = conn.execute("SELECT tenant_id FROM tasks WHERE id=?", (task_id,)).fetchone() if task_id else None
    ap_tenant = (trow["tenant_id"] if trow and "tenant_id" in trow.keys() else None) or "TENANT-HQ"
    conn.execute(
        """INSERT INTO tool_approvals
             (id, agent_id, tool, args, state, tenant_id, reason, run_id, task_id, chat_id,
              session_id, resource_id, resume_prompt, risk, target)
           VALUES (?,?,?,?, 'pending', ?,?,?,?,?,?,?,?,?,?)""",
        (aid, agent_id, tool, json.dumps(args or {}, ensure_ascii=False), ap_tenant, reason,
         run_id, task_id, chat_id, session_id, resource_id, resume_prompt, risk, target))
    conn.commit()
    write_audit(conn, legal_entity_id=entity, verb="rbac", task_id=task_id, run_id=run_id,
                layer="system", action="approval_requested", target=aid,
                payload={"agent": agent_id, "tool": tool, "args": args or {},
                         "resource_id": resource_id, "target": target,
                         "session_id": session_id},
                response={"ok": True, "run_id": run_id})
    conn.commit()
    if push and (run_id or chat_id):
        threading.Thread(target=push_approval, args=(aid,), daemon=True).start()
    return {"needs_approval": True, "approval_id": aid, "state": "pending",
            "run_id": run_id, "session_id": session_id, "resource_id": resource_id}


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def _enriched(conn, where: str, args: tuple = (), suffix: str = "") -> list:
    """跨表补名字；单测库可能只有 rbac 三张表，缺表就退化成裸行。"""
    cols = "a.*"
    joins = ""
    if _has_table(conn, "tasks"):
        joins += " LEFT JOIN tasks t ON t.id = a.task_id"
        cols += ", t.title AS task_title"
    if _has_table(conn, "ai_employees"):
        joins += " LEFT JOIN ai_employees e ON e.id = a.agent_id"
        cols += ", e.name AS agent_name"
    if _has_table(conn, "chats"):
        joins += " LEFT JOIN chats c ON c.id = a.chat_id"
        cols += ", c.title AS chat_title"
    if _has_table(conn, "resources"):
        joins += " LEFT JOIN resources r ON r.id = a.resource_id"
        cols += ", r.name AS resource_name"
    return conn.execute(f"SELECT {cols} FROM tool_approvals a{joins} WHERE {where}"
                        f" ORDER BY a.rowid DESC{suffix}", args).fetchall()


def get(conn, approval_id: str):
    ensure(conn)
    rows = _enriched(conn, "a.id = ?", (approval_id,))
    return rows[0] if rows else None


def decide(conn, approval_id: str, approve: bool, by: str = "boss") -> dict:
    ensure(conn)
    row = conn.execute("SELECT * FROM tool_approvals WHERE id=?", (approval_id,)).fetchone()
    if row is None:
        raise ValueError("approval not found")
    state = "approved" if approve else "rejected"
    conn.execute("UPDATE tool_approvals SET state=?, decided_by=?, decided_at=CURRENT_TIMESTAMP"
                 " WHERE id=?", (state, by, approval_id))
    write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="rbac", task_id=row["task_id"],
                run_id=row["run_id"], layer="system",
                action="approval_" + state, target=approval_id,
                payload={"tool": row["tool"], "agent": row["agent_id"],
                         "resource_id": row["resource_id"], "session_id": row["session_id"]},
                response={"by": by, "resume": bool(row["run_id"] and row["session_id"])})
    conn.commit()
    return {"ok": True, "state": state, "approval_id": approval_id,
            "run_id": row["run_id"], "session_id": row["session_id"],
            "chat_id": row["chat_id"], "task_id": row["task_id"],
            "resource_id": row["resource_id"],
            "resumable": bool(row["run_id"] and row["session_id"])}


def consume(conn, approval_id: str) -> None:
    """一次性放行：审批只换一次工具调用，不能当成长期权限。"""
    conn.execute("UPDATE tool_approvals SET consumed_at=CURRENT_TIMESTAMP WHERE id=?",
                 (approval_id,))
    conn.commit()


def approved_for(conn, run_id: str, resource_id: str, tool: str):
    ensure(conn)
    return conn.execute(
        """SELECT * FROM tool_approvals
           WHERE run_id=? AND resource_id=? AND state='approved'
             AND consumed_at IS NULL
           ORDER BY rowid DESC LIMIT 1""", (run_id, resource_id)).fetchone()


def approved_target(conn, run_id: str, resource_id: str, tool: str,
                    target: str | None, base=None):
    """同一 run 内、同一个已由平台落盘的目标：读回/重试写同一文件不再二次审批。"""
    if not target:
        return None
    p = pathlib.Path(str(target))
    if not p.is_absolute() and base is not None:
        p = pathlib.Path(base) / p
    try:
        real = os.path.normcase(os.path.realpath(str(p)))
    except OSError:
        real = os.path.normcase(str(p))
    for row in conn.execute(
            """SELECT * FROM tool_approvals WHERE run_id=? AND resource_id=?
               AND state='approved' AND applied_at IS NOT NULL""",
            (run_id, resource_id)):
        applied = row["applied_path"] or ""
        if applied and os.path.normcase(os.path.realpath(applied)) == real:
            return row
    return None


# ------------------------------------------------------------------ 代执行
def apply_approval(conn, approval_id: str) -> dict:
    """审批通过后，由平台**代执行**被拦下的那一次写操作。

    为什么不放开 Agent 的目录写权限：DSH 的 workspace-write 与 Low 沙箱都把
    「任务目录之外」当禁区，给 Agent 单独开洞等于扩大攻击面。平台按审批单里的
    rawInput 逐字节落盘，路径再校验一次必须命中已批准的资源，然后审计 sha256。
    Agent 通过 session/resume 收到「已代执行」的结果继续，不需要重跑。
    """
    import hashlib

    row = get(conn, approval_id)
    if row is None or row["state"] != "approved":
        return {"applied": False, "reason": "not approved"}
    if row["applied_at"]:
        return {"applied": True, "path": row["applied_path"], "sha256": row["applied_sha256"],
                "bytes": row["applied_bytes"], "already": True}
    if (row["tool"] or "").lower() not in WRITE_TOOLS:
        return {"applied": False, "reason": "tool is not a write"}
    try:
        args = json.loads(row["args"] or "{}")
    except (ValueError, TypeError):
        args = {}
    content = args.get("content")
    target = args.get("file_path") or args.get("path") or row["target"]
    if content is None or not target:
        return {"applied": False, "reason": "no content in rawInput"}

    res = conn.execute("SELECT * FROM resources WHERE id=?", (row["resource_id"],)).fetchone()
    if res is None or not res["path"]:
        return {"applied": False, "reason": "resource missing"}
    from .engine import dsh_adapter
    run = conn.execute("SELECT * FROM task_runs WHERE id=?", (row["run_id"],)).fetchone()
    actor = (run["actor_identity"] if run else None) or "system"
    ws = dsh_adapter.task_workspace(row["task_id"], actor)

    p = pathlib.Path(str(target))
    if not p.is_absolute():
        p = ws / p
    real = pathlib.Path(os.path.realpath(str(p)))
    root = pathlib.Path(os.path.realpath(str(res["path"])))
    if real != root and root not in real.parents:
        raise ValueError(f"approved target escapes resource: {real}")

    data = str(content).encode("utf-8")
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("approved payload too large (>2MB)")
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    conn.execute(
        """UPDATE tool_approvals SET consumed_at=CURRENT_TIMESTAMP, applied_at=CURRENT_TIMESTAMP,
             applied_path=?, applied_sha256=?, applied_bytes=? WHERE id=?""",
        (str(real), sha, len(data), approval_id))
    write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="rbac", task_id=row["task_id"],
                run_id=row["run_id"], layer="system", action="approval_applied",
                target=approval_id,
                payload={"path": str(real), "bytes": len(data), "sha256": sha,
                         "resource_id": row["resource_id"], "tool": row["tool"]},
                response={"ok": True})
    conn.commit()
    return {"applied": True, "path": str(real), "sha256": sha, "bytes": len(data)}


def pending(conn) -> list[dict]:
    ensure(conn)
    return [dict(r) for r in _enriched(conn, "a.state='pending'")]


def history(conn, limit: int = 50) -> list[dict]:
    ensure(conn)
    return [dict(r) for r in _enriched(conn, "a.state <> 'pending'", (),
                                       suffix=" LIMIT %d" % int(limit))]


# ------------------------------------------------------------------ 运行时闸门
def gate_tool_call(conn, *, agent_id: str | None, tool: str, update: dict,
                   run_id: str, task_id: str, session_id: str | None,
                   chat_id: str | None = None, workspace=None,
                   entity: str = "LE-GROUP-HQ") -> dict:
    """ACP tool_call 通知进来时调用。返回：

    blocked=True  -> 已生成审批单，调用方立刻 cancel 并挂起 Run
    in_resource=True -> 目标在受管资源里且有权（或普通工具），调用方跳过 workspace 策略
    """
    inputs = update.get("rawInput") or {}
    target = inputs.get("file_path") or inputs.get("path")
    action = action_for_tool(tool)
    res = resource_for_path(conn, target, base=workspace) if target else None

    if res is None:
        # 高危工具按名字拦截（默认集合为空 —— 不误伤正常 pwsh/写文件）
        if tool in high_risk_enabled():
            aid = guard_tool(conn, agent_id=agent_id or "system", tool=tool, args=inputs,
                             entity=entity, run_id=run_id, task_id=task_id,
                             session_id=session_id, chat_id=chat_id, target=target,
                             reason="high-risk tool", risk="high")
            return {"blocked": True, "in_resource": False, "tool": tool, "target": target,
                    "approval_id": aid["approval_id"], "reason": "high_risk"}
        return {"blocked": False, "in_resource": False, "tool": tool, "target": target}

    if action is None or agent_id is None:
        return {"blocked": False, "in_resource": True, "resource_id": res["id"],
                "resource_name": res["name"], "need": action, "tool": tool, "target": target}

    if has(conn, agent_id, res["id"], action):
        return {"blocked": False, "in_resource": True, "resource_id": res["id"],
                "resource_name": res["name"], "need": action, "granted": True,
                "tool": tool, "target": target}

    # 平台已经按审批单代执行过同一个目标 → 同 run 内允许重试，不再刷第二张单
    reuse = approved_target(conn, run_id, res["id"], tool, target, base=workspace)
    if reuse is not None:
        write_audit(conn, legal_entity_id=entity, verb="rbac", task_id=task_id, run_id=run_id,
                    layer="system", action="approval_reused", target=reuse["id"],
                    payload={"agent": agent_id, "resource": res["id"], "tool": tool,
                             "path": target},
                    response={"ok": True})
        conn.commit()
        return {"blocked": False, "in_resource": True, "resource_id": res["id"],
                "resource_name": res["name"], "need": action, "reused": True,
                "approval_id": reuse["id"], "tool": tool, "target": target}

    # 有「已批准但未消费」的单 → 一次性放行（断点续跑会重试同一个工具）
    row = approved_for(conn, run_id, res["id"], tool)
    if row is not None:
        consume(conn, row["id"])
        write_audit(conn, legal_entity_id=entity, verb="rbac", task_id=task_id, run_id=run_id,
                    layer="system", action="approval_consumed", target=row["id"],
                    payload={"agent": agent_id, "resource": res["id"], "tool": tool},
                    response={"ok": True})
        conn.commit()
        return {"blocked": False, "in_resource": True, "resource_id": res["id"],
                "resource_name": res["name"], "need": action, "approved_by": row["decided_by"],
                "approval_id": row["id"], "tool": tool, "target": target}

    write_audit(conn, legal_entity_id=entity, verb="rbac", task_id=task_id, run_id=run_id,
                layer="system", action="permission_denied", target=res["id"],
                payload={"agent": agent_id, "need": action, "tool": tool, "path": target},
                response={"ok": False, "parked": True})
    conn.commit()
    out = guard_tool(
        conn, agent_id=agent_id, tool=tool, args=inputs, entity=entity, run_id=run_id,
        task_id=task_id, session_id=session_id, chat_id=chat_id, resource_id=res["id"],
        target=str(target), reason=f"resource {action} denied",
        resume_prompt=(f"审批已通过（{{approval_id}}）。允许对资源 {res['id']} 执行 {action}；"
                       f"请重试刚才被拦截的一步（目标：{target}），不要重做已完成部分，"
                       f"完成后只回复 DONE。"),
        risk="resource", force=True)
    return {"blocked": True, "in_resource": True, "resource_id": res["id"],
            "resource_name": res["name"], "need": action, "tool": tool,
            "target": target, "approval_id": out["approval_id"], "reason": "resource_denied"}


def high_risk_enabled() -> set[str]:
    raw = os.environ.get("M0_APPROVAL_TOOLS", "")
    return {t.strip().lower() for t in raw.split(",") if t.strip()}


# ------------------------------------------------------------------ 推送
def push_approval(approval_id: str) -> dict:
    """独立线程：发企微文本，失败只记录，不影响拦截本身。"""
    from .db import connect

    with connect() as conn:
        row = get(conn, approval_id)
        if row is None:
            return {"ok": False, "error": "not found"}
        if row["pushed_at"]:
            return {"ok": True, "skipped": True}
        channel = "wecom" if os.environ.get("WECOM_SECRET") else "inbox"
        text = (
            "【Agent OS 审批】越权/高危操作待批\n"
            f"单号：{row['id']}\n"
            f"员工：{row['agent_name'] or row['agent_id']}\n"
            f"动作：{row['tool']} → {row['resource_name'] or row['target'] or '-'}\n"
            f"目标：{row['target'] or '-'}\n"
            f"Run：{row['run_id'] or '-'}（已挂起，批准后断点续跑）\n"
            f"任务：{row['task_title'] or row['task_id'] or '-'}\n"
            "处理：打开 Agent OS →「审批」页，允许或拒绝。"
        )
        to = os.environ.get("WECOM_APPROVAL_APPROVER", "").strip()
        result = "inbox-only"
        ok = True
        err = ""
        if channel == "wecom" and to:
            try:
                from .wecom.client import WeComClient
                client = WeComClient(conn_factory=connect)
                result = client.send_text(to, text)
            except Exception as exc:                 # noqa: BLE001
                ok, err = False, str(exc)[:300]
                result = "push failed: " + err
        conn.execute(
            "UPDATE tool_approvals SET pushed_at=CURRENT_TIMESTAMP, push_channel=?,"
            " push_result=? WHERE id=?", (channel if to else "inbox", result, approval_id))
        conn.commit()
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="rbac", task_id=row["task_id"],
                    run_id=row["run_id"], layer="system",
                    action="approval_pushed" if ok else "approval_push_failed",
                    target=approval_id, payload={"channel": channel, "to": to},
                    response={"ok": ok, "result": result})
        conn.commit()
        return {"ok": ok, "channel": channel, "result": result, "error": err}
