"""Hard red lines (v3.2): L0 compliance, not a suggestion.

A mandatory experience can carry `redline_terms` (comma separated). If a run's
artifacts contain any term, acceptance fails immediately with an L0 criterion
and the task is rejected — no human approval path.
"""
from __future__ import annotations

import sqlite3
import hashlib
import json

from .acceptance.l0_rules import BANNED_PHRASES

DEFAULT_ABSOLUTE = [
    "最好", "最佳", "最强", "最高级", "最低价", "最先进", "最有效",
    "第一品牌", "销量第一", "全国第一", "排名第一",
    "绝对", "100%", "百分之百", "永久", "根治", "治愈", "药到病除", "无副作用",
]


def _terms(row) -> list[str]:
    raw = (row["redline_terms"] or "").strip()
    if raw:
        return [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    content = (row["content"] or "")
    if any(k in content for k in ("绝对化", "广告法", "禁用词")):
        return list(DEFAULT_ABSOLUTE)
    return []


def rules(conn: sqlite3.Connection, *, tenant_id: str | None = None,
          entity: str | None = None) -> list[dict]:
    from . import experience
    rows = experience.red_lines(conn, tenant_id=tenant_id, entity=entity)
    out = []
    for r in rows:
        terms = _terms(r)
        if terms:
            snapshot = {"title": r["title"], "content": r["content"],
                        "redline_terms": r["redline_terms"], "version": r["version"]}
            out.append({"id": r["id"], "title": r["title"], "terms": terms,
                        "version": r["version"], "content_hash": hashlib.sha256(
                            json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
                            .encode("utf-8")).hexdigest()})
    return out


def check(conn: sqlite3.Connection, text: str, *, tenant_id: str | None = None,
          entity: str | None = None, file_path: str | None = None) -> list[dict]:
    from .acceptance.l0_rules import text_for_rule
    text = text_for_rule("banned", text or "", file_path)
    text = text or ""
    hits = []
    for rule in rules(conn, tenant_id=tenant_id, entity=entity):
        found = [t for t in rule["terms"] if t and t in text]
        if found:
            hits.append({"experience_id": rule["id"], "title": rule["title"],
                         "terms": sorted(set(found)), "version": rule["version"],
                         "content_hash": rule["content_hash"]})
    return hits
