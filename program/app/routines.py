"""例行任务：让 Bot 按计划自己干活（v1.8）。

设计取舍（第一性原理）：
  * 调度表达式只支持两种：`interval:30m / interval:2h` 与 `daily:09:00`。
    通用 cron 要写解析器、要处理一堆边界，收益不匹配——"每小时巡检 / 每天早报"覆盖了真实需求。
  * 时间用服务器本地时区（任务本来就是让服务器干），落库带偏移，排查时不用猜。
  * 执行不伪造 `message.user`：先发一条 `message.system`（用户看得见这是例行任务），
    再用 `task_hint` 起一轮。伪造用户发言会污染上下文，也会被记忆抽取当成用户说的。
  * 单进程单线程调度器，20 秒扫一次；每个任务加锁，避免同一任务重入。
  * pytest 下绝不启动线程（env 门 + PYTEST_CURRENT_TEST），否则测试会互相污染。
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime, timedelta

from . import db
from . import events as ev

MAX_TITLE = 120
MAX_PROMPT = 4000

_READY_DBS: set[str] = set()          # 按 DB 路径缓存建表结果（测试每个用例一个新库）
_LOCK = threading.RLock()
_RUNNING: set[str] = set()
_SCHEDULER: threading.Thread | None = None
_STOP = threading.Event()

_INTERVAL_RE = re.compile(r"^interval:(\d{1,4})\s*([mh]?)$", re.I)
_DAILY_RE = re.compile(r"^daily:(\d{1,2}):(\d{2})$")


def ensure(conn) -> None:
    with _LOCK:
        if db.db_path() in _READY_DBS:
            return
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS routines (
                id           TEXT PRIMARY KEY,
                bot_id       TEXT NOT NULL,
                session_id   TEXT NOT NULL,
                title        TEXT NOT NULL,
                prompt       TEXT NOT NULL,
                schedule     TEXT NOT NULL,
                enabled      INTEGER NOT NULL DEFAULT 1,
                next_run_at  TEXT,
                last_run_at  TEXT,
                last_status  TEXT,
                run_count    INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_routines_next"
            " ON routines(enabled, next_run_at)"
        )
        conn.commit()
        _READY_DBS.add(db.db_path())


# ---------------------------------------------------------------- 调度表达式
def parse_schedule(spec: str) -> tuple[str, int | tuple[int, int]]:
    """返回 (kind, value)。不合法直接抛 ValueError —— 建任务时就要报错，别等到跑的时候静默不跑。"""
    text = (spec or "").strip()
    m = _INTERVAL_RE.match(text)
    if m:
        amount = int(m.group(1))
        unit = (m.group(2) or "m").lower()
        minutes = amount * 60 if unit == "h" else amount
        if minutes < 1:
            raise ValueError("interval 至少 1 分钟")
        return "interval", minutes
    m = _DAILY_RE.match(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("daily 时间非法，示例：daily:09:00")
        return "daily", (hour, minute)
    raise ValueError("schedule 只支持 interval:30m / interval:2h / daily:09:00")


def next_run_at(spec: str, after: datetime | None = None) -> datetime:
    now = after or datetime.now().astimezone()
    kind, value = parse_schedule(spec)
    if kind == "interval":
        return now + timedelta(minutes=int(value))
    hour, minute = value  # type: ignore[misc]
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def describe_schedule(spec: str) -> str:
    kind, value = parse_schedule(spec)
    if kind == "interval":
        minutes = int(value)
        if minutes % 60 == 0:
            return f"每 {minutes // 60} 小时"
        return f"每 {minutes} 分钟"
    hour, minute = value  # type: ignore[misc]
    return f"每天 {hour:02d}:{minute:02d}"


# ---------------------------------------------------------------- CRUD
def _row_to_dict(row) -> dict:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    try:
        item["schedule_human"] = describe_schedule(item["schedule"])
    except ValueError:
        item["schedule_human"] = item["schedule"]
    return item


def create(conn, *, bot_id: str, session_id: str, title: str, prompt: str,
           schedule: str, enabled: bool = True) -> dict:
    ensure(conn)
    title = (title or "").strip()[:MAX_TITLE]
    prompt = (prompt or "").strip()[:MAX_PROMPT]
    if not title or not prompt:
        raise ValueError("title 和 prompt 都不能为空")
    if not bot_id or not session_id:
        raise ValueError("bot_id 和 session_id 必填")
    next_at = next_run_at(schedule).isoformat(timespec="seconds") if enabled else None
    rid = "rtn_" + secrets.token_hex(8)
    conn.execute(
        "INSERT INTO routines (id, bot_id, session_id, title, prompt, schedule,"
        " enabled, next_run_at, run_count, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,0,?)",
        (rid, bot_id, session_id, title, prompt, schedule,
         1 if enabled else 0, next_at, ev.now_iso()),
    )
    conn.commit()
    return get(conn, rid)


def get(conn, routine_id: str) -> dict | None:
    ensure(conn)
    row = conn.execute("SELECT * FROM routines WHERE id=?", (routine_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_routines(conn, *, bot_id: str | None = None, limit: int = 100) -> list[dict]:
    ensure(conn)
    if bot_id:
        rows = conn.execute(
            "SELECT * FROM routines WHERE bot_id=? ORDER BY created_at DESC LIMIT ?",
            (bot_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM routines ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def update(conn, routine_id: str, *, title: str | None = None,
           prompt: str | None = None, schedule: str | None = None,
           enabled: bool | None = None) -> dict | None:
    ensure(conn)
    current = get(conn, routine_id)
    if current is None:
        return None
    fields: dict = {}
    if title is not None:
        fields["title"] = title.strip()[:MAX_TITLE]
    if prompt is not None:
        fields["prompt"] = prompt.strip()[:MAX_PROMPT]
    if schedule is not None:
        parse_schedule(schedule)                      # 先校验，别写进脏数据
        fields["schedule"] = schedule
    if enabled is not None:
        fields["enabled"] = 1 if enabled else 0
        fields["next_run_at"] = (next_run_at(schedule or current["schedule"])
                                 .isoformat(timespec="seconds") if enabled else None)
    if not fields:
        return current
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE routines SET {assignments} WHERE id=?",
                 (*fields.values(), routine_id))
    conn.commit()
    return get(conn, routine_id)


def delete(conn, routine_id: str) -> bool:
    ensure(conn)
    cur = conn.execute("DELETE FROM routines WHERE id=?", (routine_id,))
    conn.commit()
    return cur.rowcount > 0


def due(conn, now: datetime | None = None) -> list[dict]:
    ensure(conn)
    moment = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT * FROM routines WHERE enabled=1 AND next_run_at IS NOT NULL"
        " AND next_run_at <= ? ORDER BY next_run_at LIMIT 5", (moment,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def _mark_ran(routine_id: str, status: str) -> None:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM routines WHERE id=?", (routine_id,)).fetchone()
        if row is None:
            return
        now = datetime.now().astimezone()
        try:
            nxt = next_run_at(row["schedule"], now).isoformat(timespec="seconds")
        except ValueError:
            nxt = None
        conn.execute(
            "UPDATE routines SET last_run_at=?, last_status=?, run_count=run_count+1,"
            " next_run_at=? WHERE id=?",
            (now.isoformat(timespec="seconds"), status, nxt, routine_id),
        )
        conn.commit()


# ---------------------------------------------------------------- 执行
def execute(routine: dict) -> dict:
    """跑一次例行任务。返回 {status, routine_id}。异常不外抛（调度线程不能被一条任务带死）。"""
    from .agent import loop as agent_loop

    rid = routine["id"]
    bot_id, session_id = routine["bot_id"], routine["session_id"]
    status = "done"
    try:
        with db.get_conn() as conn:
            ev.emit(conn, bot_id=bot_id, session_id=session_id,
                    kind="routine.triggered",
                    payload={"routine_id": rid, "title": routine["title"],
                             "schedule": routine["schedule"]})
            event = ev.emit(conn, bot_id=bot_id, session_id=session_id,
                            kind="message.system",
                            payload={"text": f"【例行任务】{routine['title']}"})
            conn.commit()
        ev.broadcaster.publish(event)

        result = agent_loop.run_turn(
            bot_id, session_id,
            task_hint=f"（这是例行任务「{routine['title']}」的触发，按下面要求执行）\n{routine['prompt']}")
        status = result.get("status") or "done"
    except Exception as exc:                          # noqa: BLE001 - 单条任务失败不能拖垮调度器
        status = "failed"
        try:
            with db.get_conn() as conn:
                ev.emit(conn, bot_id=bot_id, session_id=session_id, kind="error",
                        payload={"code": "ROUTINE_FAILED",
                                 "message": f"{routine['title']}: {exc}"[:300],
                                 "retriable": True})
                conn.commit()
        except Exception:                             # noqa: BLE001
            pass
    finally:
        try:
            with db.get_conn() as conn:
                ev.emit(conn, bot_id=bot_id, session_id=session_id,
                        kind="routine.finished",
                        payload={"routine_id": rid, "status": status})
                conn.commit()
        except Exception:                             # noqa: BLE001
            pass
        _mark_ran(rid, status)
        with _LOCK:
            _RUNNING.discard(rid)
    return {"routine_id": rid, "status": status}


def run_now(routine_id: str) -> dict:
    """手动触发（API 用）。后台线程跑，别卡住请求。"""
    with db.get_conn() as conn:
        routine = get(conn, routine_id)
    if routine is None:
        return {"status": "not_found"}
    _spawn(routine)
    return {"status": "started", "routine_id": routine_id}


def _spawn(routine: dict) -> None:
    rid = routine["id"]
    with _LOCK:
        if rid in _RUNNING:
            return
        _RUNNING.add(rid)
    threading.Thread(target=execute, args=(routine,),
                     name=f"routine-{rid[-6:]}", daemon=True).start()


def run_due_once(now: datetime | None = None) -> list[dict]:
    """扫描并触发到期任务（同步执行，测试用；线上由调度线程调 _spawn 版本）。"""
    with db.get_conn() as conn:
        items = due(conn, now)
    return [execute(r) for r in items]


# ---------------------------------------------------------------- 调度线程
def _loop(interval: float) -> None:
    while not _STOP.is_set():
        try:
            with db.get_conn() as conn:
                items = due(conn)
            for routine in items:
                _spawn(routine)
        except Exception as exc:                      # noqa: BLE001 - 调度器自身不能死
            print(f"[routine] scheduler tick failed: {exc}")
        _STOP.wait(interval)


def start_scheduler(interval: float = 20.0) -> None:
    global _SCHEDULER
    with _LOCK:
        if _SCHEDULER is not None and _SCHEDULER.is_alive():
            return
        _STOP.clear()
        _SCHEDULER = threading.Thread(target=_loop, args=(interval,),
                                      name="routine-scheduler", daemon=True)
        _SCHEDULER.start()


def stop_scheduler() -> None:
    _STOP.set()
