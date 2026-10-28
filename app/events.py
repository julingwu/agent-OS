"""事件流：唯一真相（契约 01 的可执行版本）。

设计约束（逐条对应 docs/design/contracts/01-events.md）：
  * 事件只追加，不改不删；
  * id 全局单调递增，客户端游标就是它；
  * 所有写路径必须经过 ``emit()``，全仓不得出现第二处 INSERT INTO events；
  * 事件体 > 8KB 直接拒绝（大对象落 artifact，事件里只放引用）；
  * 幂等键 (kind, client_msg_id)：同一条用户消息重投只落一次；
  * 投影（聊天/详情/资料库/电脑）只读事件与聚合表，不各自造状态。

写入与广播是两个动作：
    event = emit(conn, ...)        # 只写库，不提交、不广播
    conn.commit()
    publish(event)                  # 提交成功后再广播，避免回滚后发出幽灵事件
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime

SCHEMA = pathlib.Path(__file__).with_name("schema_v1.sql")

MAX_PAYLOAD_BYTES = 8 * 1024
MAX_READ_LIMIT = 500
DEFAULT_READ_LIMIT = 200
SUBSCRIBER_QUEUE = 500

# ---------------------------------------------------------------- 闭集
# 新增 kind 必须先改契约文档 + 迁移，不允许在业务代码里随手拼字符串。
KINDS = frozenset({
    # 会话与消息
    "message.user", "message.bot", "message.system", "message.delta", "message.edited",
    "message.deleted", "reaction.set", "draft.created", "typing",
    # 回合与工具
    "turn.started", "turn.ended", "thought.delta", "tool.call", "tool.result",
    "needs_human", "error",
    # 电脑
    "computer.state", "process.started", "process.tick", "process.exited",
    "process.killed", "file.created", "file.updated", "file.deleted",
    # 审批与预算
    "approval.requested", "approval.decided", "budget.updated", "secret.requested",
    # 协作
    "handoff.created", "handoff.accepted", "handoff.result",
    # 资料库 / 技能 / 例行任务
    "memory.updated", "skill.created", "skill.updated",
    "routine.created", "routine.updated", "routine.triggered", "routine.finished",
    "artifact.attached",
})

# typing 是瞬态事件：只广播、不落库（否则事件表被心跳淹没）。
TRANSIENT_KINDS = frozenset({"typing", "message.delta"})

ORIGINS = frozenset({"cloud", "exe", "user", "system"})

# 每种 kind 的必填 payload 键。只校验必填，允许附加字段（向前兼容）。
REQUIRED_PAYLOAD = {
    "message.user": ("text",),
    "message.bot": ("text",),
    "message.system": ("text",),
    "message.edited": ("target_id",),
    "message.deleted": ("target_id",),
    "reaction.set": ("target_id", "emoji", "on"),
    "draft.created": ("draft_id", "channel", "body"),
    "typing": ("state",),
    "turn.started": ("input_event_id",),
    "turn.ended": ("status",),
    "thought.delta": ("text",),
    "tool.call": ("tool", "call_id"),
    "tool.result": ("call_id", "ok"),
    "needs_human": ("question",),
    "error": ("code", "message"),
    "computer.state": ("status",),
    "process.started": ("process_id", "cmd"),
    "process.tick": ("process_id",),
    "process.exited": ("process_id",),
    "process.killed": ("process_id",),
    "file.created": ("path",),
    "file.updated": ("path",),
    "file.deleted": ("path",),
    "approval.requested": ("approval_id", "action"),
    "approval.decided": ("approval_id", "decision"),
    "budget.updated": ("used", "limit", "state"),
    "secret.requested": ("secret_id", "prompt"),
    "handoff.created": ("from_bot", "to_bot", "task"),
    "handoff.accepted": ("handoff_id",),
    "handoff.result": ("handoff_id",),
    "memory.updated": ("memory_id", "action"),
    "skill.created": ("skill_id", "name"),
    "skill.updated": ("skill_id",),
    "routine.created": ("routine_id",),
    "routine.updated": ("routine_id",),
    "routine.triggered": ("routine_id",),
    "routine.finished": ("routine_id",),
    "artifact.attached": ("artifact_id", "name"),
}


class EventError(ValueError):
    """事件定义违规（未知 kind / 缺必填字段 / 超大 / 未知 origin）。"""


@dataclass(frozen=True)
class Event:
    id: int
    ts: str
    bot_id: str
    session_id: str | None
    kind: str
    origin: str
    actor: dict
    turn_id: str | None
    refs: dict
    payload: dict

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "bot_id": self.bot_id,
            "session_id": self.session_id,
            "kind": self.kind,
            "origin": self.origin,
            "actor": self.actor,
            "turn_id": self.turn_id,
            "refs": self.refs,
            "payload": self.payload,
        }


def ensure(conn: sqlite3.Connection) -> None:
    """建 v1 表（幂等）。由 db.init_db() 调用。"""
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))


def now_iso() -> str:
    """服务端时间戳：本地时区，毫秒精度（与契约一致）。"""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def emit(
    conn: sqlite3.Connection,
    *,
    bot_id: str,
    kind: str,
    payload: dict,
    session_id: str | None = None,
    origin: str = "cloud",
    actor: dict | None = None,
    turn_id: str | None = None,
    refs: dict | None = None,
    client_msg_id: str | None = None,
) -> Event:
    """写入一条事件。调用方负责 commit，然后（可选）调用 publish()。

    幂等：同一 (kind, client_msg_id) 再次写入时返回已存在的那条，不重复插入。
    """
    if kind not in KINDS:
        raise EventError(f"unknown event kind: {kind!r}")
    if origin not in ORIGINS:
        raise EventError(f"unknown origin: {origin!r}")
    if not bot_id:
        raise EventError("bot_id is required")
    if not isinstance(payload, dict):
        raise EventError("payload must be an object")

    missing = [k for k in REQUIRED_PAYLOAD.get(kind, ()) if k not in payload]
    if missing:
        raise EventError(f"{kind} payload missing keys: {', '.join(missing)}")

    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise EventError(
            f"payload too large for {kind}: >{MAX_PAYLOAD_BYTES}B; "
            "write the object to an artifact and reference it instead"
        )

    actor = actor or {"type": "bot", "id": bot_id}
    ts = now_iso()

    if kind in TRANSIENT_KINDS:
        # 瞬态事件不落库，仅返回一个未持久化的对象供广播（id=0，ts 已填）。
        return Event(id=0, ts=ts, bot_id=bot_id, session_id=session_id,
                     kind=kind, origin=origin, actor=actor, turn_id=turn_id,
                     refs=refs or {}, payload=payload)

    cur = conn.execute(
        "INSERT INTO events (ts, bot_id, session_id, kind, origin, actor_json,"
        " turn_id, refs_json, payload_json, client_msg_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(kind, client_msg_id) DO NOTHING",
        (ts, bot_id, session_id, kind, origin,
         json.dumps(actor, ensure_ascii=False, separators=(",", ":")),
         turn_id,
         json.dumps(refs or {}, ensure_ascii=False, separators=(",", ":")),
         payload_json, client_msg_id),
    )
    if cur.rowcount == 0:
        row = conn.execute(
            "SELECT * FROM events WHERE kind=? AND client_msg_id=?",
            (kind, client_msg_id),
        ).fetchone()
        if row is None:  # 并发下极端情况：交给调用方重试
            raise EventError("idempotent insert lost its row; retry the emit")
        return _row_to_event(row)
    return _row_to_event(
        conn.execute("SELECT * FROM events WHERE id=?", (cur.lastrowid,)).fetchone()
    )


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"], ts=row["ts"], bot_id=row["bot_id"],
        session_id=row["session_id"], kind=row["kind"], origin=row["origin"],
        actor=json.loads(row["actor_json"]),
        turn_id=row["turn_id"],
        refs=json.loads(row["refs_json"] or "{}"),
        payload=json.loads(row["payload_json"]),
    )


def read(
    conn: sqlite3.Connection,
    *,
    since: int = 0,
    session_id: str | None = None,
    bot_id: str | None = None,
    kinds: list[str] | None = None,
    limit: int = DEFAULT_READ_LIMIT,
) -> list[Event]:
    """按游标读事件（升序）。since 是"已收到的最后一个 id"，返回 id > since。"""
    limit = max(1, min(int(limit), MAX_READ_LIMIT))
    sql = ["SELECT * FROM events WHERE id > ?"]
    args: list = [int(since)]
    if session_id:
        sql.append("AND session_id = ?")
        args.append(session_id)
    if bot_id:
        sql.append("AND bot_id = ?")
        args.append(bot_id)
    if kinds:
        unknown = [k for k in kinds if k not in KINDS]
        if unknown:
            raise EventError(f"unknown event kinds: {unknown}")
        sql.append("AND kind IN (%s)" % ",".join("?" * len(kinds)))
        args.extend(kinds)
    sql.append("ORDER BY id ASC LIMIT ?")
    args.append(limit)
    rows = conn.execute(" ".join(sql), tuple(args)).fetchall()
    return [_row_to_event(r) for r in rows]


def cursor(conn: sqlite3.Connection) -> int:
    """当前全局游标（最大事件 id）。"""
    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS c FROM events").fetchone()
    return int(row["c"])


def snapshot(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    limit: int = 50,
    before_id: int | None = None,
) -> dict:
    """会话首屏快照：取最后 N 条（可向更早翻页），返回升序事件 + 游标。"""
    limit = max(1, min(int(limit), MAX_READ_LIMIT))
    sql = ["SELECT * FROM events WHERE session_id = ?"]
    args: list = [session_id]
    if before_id:
        sql.append("AND id < ?")
        args.append(int(before_id))
    sql.append("ORDER BY id DESC LIMIT ?")
    args.append(limit + 1)
    rows = conn.execute(" ".join(sql), tuple(args)).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    events = [_row_to_event(r) for r in reversed(rows)]
    return {
        "events": [e.to_dict() for e in events],
        "cursor": events[-1].id if events else cursor(conn),
        "has_more": has_more,
    }


# ---------------------------------------------------------------- 广播器
class Broadcaster:
    """进程内广播：每条已提交事件推给所有订阅者（WSS 层在 W2 包 asyncio）。

    慢订阅者不是阻塞点：队列满时丢最旧的并置 dropped 标记，由客户端凭游标补拉。
    """

    def __init__(self, queue_size: int = SUBSCRIBER_QUEUE) -> None:
        self._lock = threading.Lock()
        self._subs: dict[int, deque] = {}
        self._next = 1
        self._size = queue_size
        self._dropped: set[int] = set()

    def subscribe(self) -> int:
        with self._lock:
            sid = self._next
            self._next += 1
            self._subs[sid] = deque(maxlen=self._size)
            return sid

    def unsubscribe(self, sid: int) -> None:
        with self._lock:
            self._subs.pop(sid, None)
            self._dropped.discard(sid)

    def publish(self, event: Event) -> None:
        """必须在事务提交之后调用；瞬态事件（id=0）同样广播。"""
        item = event.to_dict()
        with self._lock:
            for sid, q in self._subs.items():
                if len(q) == q.maxlen:
                    self._dropped.add(sid)
                q.append(item)

    def drain(self, sid: int) -> tuple[list[dict], bool]:
        """取出该订阅者目前已缓冲的事件；第二返回值表示期间是否有丢弃。"""
        with self._lock:
            q = self._subs.get(sid)
            if q is None:
                return [], False
            items = list(q)
            q.clear()
            dropped = sid in self._dropped
            self._dropped.discard(sid)
            return items, dropped

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


broadcaster = Broadcaster()
