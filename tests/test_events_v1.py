"""契约 01（事件协议）的验收测试。

覆盖 docs/design/contracts/01-events.md 第 8 节检查单里的可自动化部分：
  * emit() 是唯一写入口，kind 闭集 / 必填字段 / origin / bot_id 校验
  * 幂等：同一 (kind, client_msg_id) 重投只落一条，返回同一个 id
  * 超大 payload 被拒绝（必须改用 artifact 引用）
  * 瞬态事件（typing）不落库
  * read(since=) 游标语义；snapshot 升序 + 分页
  * 广播器：发布/排空；慢订阅者丢弃可被感知
"""
from __future__ import annotations

import os
import pathlib
import shutil
import uuid

import pytest

# 注：不用 pytest 的 tmp_path —— 该沙箱下 pytest 自建的临时目录无法 scandir。
# 统一在工作区内建一次性目录，跑完尽力清理。
_TMP_ROOT = pathlib.Path(__file__).resolve().parents[1] / ".tmp-tests"


@pytest.fixture()
def conn(monkeypatch):
    root = _TMP_ROOT / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DB_PATH", str(root / "oa.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(root / "workspace"))
    monkeypatch.setenv("M0_SANDBOX", "none")
    (root / "workspace").mkdir(exist_ok=True)

    from app import db

    db.init_db()
    c = db.connect()
    try:
        yield c
    finally:
        c.close()
        shutil.rmtree(root, ignore_errors=True)


def emit(conn, **kw):
    from app import events

    kw.setdefault("bot_id", "bot_1")
    kw.setdefault("session_id", "ses_1")
    return events.emit(conn, **kw)


# ---------------------------------------------------------------- emit 校验
def test_emit_rejects_unknown_kind(conn):
    from app.events import EventError

    with pytest.raises(EventError, match="unknown event kind"):
        emit(conn, kind="message.telepathy", payload={"text": "x"})


def test_emit_requires_bot_id(conn):
    from app.events import EventError, emit as real_emit

    with pytest.raises(EventError, match="bot_id"):
        real_emit(conn, bot_id="", kind="message.user", payload={"text": "hi"})


def test_emit_rejects_unknown_origin(conn):
    from app.events import EventError

    with pytest.raises(EventError, match="origin"):
        emit(conn, kind="message.user", payload={"text": "hi"}, origin="mars")


def test_emit_validates_required_payload_keys(conn):
    from app.events import EventError

    with pytest.raises(EventError, match="missing keys"):
        emit(conn, kind="tool.call", payload={"tool": "exec"})  # 缺 call_id


def test_emit_rejects_oversize_payload(conn):
    from app.events import EventError

    with pytest.raises(EventError, match="too large"):
        emit(conn, kind="message.bot", payload={"text": "x" * 9000})


# ---------------------------------------------------------------- 幂等
def test_duplicate_client_msg_id_is_idempotent(conn):
    first = emit(conn, kind="message.user", payload={"text": "你好"},
                 origin="user", client_msg_id="c-1")
    second = emit(conn, kind="message.user", payload={"text": "你好"},
                  origin="user", client_msg_id="c-1")
    conn.commit()

    assert first.id == second.id
    rows = conn.execute("SELECT COUNT(*) AS n FROM events WHERE client_msg_id='c-1'").fetchone()
    assert rows["n"] == 1


def test_null_client_msg_id_does_not_collide(conn):
    a = emit(conn, kind="message.bot", payload={"text": "a"})
    b = emit(conn, kind="message.bot", payload={"text": "b"})
    conn.commit()
    assert a.id != b.id


# ---------------------------------------------------------------- 瞬态事件
def test_transient_event_is_not_persisted(conn):
    ev = emit(conn, kind="typing", payload={"state": "on"})
    conn.commit()

    assert ev.id == 0
    row = conn.execute("SELECT COUNT(*) AS n FROM events WHERE kind='typing'").fetchone()
    assert row["n"] == 0


# ---------------------------------------------------------------- 读与游标
def test_read_returns_ascending_ids_after_since(conn):
    from app import events

    ids = [emit(conn, kind="message.bot", payload={"text": str(i)}).id for i in range(5)]
    conn.commit()

    got = events.read(conn, since=ids[1], limit=10)
    assert [e.id for e in got] == ids[2:]

    # limit 生效且最多 MAX_READ_LIMIT
    assert len(events.read(conn, since=0, limit=2)) == 2


def test_read_rejects_unknown_kind_filter(conn):
    from app.events import EventError, read

    with pytest.raises(EventError, match="unknown event kinds"):
        read(conn, since=0, kinds=["nope"])


def test_snapshot_is_ascending_and_paginates(conn):
    from app import events

    ids = [emit(conn, kind="message.bot", payload={"text": str(i)}).id for i in range(6)]
    conn.commit()

    page1 = events.snapshot(conn, "ses_1", limit=4)
    assert [e["id"] for e in page1["events"]] == ids[2:]
    assert page1["has_more"] is True
    assert page1["cursor"] == ids[-1]

    page2 = events.snapshot(conn, "ses_1", limit=4, before_id=page1["events"][0]["id"])
    assert [e["id"] for e in page2["events"]] == ids[:2]
    assert page2["has_more"] is False


def test_read_filters_by_session_and_bot(conn):
    from app import events

    emit(conn, kind="message.bot", payload={"text": "a"}, session_id="ses_1", bot_id="bot_1")
    emit(conn, kind="message.bot", payload={"text": "b"}, session_id="ses_2", bot_id="bot_2")
    emit(conn, kind="message.bot", payload={"text": "c"}, session_id="ses_1", bot_id="bot_2")
    conn.commit()

    only_s1 = events.read(conn, since=0, session_id="ses_1")
    assert [e.payload["text"] for e in only_s1] == ["a", "c"]

    only_b2 = events.read(conn, since=0, bot_id="bot_2")
    assert [e.payload["text"] for e in only_b2] == ["b", "c"]


# ---------------------------------------------------------------- 广播器
def test_broadcaster_publish_and_drain():
    from app.events import Broadcaster, Event

    b = Broadcaster(queue_size=3)
    sid = b.subscribe()

    for i in range(3):
        b.publish(Event(id=i + 1, ts="t", bot_id="bot", session_id=None,
                        kind="message.bot", origin="cloud", actor={}, turn_id=None,
                        refs={}, payload={"text": str(i)}))

    items, dropped = b.drain(sid)
    assert [it["id"] for it in items] == [1, 2, 3]
    assert dropped is False

    # 队列满后继续发布：丢最旧的，且 dropped 标记可被感知
    for i in range(5):
        b.publish(Event(id=10 + i, ts="t", bot_id="bot", session_id=None,
                        kind="message.bot", origin="cloud", actor={}, turn_id=None,
                        refs={}, payload={"text": str(i)}))
    items, dropped = b.drain(sid)
    assert len(items) == 3
    assert dropped is True

    b.unsubscribe(sid)
    assert b.subscriber_count() == 0


def test_event_roundtrip_shape(conn):
    """事件序列化后必须能还原成契约里的信封字段。"""
    ev = emit(conn, kind="tool.call", payload={"tool": "exec", "call_id": "c1"},
              turn_id="turn_1", refs={"job_id": "job_1"})
    conn.commit()

    d = ev.to_dict()
    for key in ("id", "ts", "bot_id", "session_id", "kind", "origin",
                "actor", "turn_id", "refs", "payload"):
        assert key in d
    assert d["refs"]["job_id"] == "job_1"
    assert d["payload"]["tool"] == "exec"
