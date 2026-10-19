"""Loop guard + per-bot budget.

Two bots in one group will happily thank each other forever, and every turn costs
tokens. Rules, in order:

1. A bot never answers another bot unless it was explicitly addressed (@name) —
   and even then only MAX_CHAIN replies per channel/window, spaced MIN_GAP apart.
2. Every bot has a call budget. Once spent, it is muted for MUTE_SECONDS and says
   so once; after that it stays silent.
3. The bridge's own messages are never re-ingested (from_uid / message id check).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

def _max_chain() -> int:
    return int(os.environ.get("OCTO_MAX_BOT_CHAIN", "3"))


def _window() -> int:
    return int(os.environ.get("OCTO_CHAIN_WINDOW", "600"))


def _mute_seconds() -> int:
    return int(os.environ.get("OCTO_MUTE_SECONDS", "3600"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def is_muted(conn: sqlite3.Connection, bot_uid: str) -> bool:
    row = conn.execute("SELECT muted_until FROM octo_bots WHERE bot_uid = ?", (bot_uid,)).fetchone()
    if row is None or not row["muted_until"]:
        return False
    return str(row["muted_until"]) > _fmt(_utcnow())


def note_call(conn: sqlite3.Connection, bot_uid: str) -> bool:
    """Spend one call from the bot's budget; returns False when it just went mute."""
    conn.execute("UPDATE octo_bots SET used_calls = used_calls + 1 WHERE bot_uid = ?", (bot_uid,))
    conn.commit()
    row = conn.execute("SELECT used_calls, budget_calls, muted_until FROM octo_bots"
                       " WHERE bot_uid = ?", (bot_uid,)).fetchone()
    if row is None:
        return True
    if row["budget_calls"] and row["used_calls"] > row["budget_calls"]:
        until = _fmt(_utcnow() + timedelta(seconds=_mute_seconds()))
        conn.execute("UPDATE octo_bots SET muted_until = ? WHERE bot_uid = ?", (until, bot_uid))
        conn.commit()
        return False
    return True


def chain_allows(conn: sqlite3.Connection, channel_id: str, bot_uid: str,
                 latest_from: str, mentions_bot: bool,
                 latest_is_bot: bool | None = None) -> tuple[bool, str]:
    """Can `bot_uid` answer the latest message in this channel?

    `latest_is_bot` should come from the caller's own registry: the `_bot` uid
    suffix is not a reliable test (demo bots are called `fe_bot_demo`).
    """
    row = conn.execute("SELECT * FROM octo_chains WHERE channel_id = ?",
                       (channel_id,)).fetchone()
    now = _utcnow()
    if row is None:
        conn.execute("""INSERT INTO octo_chains (channel_id, last_from, bot_replies, window_start)
                        VALUES (?,?,0,?)""", (channel_id, latest_from, _fmt(now)))
        conn.commit()
        row = conn.execute("SELECT * FROM octo_chains WHERE channel_id = ?",
                           (channel_id,)).fetchone()

    started = row["window_start"] or _fmt(now)
    age = (now - datetime.strptime(started, "%Y-%m-%d %H:%M:%S")).total_seconds()
    if age > _window():
        conn.execute("UPDATE octo_chains SET bot_replies = 0, window_start = ? WHERE channel_id = ?",
                     (_fmt(now), channel_id))
        conn.commit()
        row = conn.execute("SELECT * FROM octo_chains WHERE channel_id = ?",
                           (channel_id,)).fetchone()

    if latest_is_bot is None:
        latest_is_bot = latest_from != bot_uid and latest_from.endswith("_bot")

    # a human message resets the chain
    if not latest_is_bot:
        conn.execute("UPDATE octo_chains SET bot_replies = 0, last_from = ?, window_start = ?"
                     " WHERE channel_id = ?", (latest_from, _fmt(now), channel_id))
        conn.commit()
        return True, "human"

    if not mentions_bot:
        return False, "bot-to-bot without mention"
    if row["bot_replies"] >= _max_chain():
        return False, f"chain limit {_max_chain()}/{_window()}s"
    return True, "mentioned"


def note_reply(conn: sqlite3.Connection, channel_id: str, bot_uid: str) -> None:
    conn.execute("""UPDATE octo_chains SET bot_replies = bot_replies + 1, last_from = ?
                    WHERE channel_id = ?""", (bot_uid, channel_id))
    conn.commit()


def reset_chains(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM octo_chains")
    conn.commit()
