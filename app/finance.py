"""V4.3 动态财务预算引擎。

固定额度（V4.2 的 2 万硬顶）只能防破产，不能防「大单被小额度卡死」。
这一层把预算做成**池 + 分配 + 弹性追加**：

1. 财务 Agent 在 DAG 规划之后给每个阶段估 base（按阶段类型/角色/需求复杂度），
   再按弹性系数（默认 1.5，即 50% 试错缓冲）算出该阶段初始额度；
2. 项目总预算池 = 各阶段初始额度之和（超过全局上限时等比缩放，安全锁）；
3. 阶段花到额度的 80% 强制暂停，抛 BudgetPause，由上层推「追加预算审批卡」；
   批准就从池子的**未花余额**里划拨给该阶段，驳回则阶段停在那里等人工；
4. 池子见底（未花余额 ≤ 0）时项目强制停摆（BudgetHalt）并通知老板。

所有额度都以 token 计。钱是结果，token 是因 —— 先把因管住。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid

log = logging.getLogger("finance")

ELASTIC_DEFAULT = 1.5          # 50% 试错缓冲
CAP_DEFAULT = 1_000_000        # 项目全局最高预算（安全锁）
PAUSE_RATIO_DEFAULT = 0.8      # 花到 80% 暂停并要钱
MIN_STAGE_QUOTA = 3000

DDL = """
CREATE TABLE IF NOT EXISTS project_budgets (
  project_id TEXT PRIMARY KEY,
  tenant_id TEXT,
  pool_tokens INTEGER NOT NULL,
  spent_tokens INTEGER NOT NULL DEFAULT 0,
  elastic REAL NOT NULL DEFAULT 1.5,
  cap_tokens INTEGER NOT NULL DEFAULT 1000000,
  capped INTEGER NOT NULL DEFAULT 0,          -- 池子被全局上限压缩过
  state TEXT NOT NULL DEFAULT 'active',       -- active | paused | halted | done
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stage_budgets (
  project_id TEXT NOT NULL,
  stage_key TEXT NOT NULL,
  title TEXT,
  role TEXT,
  base_estimate INTEGER NOT NULL,
  buffer_pct INTEGER NOT NULL DEFAULT 50,
  quota INTEGER NOT NULL,
  spent INTEGER NOT NULL DEFAULT 0,
  topups INTEGER NOT NULL DEFAULT 0,          -- 追加过几次
  state TEXT NOT NULL DEFAULT 'active',       -- active | awaiting_budget | done
  PRIMARY KEY (project_id, stage_key)
);

CREATE TABLE IF NOT EXISTS budget_approvals (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  stage_key TEXT NOT NULL,
  requested_tokens INTEGER NOT NULL,
  granted_tokens INTEGER NOT NULL DEFAULT 0,
  reason TEXT,
  state TEXT NOT NULL DEFAULT 'pending',      -- pending | approved | rejected | exhausted
  requested_by TEXT,
  decided_by TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  decided_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS budget_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT,
  stage_key TEXT,
  kind TEXT NOT NULL,                         -- estimate | pause | topup | approve | reject | halt
  tokens INTEGER,
  detail TEXT,
  actor TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 阶段类型 → 基础预估（token）。实测：一次资料检索 ≈ 2.5–3.6 万（含读页），
# 所以检索类 base 给 3 万；撰写/审核轻得多。数字不追求精确，追求「不卡死也不放羊」。
BASE_BY_KIND = {
    "retrieve": 30000, "research": 30000, "analyze": 12000, "author": 12000,
    "review": 6000, "deliver": 800, "spec": 8000, "design": 12000,
    "implement": 30000, "test": 15000, "deploy": 6000,
}
BASE_BY_ROLE = {
    "research": 30000, "data": 12000, "audit": 6000, "dev": 30000, "qa": 15000,
    "pm": 4000, "design": 12000, "platform": 800,
}
BASE_DEFAULT = 10000


class BudgetPause(Exception):
    """阶段花到 80%：暂停并要钱。approval_id 指到那张审批卡。"""

    def __init__(self, message: str, approval_id: str | None = None,
                 stage_key: str | None = None, tokens: int = 0):
        super().__init__(message)
        self.approval_id = approval_id
        self.stage_key = stage_key
        self.tokens = tokens


class BudgetHalt(Exception):
    """项目池见底：强制停摆。"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


# ------------------------------------------------------------------ 配置
def _setting(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return str(row["value"]) if row is not None and row["value"] not in (None, "") else None


def config(conn: sqlite3.Connection) -> dict:
    """弹性系数 / 全局上限 / 暂停比例：settings 表优先，其次环境变量，最后默认。"""
    def _f(key, env, default, lo, hi):
        raw = _setting(conn, key) or os.environ.get(env)
        try:
            val = float(raw) if raw not in (None, "") else float(default)
        except (TypeError, ValueError):
            val = float(default)
        return max(lo, min(hi, val))

    elastic = _f("budget_elastic", "M0_BUDGET_ELASTIC", ELASTIC_DEFAULT, 1.0, 5.0)
    cap = int(_f("project_budget_cap", "M0_PROJECT_BUDGET_CAP", CAP_DEFAULT,
                 50_000, 100_000_000_000))
    pause_ratio = _f("budget_pause_ratio", "M0_BUDGET_PAUSE_RATIO",
                     PAUSE_RATIO_DEFAULT, 0.5, 1.0)
    return {"elastic": elastic, "cap": cap, "pause_ratio": pause_ratio,
            "buffer_pct": round((elastic - 1) * 100)}


def set_config(conn: sqlite3.Connection, *, elastic: float | None = None,
               cap: int | None = None, pause_ratio: float | None = None) -> dict:
    pairs = {"budget_elastic": elastic, "project_budget_cap": cap,
             "budget_pause_ratio": pause_ratio}
    for key, val in pairs.items():
        if val is None:
            continue
        if key == "budget_elastic" and not 1.0 <= float(val) <= 5.0:
            raise ValueError("InvalidElastic")
        if key == "project_budget_cap" and not 50_000 <= int(val) <= 100_000_000_000:
            raise ValueError("InvalidProjectCap")
        if key == "budget_pause_ratio" and not 0.5 <= float(val) <= 1.0:
            raise ValueError("InvalidPauseRatio")
        conn.execute("INSERT INTO settings (key,value) VALUES (?,?)"
                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, str(val)))
    conn.commit()
    return config(conn)


# ------------------------------------------------------------------ 估算
def _base_for(node: dict) -> int:
    key = str(node.get("key") or "").split("_")[-1]          # an_retrieve → retrieve
    kind = str(node.get("kind") or "").lower()
    role = str(node.get("role") or "").lower()
    if kind in BASE_BY_KIND:
        return BASE_BY_KIND[kind]
    if key in BASE_BY_KIND:
        return BASE_BY_KIND[key]
    if role in BASE_BY_ROLE:
        return BASE_BY_ROLE[role]
    return BASE_DEFAULT


def _complexity(requirement: str, flow_id: str = "") -> float:
    n = len(requirement or "")
    factor = 0.9 if n < 20 else (1.0 if n < 80 else 1.15)
    if "URGENT" in (flow_id or ""):
        factor *= 1.2                                        # 救火要人，也要钱
    return factor


def estimate(nodes: list[dict], requirement: str = "", flow_id: str = "",
             elastic: float = ELASTIC_DEFAULT) -> list[dict]:
    """财务 Agent：逐阶段估 base，再按弹性系数算初始额度（含 50% 试错缓冲）。"""
    factor = _complexity(requirement, flow_id)
    out = []
    for node in nodes:
        base = int(round(_base_for(node) * factor / 500.0) * 500)
        quota = int(round(base * elastic / 500.0) * 500)
        out.append({"stage_key": node.get("key"), "title": node.get("title"),
                    "role": node.get("role"), "base_estimate": max(base, 1000),
                    "buffer_pct": round((elastic - 1) * 100),
                    "quota": max(quota, MIN_STAGE_QUOTA)})
    return out


# ------------------------------------------------------------------ 建池
def open_project(conn: sqlite3.Connection, pid: str, nodes: list[dict], *,
                 requirement: str = "", flow_id: str = "",
                 tenant_id: str | None = None) -> dict:
    """规划完就建池：写 project_budgets + stage_budgets，返回可展示的预算表。"""
    ensure(conn)
    cfg = config(conn)
    rows = estimate(nodes, requirement, flow_id, cfg["elastic"])
    pool = sum(r["quota"] for r in rows)
    capped = 0
    if pool > cfg["cap"]:                                    # 安全锁：等比缩放
        scale = cfg["cap"] / float(pool)
        for r in rows:
            r["quota"] = max(MIN_STAGE_QUOTA, int(r["quota"] * scale / 500.0) * 500)
        # 地板值（3k）可能把总量顶回上限之上 → 从最大的往下削，直到装进上限
        guard = 0
        while sum(r["quota"] for r in rows) > cfg["cap"] and guard < 4000:
            biggest = max(rows, key=lambda r: r["quota"])
            if biggest["quota"] <= MIN_STAGE_QUOTA:
                break
            biggest["quota"] -= 500
            guard += 1
        pool, capped = sum(r["quota"] for r in rows), 1
    conn.execute("INSERT INTO project_budgets (project_id,tenant_id,pool_tokens,elastic,"
                 "cap_tokens,capped,state,updated_at) VALUES (?,?,?,?,?,?,'active',"
                 "CURRENT_TIMESTAMP) ON CONFLICT(project_id) DO UPDATE SET"
                 " pool_tokens=excluded.pool_tokens, elastic=excluded.elastic,"
                 " capped=excluded.capped, updated_at=CURRENT_TIMESTAMP",
                 (pid, tenant_id, pool, cfg["elastic"], cfg["cap"], capped))
    for r in rows:
        conn.execute("INSERT INTO stage_budgets (project_id,stage_key,title,role,"
                     "base_estimate,buffer_pct,quota) VALUES (?,?,?,?,?,?,?)"
                     " ON CONFLICT(project_id,stage_key) DO UPDATE SET"
                     " base_estimate=excluded.base_estimate, quota=excluded.quota,"
                     " buffer_pct=excluded.buffer_pct",
                     (pid, r["stage_key"], r["title"], r["role"], r["base_estimate"],
                      r["buffer_pct"], r["quota"]))
    _event(conn, pid, None, "estimate", pool,
           json.dumps({"elastic": cfg["elastic"], "cap": cfg["cap"], "capped": capped,
                       "stages": rows}, ensure_ascii=False))
    conn.commit()
    return overview(conn, pid)


def _event(conn, pid, stage_key, kind, tokens=None, detail=None, actor=None) -> None:
    conn.execute("INSERT INTO budget_events (project_id,stage_key,kind,tokens,detail,actor)"
                 " VALUES (?,?,?,?,?,?)", (pid, stage_key, kind, tokens, detail, actor))


# ------------------------------------------------------------------ 读数
def overview(conn: sqlite3.Connection, pid: str) -> dict:
    try:
        ensure(conn)
    except sqlite3.Error:                # 只读接口不该因为 DDL 冲突而打不开
        pass
    head = conn.execute("SELECT * FROM project_budgets WHERE project_id=?", (pid,)).fetchone()
    rows = conn.execute("SELECT * FROM stage_budgets WHERE project_id=? ORDER BY rowid",
                        (pid,)).fetchall()
    stages = []
    for r in rows:
        quota = int(r["quota"] or 0)
        spent = int(r["spent"] or 0)
        stages.append({"key": r["stage_key"], "title": r["title"], "role": r["role"],
                       "base_estimate": int(r["base_estimate"] or 0),
                       "buffer_pct": int(r["buffer_pct"] or 0), "quota": quota,
                       "spent": spent, "pct": round(spent * 100.0 / quota, 1) if quota else 0.0,
                       "topups": int(r["topups"] or 0), "state": r["state"]})
    if head is None:
        return {"project_id": pid, "pool": 0, "spent": 0, "remaining": 0,
                "allocated": 0, "capped": 0, "state": "unknown", "stages": [],
                "elastic": config(conn)["elastic"]}
    pool = int(head["pool_tokens"] or 0)
    spent = int(head["spent_tokens"] or 0)
    return {"project_id": pid, "pool": pool, "spent": spent,
            "remaining": max(0, pool - spent), "allocated": sum(s["quota"] for s in stages),
            "elastic": float(head["elastic"] or ELASTIC_DEFAULT),
            "cap": int(head["cap_tokens"] or CAP_DEFAULT), "capped": int(head["capped"] or 0),
            "state": head["state"], "stages": stages,
            "pause_ratio": config(conn)["pause_ratio"]}


def quota(conn: sqlite3.Connection, pid: str, stage_key: str) -> dict | None:
    ensure(conn)
    row = conn.execute("SELECT * FROM stage_budgets WHERE project_id=? AND stage_key=?",
                       (pid, stage_key)).fetchone()
    if row is None:
        return None
    return {"quota": int(row["quota"] or 0), "spent": int(row["spent"] or 0),
            "topups": int(row["topups"] or 0), "state": row["state"],
            "role": row["role"], "title": row["title"]}


def pause_threshold(conn: sqlite3.Connection, pid: str, stage_key: str) -> int:
    """本次运行内允许花到的 token 数（超过就暂停要钱）。"""
    cfg, q = config(conn), quota(conn, pid, stage_key)
    if not q:
        return 0
    return max(1000, int(q["quota"] * cfg["pause_ratio"]) - q["spent"])


def spend(conn: sqlite3.Connection, pid: str, stage_key: str, tokens: int) -> dict:
    """记录阶段/项目的真实消耗（按 run 结束后回写）。"""
    tokens = max(0, int(tokens or 0))
    ensure(conn)
    conn.execute("UPDATE stage_budgets SET spent=spent+? WHERE project_id=? AND stage_key=?",
                 (tokens, pid, stage_key))
    conn.execute("UPDATE project_budgets SET spent_tokens=spent_tokens+?,"
                 " updated_at=CURRENT_TIMESTAMP WHERE project_id=?", (tokens, pid))
    conn.commit()
    return {"spent": tokens, **overview(conn, pid)}


def mark_stage(conn, pid: str, stage_key: str, state: str) -> None:
    conn.execute("UPDATE stage_budgets SET state=? WHERE project_id=? AND stage_key=?",
                 (state, pid, stage_key))
    conn.commit()


def set_state(conn, pid: str, state: str) -> None:
    conn.execute("UPDATE project_budgets SET state=?, updated_at=CURRENT_TIMESTAMP"
                 " WHERE project_id=?", (state, pid))
    conn.commit()


# ------------------------------------------------------------------ 追加 / 审批
def request_topup(conn: sqlite3.Connection, pid: str, stage_key: str, *, tokens: int | None = None,
                  reason: str = "", actor: str | None = None,
                  tenant_id: str | None = None) -> dict:
    """阶段花到 80%：开一张追加预算审批卡（同一阶段已有 pending 就复用）。"""
    ensure(conn)
    dup = conn.execute("SELECT * FROM budget_approvals WHERE project_id=? AND stage_key=?"
                       " AND state='pending'", (pid, stage_key)).fetchone()
    if dup is not None:
        return dict(dup)
    q = quota(conn, pid, stage_key) or {"quota": 0}
    amount = int(tokens or max(5000, round(q["quota"] * 0.5 / 1000.0) * 1000))
    aid = "BUD-" + uuid.uuid4().hex[:12]
    conn.execute("INSERT INTO budget_approvals (id,project_id,stage_key,requested_tokens,"
                 "reason,state,requested_by) VALUES (?,?,?,?,?,'pending',?)",
                 (aid, pid, stage_key, amount, reason or "阶段额度已用至 80%，申请追加",
                  actor))
    _event(conn, pid, stage_key, "pause", amount, reason, actor)
    conn.execute("UPDATE stage_budgets SET state='awaiting_budget'"
                 " WHERE project_id=? AND stage_key=?", (pid, stage_key))
    conn.execute("UPDATE project_budgets SET state='paused', updated_at=CURRENT_TIMESTAMP"
                 " WHERE project_id=? AND state='active'", (pid,))
    conn.commit()
    _notify(conn, pid, "budget_pause", f"阶段 {stage_key} 额度已用 80%，需老板批准追加 "
                                       f"{amount:,} tokens（审批 {aid}）", tenant_id)
    return {"id": aid, "project_id": pid, "stage_key": stage_key,
            "requested_tokens": amount, "state": "pending"}


def pending(conn: sqlite3.Connection, *, tenant_id: str | None = None,
            limit: int = 50) -> list[dict]:
    ensure(conn)
    sql = ("SELECT a.*, b.pool_tokens, b.spent_tokens FROM budget_approvals a"
           " LEFT JOIN project_budgets b ON b.project_id=a.project_id"
           " WHERE a.state='pending'")
    args: list = []
    if tenant_id:
        sql += " AND b.tenant_id=?"
        args.append(tenant_id)
    sql += " ORDER BY a.created_at DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    return [{"id": r["id"], "project_id": r["project_id"], "stage_key": r["stage_key"],
             "requested_tokens": r["requested_tokens"], "reason": r["reason"],
             "state": r["state"], "created_at": r["created_at"],
             "pool": int(r["pool_tokens"] or 0), "spent": int(r["spent_tokens"] or 0),
             "remaining": max(0, int(r["pool_tokens"] or 0) - int(r["spent_tokens"] or 0))}
            for r in rows]


def decide(conn: sqlite3.Connection, approval_id: str, *, approve: bool,
           actor: str | None = None, tokens: int | None = None,
           tenant_id: str | None = None) -> dict:
    """老板批准 → 从池子未花余额划拨给该阶段；池子见底 → 项目停摆。"""
    ensure(conn)
    row = conn.execute("SELECT * FROM budget_approvals WHERE id=?", (approval_id,)).fetchone()
    if row is None:
        raise KeyError("ApprovalNotFound")
    if row["state"] != "pending":
        raise ValueError("ApprovalAlreadyDecided")
    pid, key = row["project_id"], row["stage_key"]
    ov = overview(conn, pid)
    want = int(tokens or row["requested_tokens"])
    if not approve:
        conn.execute("UPDATE budget_approvals SET state='rejected',decided_by=?,"
                     " decided_at=CURRENT_TIMESTAMP WHERE id=?", (actor, approval_id))
        _event(conn, pid, key, "reject", want, "老板驳回追加", actor)
        conn.commit()
        _notify(conn, pid, "budget_rejected",
                f"阶段 {key} 的追加申请被驳回，项目暂停等你决定", tenant_id)
        return {"ok": True, "state": "rejected", "project_id": pid, "stage_key": key}

    remaining = ov["remaining"]
    if remaining <= 0:
        conn.execute("UPDATE budget_approvals SET state='exhausted',decided_by=?,"
                     " decided_at=CURRENT_TIMESTAMP WHERE id=?", (actor, approval_id))
        set_state(conn, pid, "halted")
        _event(conn, pid, key, "halt", 0, "项目池见底", actor)
        conn.commit()
        _notify(conn, pid, "budget_halted", "项目预算池已见底，项目强制停摆", tenant_id)
        return {"ok": False, "state": "exhausted", "project_id": pid,
                "reason": "项目预算池见底，项目已停摆"}

    grant = min(want, remaining)
    conn.execute("UPDATE stage_budgets SET quota=quota+?, topups=topups+1, state='active'"
                 " WHERE project_id=? AND stage_key=?", (grant, pid, key))
    conn.execute("UPDATE budget_approvals SET state='approved', granted_tokens=?,"
                 " decided_by=?, decided_at=CURRENT_TIMESTAMP WHERE id=?",
                 (grant, actor, approval_id))
    conn.execute("UPDATE project_budgets SET state='active', updated_at=CURRENT_TIMESTAMP"
                 " WHERE project_id=?", (pid,))
    _event(conn, pid, key, "approve", grant,
           json.dumps({"requested": want, "remaining_before": remaining}, ensure_ascii=False),
           actor)
    conn.commit()
    _notify(conn, pid, "budget_approved",
            f"老板批准为阶段 {key} 追加 {grant:,} tokens，剩余池 "
            f"{remaining - grant:,}", tenant_id)
    return {"ok": True, "state": "approved", "granted": grant, "project_id": pid,
            "stage_key": key, "budget": overview(conn, pid)}


def _notify(conn, pid: str, event: str, body: str, tenant_id: str | None) -> None:
    """企微群推；没配 webhook 就只在库里留一条 notifications。"""
    try:
        from . import notify
        notify.push_event(conn, pid=pid, event=event, body=body, tenant_id=tenant_id)
    except Exception as exc:                      # noqa: BLE001 - 通知不是闸门
        log.warning("budget notify failed: %s", exc)


def recent_projects(conn: sqlite3.Connection, *, limit: int = 30) -> list[dict]:
    """FinOps 面板用：最近开过预算池的项目一览。"""
    ensure(conn)
    rows = conn.execute(
        "SELECT b.project_id, b.pool_tokens, b.spent_tokens, b.state, b.elastic, b.capped,"
        "       p.name, p.state AS project_state, p.created_at"
        " FROM project_budgets b LEFT JOIN projects p ON p.id=b.project_id"
        " ORDER BY b.created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        pool, spent = int(r["pool_tokens"] or 0), int(r["spent_tokens"] or 0)
        out.append({"project_id": r["project_id"], "name": r["name"] or r["project_id"],
                    "pool": pool, "spent": spent, "remaining": max(0, pool - spent),
                    "state": r["state"], "project_state": r["project_state"],
                    "elastic": float(r["elastic"] or ELASTIC_DEFAULT),
                    "capped": int(r["capped"] or 0), "created_at": r["created_at"],
                    "pct": round(spent * 100.0 / pool, 1) if pool else 0.0})
    return out


def plan_table(ov: dict) -> str:
    """作战计划里的预算块（Markdown）。"""
    rows = ["", f"### 项目预算池 · {ov['pool']:,} tokens"
                f"（弹性系数 {ov['elastic']:g}，含 {round((ov['elastic'] - 1) * 100)}% 试错缓冲）"]
    if ov.get("capped"):
        rows.append(f"> 触发全局上限 {ov['cap']:,}，各阶段额度已等比压缩。")
    rows += ["", "| 阶段 | 角色 | 基础预估 | 初始额度 |", "|---|---|---|---|"]
    for s in ov["stages"]:
        rows.append(f"| {s['title']} | {s['role']} | {s['base_estimate']:,} | "
                    f"{s['quota']:,} |")
    rows += ["", f"阶段花到额度 80% 会自动暂停并推送追加审批卡；池子见底项目停摆。"]
    return "\n".join(rows)
