"""组织经验（v2.4）：把「被驳回的意见」和「研讨会共识」沉淀成可复用记忆。

三层记忆：
  1. 个人偏好   user_preferences（老板称呼/口味，原有）
  2. 项目记忆   memory_entries（scope = entity/pack/global，原有）
  3. 组织经验   org_experiences（本模块：驳回原因、共识结论、决策否决）

注入点：`pipeline.run_project` 在立项时按需求标签检索经验，把命中条目追加到
requirement 后面（写进立项书/纪要，审计 action=experience_injected）。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from .audit import new_id, write_audit

DDL = """
CREATE TABLE IF NOT EXISTS org_experiences (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,              -- rejection | consensus | decision
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  tags TEXT,
  source_type TEXT,
  source_id TEXT,
  project_id TEXT,
  task_id TEXT,
  legal_entity_id TEXT DEFAULT 'LE-GROUP-HQ',
  tenant_id TEXT NOT NULL DEFAULT 'TENANT-HQ',
  weight REAL DEFAULT 1.0,
  uses INTEGER DEFAULT 0,
  mandatory INTEGER NOT NULL DEFAULT 0,
  redline_terms TEXT,
  state TEXT NOT NULL DEFAULT 'pending',
  version INTEGER NOT NULL DEFAULT 1,
  reviewed_by TEXT,
  reviewed_at TIMESTAMP,
  review_note TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_exp_kind ON org_experiences(kind);
CREATE INDEX IF NOT EXISTS idx_exp_project ON org_experiences(project_id);
CREATE TABLE IF NOT EXISTS experience_reviews (
  id TEXT PRIMARY KEY,
  experience_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  legal_entity_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  action TEXT NOT NULL,
  actor TEXT NOT NULL,
  snapshot TEXT NOT NULL,
  note TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

KIND_LABEL = {"rejection": "驳回经验", "consensus": "共识结论", "decision": "决策否决"}


class ReviewConflict(RuntimeError):
    """A reviewer must reload content changed or decided since their page loaded."""


def ensure(conn) -> None:
    caller_transaction = conn.in_transaction
    statement = ""
    for line in DDL.splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(org_experiences)")}
    additions = {
        "tenant_id": "TEXT NOT NULL DEFAULT 'TENANT-HQ'",
        "mandatory": "INTEGER NOT NULL DEFAULT 0",
        "redline_terms": "TEXT", "state": "TEXT NOT NULL DEFAULT 'pending'",
        "version": "INTEGER NOT NULL DEFAULT 1", "reviewed_by": "TEXT",
        "reviewed_at": "TIMESTAMP", "review_note": "TEXT",
    }
    for name, ddl in additions.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE org_experiences ADD COLUMN {name} {ddl}")
    # Only the one-time migration may correct the default assigned to old rows.
    if "tenant_id" not in cols:
        conn.execute("""UPDATE org_experiences SET tenant_id=COALESCE(
            (SELECT tenant_id FROM legal_entities WHERE id=legal_entity_id),'TENANT-HQ')""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_scope ON org_experiences"
                 "(tenant_id,legal_entity_id,state)")
    if not caller_transaction:
        conn.commit()


def _scope(conn, entity: str | None, tenant_id: str | None) -> tuple[str, str]:
    from . import tenancy
    entity = entity or "LE-GROUP-HQ"
    actual = tenancy.of_entity(conn, entity)
    if tenant_id is not None and tenant_id != actual:
        raise PermissionError("TenantMismatch")
    return entity, actual


def _reviewer(conn, actor: str | dict | None, entity: str, tenant_id: str) -> dict:
    from . import identity, tenancy
    actor_id = actor.get("id") if isinstance(actor, dict) else actor
    row = identity.get_identity(conn, actor_id or "")
    if row is None or row["role"] not in {"ceo", "system"}:
        raise PermissionError("ExperienceReviewerRequired")
    if tenancy.of_identity(conn, row["id"]) != tenant_id:
        raise PermissionError("TenantMismatch")
    if row["legal_entity_id"] != entity:
        raise PermissionError("CrossEntityForbidden")
    return dict(row)


def _history(conn, row, action: str, actor: str, note: str = "") -> None:
    import json
    conn.execute("""INSERT INTO experience_reviews
        (id,experience_id,tenant_id,legal_entity_id,version,action,actor,snapshot,note)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (new_id("ER"), row["id"], row["tenant_id"], row["legal_entity_id"],
         row["version"], action, actor, json.dumps(dict(row), ensure_ascii=False), note[:500]))


def _tags(text: str) -> list[str]:
    try:
        from .ui.roles import tags_of
        tags = list(tags_of(text or ""))
    except Exception:                     # noqa: BLE001
        tags = []
    # 补几个高频业务词，标签库太窄时也能命中
    for word in ("小程序", "记账", "财务", "报销", "审批", "计算器", "落地页", "文案",
                 "数据", "报表", "接口", "测试", "重构", "安全"):
        if word in (text or "") and word not in tags:
            tags.append(word)
    return tags[:8]


def add(conn, *, kind: str, title: str, content: str, tags: list[str] | None = None,
        source_type: str = "", source_id: str = "", project_id: str | None = None,
        task_id: str | None = None, entity: str = "LE-GROUP-HQ",
        weight: float = 1.0, mandatory: bool = False,
        redline_terms: str | list[str] | None = None,
        tenant_id: str | None = None, actor: str | dict | None = None) -> dict:
    ensure(conn)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    if mandatory:
        _reviewer(conn, actor, entity, tenant_id)
    existing = conn.execute(
        """SELECT * FROM org_experiences WHERE kind=? AND source_id=? AND title=?
           AND tenant_id=? AND legal_entity_id=? LIMIT 1""",
        (kind, source_id, title, tenant_id, entity)).fetchone()
    if existing is not None:
        return dict(existing)
    eid = new_id("EXP")
    tags = tags if tags is not None else _tags(title + " " + content)
    if isinstance(tags, str):
        tags = [x.strip() for x in tags.replace("，", ",").split(",") if x.strip()]
    if isinstance(redline_terms, list):
        redline_terms = ",".join(redline_terms)
    conn.execute(
        """INSERT INTO org_experiences
             (id, kind, title, content, tags, source_type, source_id, project_id,
              task_id, legal_entity_id, weight, tenant_id, mandatory, redline_terms, state)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending')""",
        (eid, kind, title[:160], content[:4000], ",".join(tags), source_type, source_id,
         project_id, task_id, entity, weight, tenant_id, int(mandatory),
         (redline_terms or "").strip()[:2000]))
    write_audit(conn, legal_entity_id=entity, verb="memory_write", task_id=task_id,
                layer="system", action="experience_added", target=eid,
                payload={"kind": kind, "tags": tags, "source": source_id,
                         "title": title[:120]},
                response={"ok": True})
    conn.commit()
    return dict(conn.execute("SELECT * FROM org_experiences WHERE id=?", (eid,)).fetchone())


def capture_rejection(conn, task_id: str, *, verdict: str = "rejected") -> dict | None:
    """任务被驳回 / 判 failed 时沉淀：哪几条判据没过、为什么。"""
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if task is None:
        return None
    failed = conn.execute(
        """SELECT criterion, note FROM acceptance_criteria
           WHERE task_id=? AND state='failed' ORDER BY seq""", (task_id,)).fetchall()
    if not failed:
        return None
    reasons = "；".join(f"{f['criterion']}（{f['note'] or '未达标'}）" for f in failed)
    content = (f"任务《{task['title']}》被{ '驳回' if verdict == 'rejected' else '判失败' }。"
               f"未通过判据：{reasons}。下次同类任务先按这些判据自检，再提交。")
    return add(conn, kind="rejection", title=f"驳回：{task['title']}",
               content=content, tags=_tags(task["title"] + " " + reasons),
               source_type="task", source_id=task_id, task_id=task_id,
               entity=task["legal_entity_id"], weight=1.2)


def capture_consensus(conn, *, project_id: str, workshop_id: str, requirement: str,
                      statements: list[dict], minutes: str | None = None) -> dict | None:
    """研讨会收尾：把风险意见与表决结论沉淀成一条共识。"""
    if not statements:
        return None
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    name = project["name"] if project else project_id
    risks = "；".join(f"{s.get('title', s.get('role', ''))}：{str(s.get('risk', ''))[:90]}"
                     for s in statements if s.get("risk"))
    votes = "；".join(f"{s.get('title', s.get('role', ''))}：{s.get('stance', '同意')}"
                     for s in statements)
    content = (f"项目《{name}》研讨会共识：需求「{requirement[:160]}」。"
               f"风险：{risks[:500]}。表决：{votes[:300]}。")
    return add(conn, kind="consensus", title=f"共识：{name}",
               content=content, tags=_tags(requirement + " " + name),
               source_type="workshop", source_id=workshop_id, project_id=project_id,
               entity=project["legal_entity_id"] if project else "LE-GROUP-HQ",
               weight=1.0)


def capture_decision_rejection(conn, decision_id: str, note: str | None = None) -> dict | None:
    row = conn.execute("SELECT * FROM decision_packages WHERE id=?", (decision_id,)).fetchone()
    if row is None:
        return None
    content = (f"决策包《{row['title']}》被否决。摘要：{row['summary'] or '-'}。"
               f"否决意见：{note or row['decision_note'] or '未填写'}。"
               "同类决策下次先补齐收益/风险/回滚证据。")
    return add(conn, kind="decision", title=f"否决：{row['title']}",
               content=content, tags=_tags(row["title"] + " " + (row["summary"] or "")),
               source_type="decision", source_id=decision_id,
               project_id=None, task_id=row["task_id"],
               entity=row["legal_entity_id"], weight=1.3)


def _grams(text: str) -> list[str]:
    out = re.findall(r"[\u4e00-\u9fa5]{2,4}|[A-Za-z][A-Za-z0-9_]{1,20}", text or "")
    return list(dict.fromkeys(out))[:60]


def context_for(conn, text: str, *, entity: str | None = None, limit: int = 3,
                record: bool = True, tenant_id: str | None = None) -> dict:
    """按标签 + 词面重合度召回经验；返回可直接拼进 prompt 的文本与 ids。"""
    ensure(conn)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    tags = set(_tags(text))
    grams = _grams(text)
    rows = conn.execute(
        """SELECT * FROM org_experiences WHERE state='approved'
           AND tenant_id=? AND legal_entity_id=? AND (weight>0 OR mandatory=1)
           ORDER BY mandatory DESC, rowid DESC""", (tenant_id, entity)).fetchall()
    scored = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in rows:
        if row["mandatory"]:
            scored.append((float("inf"), row))
            continue
        row_tags = set((row["tags"] or "").split(",")) - {""}
        score = 3.0 * len(tags & row_tags)
        hay = (row["title"] or "") + " " + (row["content"] or "")
        score += sum(1 for g in grams if g in hay) * 0.8
        try:
            age = (now - datetime.fromisoformat(str(row["created_at"]))).days
            score += max(0.0, 1.0 - age / 120.0) * 0.5
        except (ValueError, TypeError):
            pass
        score *= float(row["weight"] or 1.0)
        if score > 2.0:
            scored.append((score, row))
    scored.sort(key=lambda x: -x[0])
    mandatory = [r for _, r in scored if r["mandatory"]]
    picked = mandatory + [r for _, r in scored if not r["mandatory"]][:max(0, limit)]
    if record and picked:
        for r in picked:
            conn.execute("UPDATE org_experiences SET uses = uses + 1 WHERE id=?", (r["id"],))
        write_audit(conn, legal_entity_id=entity or "LE-GROUP-HQ", verb="memory_read",
                    task_id=None, layer="system", action="experience_used",
                    target=",".join(r["id"] for r in picked),
                    payload={"query": (text or "")[:200],
                             "ids": [r["id"] for r in picked]},
                    response={"ok": True, "count": len(picked)})
        conn.commit()
    lines = []
    for r in picked:
        label = KIND_LABEL.get(r["kind"], r["kind"])
        snippet = (r["content"] or "").strip()
        if len(snippet) > 400:
            snippet = snippet[:400] + "…"
        lines.append(f"- [{label}] {snippet}")
    return {"text": "\n".join(lines), "ids": [r["id"] for r in picked],
            "count": len(picked), "tags": sorted(tags)}


def list_all(conn, *, kind: str | None = None, state: str | None = None,
             limit: int = 100, tenant_id: str | None = None,
             entity: str | None = None) -> list[dict]:
    ensure(conn)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    where, args = ["tenant_id=?", "legal_entity_id=?"], [tenant_id, entity]
    for col, value in (("kind", kind), ("state", state)):
        if value:
            where.append(f"{col}=?")
            args.append(value)
    return [dict(r) for r in conn.execute("SELECT * FROM org_experiences WHERE "
        + " AND ".join(where) + " ORDER BY rowid DESC LIMIT ?", args + [min(limit, 500)])]


def stats(conn, *, tenant_id: str | None = None, entity: str | None = None) -> dict:
    ensure(conn)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    by_kind = {r["kind"]: r["n"] for r in conn.execute(
        "SELECT kind, COUNT(*) AS n FROM org_experiences"
        " WHERE tenant_id=? AND legal_entity_id=? GROUP BY kind", (tenant_id, entity))}
    by_state = {r["state"]: r["n"] for r in conn.execute(
        "SELECT state, COUNT(*) AS n FROM org_experiences"
        " WHERE tenant_id=? AND legal_entity_id=? GROUP BY state", (tenant_id, entity))}
    total = sum(by_kind.values())
    layers = {"preferences": 0, "project_memories": 0, "org_experiences": total}
    try:
        layers["preferences"] = conn.execute(
            "SELECT COUNT(*) FROM user_preferences p JOIN identities i ON i.id=p.identity_id"
            " WHERE i.tenant_id=? AND i.legal_entity_id=?", (tenant_id, entity)).fetchone()[0]
    except sqlite3.OperationalError:
        pass
    try:
        layers["project_memories"] = conn.execute(
            "SELECT COUNT(*) FROM memory_entries m JOIN ai_employees e ON e.id=m.employee_id"
            " WHERE e.tenant_id=? AND e.legal_entity_id=?", (tenant_id, entity)).fetchone()[0]
    except sqlite3.OperationalError:
        pass
    mandatory = conn.execute("SELECT COUNT(*) FROM org_experiences"
        " WHERE tenant_id=? AND legal_entity_id=? AND mandatory=1 AND state='approved'",
        (tenant_id, entity)).fetchone()[0]
    return {"total": total, "by_kind": by_kind, "by_state": by_state,
            "mandatory": mandatory, "layers": layers}


def red_lines(conn, *, tenant_id: str | None = None, entity: str | None = None) -> list[dict]:
    ensure(conn)
    entity, tenant_id = _scope(conn, entity, tenant_id)
    return [dict(r) for r in conn.execute("""SELECT * FROM org_experiences
        WHERE tenant_id=? AND legal_entity_id=? AND state='approved' AND mandatory=1
        ORDER BY rowid""", (tenant_id, entity))]


def review(conn, exp_id: str, action: str, *, reviewer: str, note: str | None = None,
           tenant_id: str | None = None, entity: str | None = None,
           expected_version: int | None = None, require_pending: bool = False) -> dict:
    ensure(conn)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    entity, tenant_id = _scope(conn, entity, tenant_id)
    actor = _reviewer(conn, reviewer, entity, tenant_id)
    if action not in {"approve", "reject"}:
        raise ValueError("action must be approve|reject")
    row = conn.execute("SELECT * FROM org_experiences WHERE id=? AND tenant_id=?"
                       " AND legal_entity_id=?", (exp_id, tenant_id, entity)).fetchone()
    if row is None:
        raise KeyError(exp_id)
    if expected_version is not None and row["version"] != expected_version:
        raise ReviewConflict("ExperienceVersionChanged")
    if require_pending and row["state"] != "pending":
        raise ReviewConflict("ExperienceAlreadyReviewed")
    expected_version = row["version"] if expected_version is None else expected_version
    changed = conn.execute("""UPDATE org_experiences SET state=?,reviewed_by=?,
        reviewed_at=CURRENT_TIMESTAMP,review_note=?
        WHERE id=? AND tenant_id=? AND legal_entity_id=? AND version=?
          AND (?=0 OR state='pending')""",
        ("approved" if action == "approve" else "rejected", actor["id"],
         (note or "")[:500], exp_id, tenant_id, entity, expected_version, int(require_pending)))
    if changed.rowcount != 1:
        raise ReviewConflict("ExperienceVersionChanged")
    _history(conn, row, action, actor["id"], note or "")
    write_audit(conn, legal_entity_id=entity, verb="memory_write", actor=actor["id"],
                action="experience_reviewed", target=exp_id,
                payload={"action": action, "version": row["version"]}, response={"ok": True})
    conn.commit()
    return dict(conn.execute("SELECT * FROM org_experiences WHERE id=?", (exp_id,)).fetchone())


def edit(conn, exp_id: str, changes: dict, *, actor: str,
         tenant_id: str | None = None, entity: str | None = None) -> dict:
    ensure(conn)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    entity, tenant_id = _scope(conn, entity, tenant_id)
    who = _reviewer(conn, actor, entity, tenant_id)
    row = conn.execute("SELECT * FROM org_experiences WHERE id=? AND tenant_id=?"
                       " AND legal_entity_id=?", (exp_id, tenant_id, entity)).fetchone()
    if row is None:
        raise KeyError(exp_id)
    allowed = {"title", "content", "mandatory", "redline_terms", "tags"}
    values = {k: v for k, v in changes.items() if k in allowed}
    if not values:
        return dict(row)
    if isinstance(values.get("redline_terms"), list):
        values["redline_terms"] = ",".join(values["redline_terms"])
    if "tags" in values:
        tags = values["tags"]
        if isinstance(tags, str):
            tags = [x.strip() for x in tags.replace("，", ",").split(",") if x.strip()]
        values["tags"] = ",".join(tags)[:500]
    _history(conn, row, "edit", who["id"])
    conn.execute("UPDATE org_experiences SET "
        + ",".join(f"{k}=?" for k in values)
        + ",state='pending',version=version+1,reviewed_by=NULL,reviewed_at=NULL,review_note=NULL"
        + " WHERE id=?", list(values.values()) + [exp_id])
    write_audit(conn, legal_entity_id=entity, verb="memory_write", actor=who["id"],
                action="experience_edited", target=exp_id,
                payload={"previous_version": row["version"]}, response={"state": "pending"})
    conn.commit()
    return dict(conn.execute("SELECT * FROM org_experiences WHERE id=?", (exp_id,)).fetchone())
