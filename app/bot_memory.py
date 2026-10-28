"""每个 Bot 的长期记忆：写入、去重、检索（v1 产品层）。

与旧版 `app/memory.py`（OA 的 scoped memory）无关，互不影响。

设计取舍（第一性原理）：
  * 记忆是**每个 Bot 私有**的，不共享 —— 共享会立刻退化成"一个人的记忆"，Bot 就没个性了。
  * 去重靠 (bot_id, 归一化内容 hash)，同一句事实反复说不重复存。
  * 检索不用向量库：SQLite 装不下 embedding 依赖，中文也没有空格分词。
    这里用「中文 bigram + 英文词」做子串命中打分，再叠加重要度和新鲜度。
    记忆量级在千条内，Python 打分足够快。
  * 记忆会过时：prompt 里明确告诉模型"冲突时以用户最新说法为准"，避免记忆变枷锁。
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta

MAX_CONTENT = 500
MAX_PER_BOT = 800
SEARCH_SCAN = 400

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[a-zA-Z][a-zA-Z0-9_.+-]{1,}")
_PUNCT = re.compile(r"[\s，。！？、；：“”‘’（）【】《》,.!?;:\"'()\[\]<>|/\\]+")

_SCHEMA_READY = False

# 记忆槽位是**封闭词表**：如果让模型自由起名，同一个事实会散在"公司/公司名称/公司信息"
# 三个槽里，重复问题只是从内容搬到了 topic 上。宁可槽位粗一点，也不要漂移。
TOPICS = ("公司", "产品", "客户", "成员", "偏好", "回答风格",
          "技术栈", "服务器", "流程", "其它")

_TOPIC_ALIASES = {
    "公司名称": "公司", "公司名": "公司", "公司信息": "公司", "企业": "公司",
    "回答偏好": "回答风格", "偏好设置": "回答风格", "风格": "回答风格",
    "说话方式": "回答风格", "沟通方式": "回答风格",
    "客户名称": "客户", "客户称呼": "客户", "客户信息": "客户",
    "服务器规格": "服务器", "机器": "服务器", "部署": "服务器",
    "技术选型": "技术栈", "协作工具": "流程", "工作流程": "流程",
    "团队": "成员", "人员": "成员",
}


def normalize_topic(topic: str) -> str:
    """把模型给的槽位名收敛到封闭词表里；认不出来的一律进"其它"。"""
    t = (topic or "").strip()
    if not t:
        return ""
    t = _TOPIC_ALIASES.get(t, t)
    for suffix in ("名称", "信息", "资料", "规格", "情况", "设置"):
        if t.endswith(suffix) and t[: -len(suffix)] in TOPICS:
            t = t[: -len(suffix)]
            break
    return t if t in TOPICS else "其它"


def ensure(conn) -> None:
    """老库迁移：memories 表可能没有 topic 列（甚至整张表不存在）。

    只在首次调用时检查一次；生产库是就地升级，不做重建。
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    if not cols:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS memories ("
            " id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, session_id TEXT,"
            " kind TEXT NOT NULL DEFAULT 'fact', content TEXT NOT NULL,"
            " tags TEXT NOT NULL DEFAULT '', importance INTEGER NOT NULL DEFAULT 1,"
            " topic TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL,"
            " source_event_id INTEGER, created_at TEXT NOT NULL,"
            " last_used_at TEXT, use_count INTEGER NOT NULL DEFAULT 0);"
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_bot_hash"
            " ON memories(bot_id, content_hash);"
            "CREATE INDEX IF NOT EXISTS idx_memories_bot_time"
            " ON memories(bot_id, created_at DESC);")
    elif "topic" not in cols:
        conn.execute("ALTER TABLE memories ADD COLUMN topic TEXT NOT NULL DEFAULT ''")
    conn.commit()
    _SCHEMA_READY = True


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _norm(text: str) -> str:
    return _PUNCT.sub(" ", (text or "").strip().lower()).strip()


def _hash(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()[:32]


def _tokens(query: str) -> set[str]:
    """查询词 → 中文 bigram + 英文/数字词。"""
    q = (query or "").lower()
    tokens: set[str] = {m.lower() for m in _LATIN.findall(q)}
    for run in _CJK_RUN.findall(q):
        if len(run) == 1:
            tokens.add(run)
        for i in range(len(run) - 1):
            tokens.add(run[i:i + 2])
    tokens.discard("")
    return tokens


def _containment(a: set[str], b: set[str]) -> float:
    """两个词集的重合度（用较小集合做分母，容忍一方更长）。"""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _find_near_duplicate(conn, bot_id: str, content: str) -> dict | None:
    """找出语义上几乎重复的旧记忆。

    精确 hash 只能挡"一字不差"，挡不住"用户偏好先给结论" vs "用户喜欢先看结论"。
    这里用中文 bigram 的重合度做近似判重，阈值 0.75：同一条事实换个说法不会再存一条。
    """
    tokens = _tokens(content)
    if not tokens:
        return None
    rows = conn.execute(
        "SELECT id, content, importance FROM memories WHERE bot_id=?"
        " ORDER BY created_at DESC LIMIT 200", (bot_id,)).fetchall()
    for row in rows:
        if _containment(tokens, _tokens(row["content"])) >= 0.8:
            return dict(row)
    return None


def add(conn, bot_id: str, content: str, *, kind: str = "fact",
        tags: str = "", importance: int = 1, session_id: str | None = None,
        topic: str = "", source_event_id: int | None = None) -> dict | None:
    """写一条记忆；完全重复 / 同 topic 覆盖 / 近似重复都返回 None。

    三种去重（按优先级）：
      1. 同 topic 槽位 → 覆盖旧值（"公司名称"只有一格，新值替换旧值）；
      2. 内容 hash 相同 → 只累加命中次数；
      3. 近似重复（bigram 重合 ≥ 0.8）→ 合并重要度。
    """
    ensure(conn)
    content = (content or "").strip()
    if not content or not bot_id:
        return None
    content = content[:MAX_CONTENT]
    topic = normalize_topic(topic)
    weight = max(1, min(int(importance or 1), 3))
    h = _hash(content)

    if topic:
        row = conn.execute(
            "SELECT id FROM memories WHERE bot_id=? AND topic=?",
            (bot_id, topic)).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE memories SET content=?, kind=?, importance=MAX(importance, ?),"
                " content_hash=?, use_count=use_count+1, last_used_at=? WHERE id=?",
                (content, kind or "fact", weight, h, _now(), row["id"]))
            conn.commit()
            return None

    row = conn.execute(
        "SELECT id FROM memories WHERE bot_id=? AND content_hash=?",
        (bot_id, h)).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE memories SET use_count = use_count + 1, last_used_at=? WHERE id=?",
            (_now(), row["id"]))
        conn.commit()
        return None

    near = _find_near_duplicate(conn, bot_id, content)
    if near is not None:
        conn.execute(
            "UPDATE memories SET importance=MAX(importance, ?), use_count=use_count+1,"
            " last_used_at=?, content=CASE WHEN length(?) > length(content) THEN ? ELSE content END"
            " WHERE id=?",
            (weight, _now(), content, content, near["id"]))
        conn.commit()
        return None

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM memories WHERE bot_id=?", (bot_id,)).fetchone()["n"]
    if total >= MAX_PER_BOT:
        conn.execute(
            "DELETE FROM memories WHERE id IN ("
            " SELECT id FROM memories WHERE bot_id=?"
            " ORDER BY importance ASC, COALESCE(last_used_at, created_at) ASC LIMIT 40)",
            (bot_id,))

    memory_id = "mem_" + secrets.token_hex(8)
    conn.execute(
        "INSERT INTO memories (id, bot_id, session_id, kind, content, tags,"
        " importance, topic, content_hash, source_event_id, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (memory_id, bot_id, session_id, kind or "fact", content,
         tags or "", weight, topic, h, source_event_id, _now()))
    conn.commit()
    return {"id": memory_id, "bot_id": bot_id, "kind": kind or "fact",
            "content": content, "importance": weight, "tags": tags, "topic": topic}


def _score(content: str, tokens: set[str], importance: int, created_at: str) -> float:
    text = content.lower()
    hits = sum(1 for t in tokens if t and t in text)
    if hits == 0:
        return 0.0
    score = hits * 3.0 + importance
    try:
        created = datetime.fromisoformat(created_at)
        age = datetime.now().astimezone() - created
        if age < timedelta(days=7):
            score += 1.5
        elif age < timedelta(days=30):
            score += 0.5
    except (TypeError, ValueError):
        pass
    return score


def search(conn, bot_id: str, query: str, *, limit: int = 8) -> list[dict]:
    """按当前问题找最相关的记忆。"""
    ensure(conn)
    rows = conn.execute(
        "SELECT * FROM memories WHERE bot_id=? ORDER BY created_at DESC LIMIT ?",
        (bot_id, SEARCH_SCAN)).fetchall()
    tokens = _tokens(query or "")
    if not tokens:
        return []
    scored: list[tuple[float, dict]] = []
    for r in rows:
        item = dict(r)
        score = _score(item["content"], tokens, item["importance"], item["created_at"])
        if score > 0:
            scored.append((score, item))
    if not scored:
        return []
    scored.sort(key=lambda pair: pair[0], reverse=True)
    picked = [item for _, item in scored[:max(1, min(limit, 20))]]
    now = _now()
    for item in picked:
        conn.execute("UPDATE memories SET use_count=use_count+1, last_used_at=? WHERE id=?",
                     (now, item["id"]))
    conn.commit()
    return picked


def list_for_bot(conn, bot_id: str, *, limit: int = 50,
                 offset: int = 0) -> list[dict]:
    ensure(conn)
    rows = conn.execute(
        "SELECT * FROM memories WHERE bot_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (bot_id, max(1, min(limit, 200)), max(0, offset))).fetchall()
    return [dict(r) for r in rows]


def count(conn, bot_id: str) -> int:
    ensure(conn)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM memories WHERE bot_id=?", (bot_id,)).fetchone()
    return int(row["n"] if row else 0)


def delete(conn, memory_id: str) -> bool:
    ensure(conn)
    cur = conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
    conn.commit()
    return cur.rowcount > 0


def enabled(conn) -> bool:
    row = conn.execute(
        "SELECT value_json FROM app_settings WHERE key='memory_enabled'").fetchone()
    if row is None:
        return True
    try:
        return bool(json.loads(row["value_json"]))
    except (TypeError, ValueError):
        return True
