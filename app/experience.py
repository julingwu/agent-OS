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
  weight REAL DEFAULT 1.0,
  uses INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_exp_kind ON org_experiences(kind);
CREATE INDEX IF NOT EXISTS idx_exp_project ON org_experiences(project_id);
"""

KIND_LABEL = {"rejection": "驳回经验", "consensus": "共识结论", "decision": "决策否决"}


def ensure(conn) -> None:
    conn.executescript(DDL)
    conn.commit()


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
        weight: float = 1.0) -> dict:
    ensure(conn)
    existing = conn.execute(
        """SELECT * FROM org_experiences WHERE kind=? AND source_id=? AND title=?
           LIMIT 1""", (kind, source_id, title)).fetchone()
    if existing is not None:
        return dict(existing)
    eid = new_id("EXP")
    tags = tags if tags is not None else _tags(title + " " + content)
    conn.execute(
        """INSERT INTO org_experiences
             (id, kind, title, content, tags, source_type, source_id, project_id,
              task_id, legal_entity_id, weight)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (eid, kind, title[:160], content[:4000], ",".join(tags), source_type, source_id,
         project_id, task_id, entity, weight))
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
                record: bool = True) -> dict:
    """按标签 + 词面重合度召回经验；返回可直接拼进 prompt 的文本与 ids。"""
    ensure(conn)
    tags = set(_tags(text))
    grams = _grams(text)
    rows = conn.execute(
        """SELECT * FROM org_experiences WHERE weight > 0
           ORDER BY rowid DESC LIMIT 300""").fetchall()
    scored = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for row in rows:
        if entity and row["legal_entity_id"] not in (entity, "LE-GROUP-HQ"):
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
    picked = [r for _, r in scored[:limit]]
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


def list_all(conn, *, kind: str | None = None, limit: int = 100) -> list[dict]:
    ensure(conn)
    if kind:
        rows = conn.execute("SELECT * FROM org_experiences WHERE kind=?"
                            " ORDER BY rowid DESC LIMIT ?", (kind, limit)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM org_experiences ORDER BY rowid DESC LIMIT ?",
                            (limit,)).fetchall()
    return [dict(r) for r in rows]


def stats(conn) -> dict:
    ensure(conn)
    by_kind = {r["kind"]: r["n"] for r in conn.execute(
        "SELECT kind, COUNT(*) AS n FROM org_experiences GROUP BY kind")}
    total = sum(by_kind.values())
    layers = {"preferences": 0, "project_memories": 0, "org_experiences": total}
    try:
        layers["preferences"] = conn.execute(
            "SELECT COUNT(*) FROM user_preferences").fetchone()[0]
    except sqlite3.OperationalError:
        pass
    try:
        layers["project_memories"] = conn.execute(
            "SELECT COUNT(*) FROM memory_entries").fetchone()[0]
    except sqlite3.OperationalError:
        pass
    return {"total": total, "by_kind": by_kind, "layers": layers}
