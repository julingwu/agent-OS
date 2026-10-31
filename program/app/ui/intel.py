"""Agent 的「记忆 / 上下文 / 查库」三件套。

三件事分开做，谁也不绑死在别处：

1. 偏好：从一句话里抽出「以后叫我老板」这类指令，存进 user_preferences，
   之后每次拼 prompt 都带上。
2. 上下文：群里最近的对话打包成 history，模型不再是金鱼。
3. 查库：遇到「我的秘书呢」「现在有几个任务」这类问题，先把组织架构 /
   任务 / 预算查出来，作为事实注入；模型只负责组织语言，不许瞎猜。

工具调用在这里是**服务端执行后注入**（不是让模型自己 function-call）：
ACP 侧只能拿到一段文本，把查询结果写进 prompt 是唯一稳的做法。
"""
from __future__ import annotations

import re
import sqlite3

# ------------------------------------------------------------------ 偏好

# 「以后叫我老板」「叫我一声老大」「以后称呼我为老板」
_TITLE_PATTERNS = [
    re.compile(r"(?:以后|今后|之后)?\s*(?:请)?(?:叫我|称呼我(?:为|作)?|喊我)\s*[「『\"']?([\u4e00-\u9fa5A-Za-z]{1,10})[」』\"']?"),
]
_WANT_TITLE = re.compile(r"(以后|今后|之后|记住|请|都)")
_STOP_WORDS = {"什么", "一下", "一声", "名字", "小文", "小码"}
_VERB_HINTS = re.compile(r"(看|说|帮|查|写|做|来|去|试|想|问|统计|列)")


def extract_preference(text: str) -> tuple[str, str] | None:
    """把「以后叫我老板」变成 ('boss_title', '老板')。

    宁可不记，也不能记错：只接受 1-6 个字、且不含动词的称呼，
    否则「叫我看看这个」会被记成 boss_title=看看这个。
    """
    t = (text or "").strip()
    if not t or len(t) > 40:
        return None
    for pat in _TITLE_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        value = m.group(1).strip()
        if not value or value in _STOP_WORDS:
            continue
        if len(value) > 6 or _VERB_HINTS.search(value):
            continue
        # 只有明确是「设定称呼」的句子才写入，避免「叫我看看」这种误伤
        if _WANT_TITLE.search(t[: m.start() + 2]) or t.startswith("叫我") or "以后" in t:
            return "boss_title", value
    return None


def save_preference(conn: sqlite3.Connection, identity_id: str, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO user_preferences (identity_id, key, value, updated_at)
           VALUES (?,?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(identity_id, key) DO UPDATE SET value=excluded.value,
             updated_at=CURRENT_TIMESTAMP""", (identity_id, key, value))
    conn.commit()


def delete_preference(conn: sqlite3.Connection, identity_id: str, key: str) -> None:
    conn.execute("DELETE FROM user_preferences WHERE identity_id=? AND key=?", (identity_id, key))
    conn.commit()


def clear_preferences(conn: sqlite3.Connection, identity_id: str) -> None:
    conn.execute("DELETE FROM user_preferences WHERE identity_id=?", (identity_id,))
    conn.commit()


def preferences(conn: sqlite3.Connection, identity_id: str | None) -> dict[str, str]:
    if not identity_id:
        return {}
    rows = conn.execute("SELECT key, value FROM user_preferences WHERE identity_id=?",
                        (identity_id,)).fetchall()
    return {r["key"]: r["value"] for r in rows}


def pref_block(prefs: dict[str, str]) -> str:
    if not prefs:
        return ""
    bits = []
    if prefs.get("boss_title"):
        bits.append(f"称呼用户为「{prefs['boss_title']}」")
    for k, v in prefs.items():
        if k != "boss_title":
            bits.append(f"{k}={v}")
    return "【重要偏好】" + "；".join(bits) + "。每次回答都必须遵守。"


# ------------------------------------------------------------------ 查库

def org_digest(conn: sqlite3.Connection, entity: str | None) -> str:
    ents = conn.execute(
        "SELECT id, name, type, parent_entity_id FROM legal_entities"
        " WHERE COALESCE(state,'active')='active' AND id=?", (entity,)).fetchall()
    lines = ["【组织架构】"]
    for e in ents:
        lines.append(f"- {e['name']}（{e['type']}，{e['id']}）")
        emps = conn.execute(
            """SELECT e.name, e.status, p.title FROM ai_employees e
               LEFT JOIN positions p ON p.id = e.position_id
               WHERE e.legal_entity_id=? AND COALESCE(e.status,'online')!='fired'""",
            (e["id"],)).fetchall()
        for p in emps:
            lines.append(f"    · {p['name']} — {p['title'] or '员工'}（{p['status']}）")
    return "\n".join(lines)


def task_digest(conn: sqlite3.Connection, entity: str | None, limit: int = 8) -> str:
    sql = """SELECT id, title, state, created_at FROM tasks"""
    args: list = []
    if entity:
        sql += " WHERE legal_entity_id=?"
        args.append(entity)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    if not rows:
        return "【最近任务】暂无"
    lines = ["【最近任务】"]
    for r in rows:
        lines.append(f"- {r['id']} {r['title'][:24]} → {r['state']}")
    return "\n".join(lines)


def budget_digest(conn: sqlite3.Connection, entity: str | None) -> str:
    if not entity:
        return ""
    row = conn.execute(
        """SELECT limit_tokens, used_tokens FROM budgets
           WHERE legal_entity_id=? AND task_id IS NULL AND identity_id IS NULL
           ORDER BY rowid DESC LIMIT 1""", (entity,)).fetchone()
    if not row or not row["limit_tokens"]:
        return ""
    left = int(row["limit_tokens"]) - int(row["used_tokens"] or 0)
    return f"【预算】本实体剩余 {left:,} tokens（上限 {int(row['limit_tokens']):,}）"


_ORG_HINTS = re.compile(r"(秘书|员工|同事|团队|组织|架构|部门|谁|哪些人|几个人|花名册)")
_TASK_HINTS = re.compile(r"(任务|进度|进展|做完了|交付|验收|卡住)")
_BUDGET_HINTS = re.compile(r"(预算|额度|还剩|花了多少|token)", re.I)


def lookup(conn: sqlite3.Connection, text: str, entity: str | None) -> str:
    """按问题类型挑要查的表，返回一段事实文本（空串表示不需要查）。"""
    t = text or ""
    chunks = []
    if _ORG_HINTS.search(t):
        chunks.append(org_digest(conn, entity))
    if _TASK_HINTS.search(t):
        chunks.append(task_digest(conn, entity))
    if _BUDGET_HINTS.search(t):
        b = budget_digest(conn, entity)
        if b:
            chunks.append(b)
    return "\n\n".join(chunks)


def needs_lookup(text: str) -> bool:
    return bool(_ORG_HINTS.search(text or "") or _TASK_HINTS.search(text or "") or _BUDGET_HINTS.search(text or ""))


# ------------------------------------------------------------------ 上下文

def history_block(conn: sqlite3.Connection, chat_id: str, limit: int = 10) -> str:
    rows = conn.execute(
        """SELECT role, author, content FROM chat_messages
           WHERE chat_id=? AND COALESCE(kind,'text')!='card' ORDER BY id DESC LIMIT ?""",
        (chat_id, limit)).fetchall()
    if not rows:
        return ""
    lines = ["【群里最近的对话】"]
    for r in reversed(rows):
        who = r["author"] or ("我" if r["role"] == "human" else "AI")
        lines.append(f"{who}：{(r['content'] or '').strip()[:200]}")
    return "\n".join(lines)


def kb_block(conn: sqlite3.Connection, entity: str | None, limit: int = 3) -> str:
    if not entity:
        return ""
    rows = conn.execute(
        """SELECT name, excerpt FROM kb_documents
           WHERE legal_entity_id=? AND COALESCE(state,'active')='active'
           ORDER BY created_at DESC LIMIT ?""", (entity, limit)).fetchall()
    if not rows:
        return ""
    lines = ["【部门知识库】"]
    for r in rows:
        lines.append(f"- {r['name']}：{(r['excerpt'] or '')[:300]}")
    return "\n".join(lines)


def build_task_prompt(conn: sqlite3.Connection, base: str, *, chat_id: str | None,
                      identity_id: str | None, entity: str | None) -> str:
    """偏好 + 群历史 + 知识库 + 原始任务指令。"""
    parts = []
    pb = pref_block(preferences(conn, identity_id))
    if pb:
        parts.append(pb)
    if chat_id:
        hb = history_block(conn, chat_id)
        if hb:
            parts.append(hb)
    kb = kb_block(conn, entity)
    if kb:
        parts.append(kb)
    parts.append(base)
    return "\n\n".join(parts)
