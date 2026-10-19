"""Read new Octo messages straight from MySQL.

Why not the Bot API's event queue: in this OSS build nothing feeds it. The only
writers are `bot_joined_group` and the internal `/v1/internal/bot-mentions`
ingress, and no shipped service calls that ingress (matter only does
`/v1/internal/notify`). So a bot that relies on `/v1/bot/events` sits there
forever. Messages themselves are stored as plain JSON in `message*`, so the
bridge tails those tables instead and keeps a per-shard cursor.

Set OCTO_INBOX=db to use this path (default), OCTO_INBOX=events for the Bot API.
"""
from __future__ import annotations

import json
import os
import threading
import time

import pymysql

SHARDS = ("message", "message1", "message2", "message3", "message4")
SYSTEM_UIDS = {"botfather", "notification", "system"}


def _conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.environ.get("OCTO_MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("OCTO_MYSQL_PORT", "23306")),
        user=os.environ.get("OCTO_MYSQL_USER", "root"),
        password=os.environ.get("OCTO_MYSQL_PASSWORD", ""),
        database=os.environ.get("OCTO_MYSQL_DB", "octo"),
        charset="utf8mb4", autocommit=True, cursorclass=pymysql.cursors.DictCursor)


class MysqlInbox:
    """Tail the message shards and hand back text messages, oldest first."""

    def __init__(self, bot_uid: str) -> None:
        self.bot_uid = bot_uid
        self.cursors: dict[str, int] = {}
        self._groups: set[str] = set()
        self._groups_at = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ setup
    def prime(self) -> None:
        """Start from 'now' so history is not replayed as fresh work."""
        with _conn() as c, c.cursor() as cur:
            for t in SHARDS:
                cur.execute(f"SELECT COALESCE(MAX(id), 0) AS m FROM `{t}`")
                self.cursors[t] = int(cur.fetchone()["m"] or 0)

    def groups_of_bot(self, force: bool = False) -> set[str]:
        if not force and time.time() - self._groups_at < 30:
            return self._groups
        with _conn() as c, c.cursor() as cur:
            cur.execute("SELECT group_no FROM group_member WHERE uid = %s", (self.bot_uid,))
            self._groups = {r["group_no"] for r in cur.fetchall()}
        self._groups_at = time.time()
        return self._groups

    # ------------------------------------------------------------------ fetch
    def fetch(self, limit: int = 50) -> list[dict]:
        if not self.cursors:
            self.prime()
        groups = self.groups_of_bot()
        out: list[dict] = []
        with _conn() as c, c.cursor() as cur:
            for t in SHARDS:
                cur.execute(
                    f"""SELECT id, message_id, from_uid, channel_id, channel_type, payload
                        FROM `{t}` WHERE id > %s ORDER BY id ASC LIMIT %s""",
                    (self.cursors[t], limit))
                rows = cur.fetchall()
                if not rows:
                    continue
                self.cursors[t] = rows[-1]["id"]
                for r in rows:
                    if r["from_uid"] == self.bot_uid or r["from_uid"] in SYSTEM_UIDS:
                        continue
                    ctype = int(r["channel_type"] or 0)
                    if ctype == 2 and r["channel_id"] not in groups:
                        # A group created seconds ago is not in the 30s cache yet;
                        # refresh once before deciding this channel is foreign.
                        groups = self.groups_of_bot(force=True)
                        if r["channel_id"] not in groups:
                            continue
                    if ctype == 1 and self.bot_uid not in (
                            r["channel_id"] or "") and not str(r["channel_id"]).endswith(
                                self.bot_uid):
                        continue
                    payload = r["payload"]
                    if isinstance(payload, (bytes, bytearray)):
                        payload = payload.decode("utf-8", "replace")
                    try:
                        data = json.loads(payload)
                    except Exception:                # noqa: BLE001 - non-JSON payloads
                        continue
                    if int(data.get("type") or 0) != 1:
                        continue
                    text = str(data.get("content") or "").strip()
                    if not text:
                        continue
                    out.append({"message_id": r["message_id"], "from_uid": r["from_uid"],
                                "channel_id": r["channel_id"], "channel_type": ctype,
                                "payload": {"type": 1, "content": text}})
        out.sort(key=lambda m: m["message_id"])
        return out
