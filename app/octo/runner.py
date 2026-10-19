"""Event loop: Octo messages -> shell tasks -> replies, with the loop guard in front."""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
import time

from ..db import connect
from . import guard, tools
from .client import OctoBotClient
from . import intent as intent_mod
from .inbox import MysqlInbox

ROOT_HINT = os.environ.get("OA_BASE", "http://127.0.0.1:8077")
log = logging.getLogger("octo.runner")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))


def _shell_client(identity_id: str, secret: str):
    from oa_client import OAClient          # tools/ on sys.path
    return OAClient(ROOT_HINT, identity_id, secret)


def _text_of(payload: dict) -> str:
    if isinstance(payload, dict):
        if payload.get("type") == 1:
            return str(payload.get("content") or "")
        return str(payload.get("content") or payload.get("text") or "")
    return str(payload or "")


class OctoRunner(threading.Thread):
    daemon = True

    def __init__(self, bot_uid: str, interval: float = 1.0) -> None:
        super().__init__(name=f"octo-runner-{bot_uid}")
        self.bot_uid = bot_uid
        self.interval = interval
        self.stop_event = threading.Event()
        self.last_error: str | None = None
        self.handled = 0
        self.dropped = 0
        self.chatted = 0
        self.worked = 0

    # ------------------------------------------------------------------ loop
    def run(self) -> None:                       # pragma: no cover - thread body
        log.info("octo runner started for %s", self.bot_uid)
        client = None
        mode = os.environ.get("OCTO_INBOX", "db")
        inbox = MysqlInbox(self.bot_uid) if mode == "db" else None
        while not self.stop_event.is_set():
            try:
                if client is None:
                    with connect() as conn:
                        client = tools.bot_client(conn, self.bot_uid)
                if inbox is not None:
                    for msg in inbox.fetch():
                        self.handle_event({"message": msg}, client)
                else:
                    for ev in client.poll(5_000) or []:
                        try:
                            self.handle_event(ev, client)
                        finally:
                            if ev.get("event_id") is not None:
                                client.ack(ev.get("event_id"))
                self.last_error = None
            except Exception as exc:             # noqa: BLE001 - keep polling
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("octo poll failed: %s", self.last_error)
                self.stop_event.wait(3)
            self.stop_event.wait(self.interval)
        log.info("octo runner stopped")

    def stop(self) -> None:
        self.stop_event.set()

    # ------------------------------------------------------------------ events
    def handle_event(self, ev: dict, client: OctoBotClient) -> None:
        msg = ev.get("message") or {}
        from_uid = msg.get("from_uid") or ""
        mid = msg.get("message_id")
        text = _text_of(msg.get("payload") or {})
        channel_id = msg.get("channel_id") or from_uid
        channel_type = int(msg.get("channel_type") or 1)
        if not text or from_uid == self.bot_uid:
            return

        with connect() as conn:
            if not self._first_sight(conn, mid):
                return
            row = conn.execute("SELECT * FROM octo_bots WHERE bot_uid = ?",
                               (self.bot_uid,)).fetchone()
            if row is None:
                return
            mentions = self.bot_uid.split("_")[0][:6] in text or f"@{row['display_name']}" in text
            is_bot = conn.execute("SELECT 1 FROM octo_bots WHERE bot_uid = ?",
                                  (from_uid,)).fetchone() is not None
            ok, why = guard.chain_allows(conn, channel_id, self.bot_uid, from_uid, mentions,
                                         latest_is_bot=is_bot)
            if not ok:
                self.dropped += 1
                log.info("dropped (%s) from=%s channel=%s", why, from_uid, channel_id)
                return
            if guard.is_muted(conn, self.bot_uid):
                return
            if not guard.note_call(conn, self.bot_uid):
                client.send(channel_id, channel_type,
                            f"{row['display_name']} 的调用预算已用完，静默 1 小时。")
                return

            if is_bot:
                # agent-to-agent: answer briefly, never start a task
                client.send(channel_id, channel_type,
                            f"收到 @{from_uid}，我按分工继续，细节回帖到本群。")
                guard.note_reply(conn, channel_id, self.bot_uid)
                self.handled += 1
                return

            ident = conn.execute("SELECT id, secret FROM identities WHERE id = ?",
                                 (row["identity_id"],)).fetchone()
            if ident is None:
                client.send(channel_id, channel_type, "这个机器人还没绑定 OA 身份。")
                return

        # Chat or work? Without this every "你好" became a task with criteria.
        verdict = intent_mod.classify(text)
        if verdict["intent"] == "chat":
            client.send(channel_id, channel_type, verdict["reply"])
            with connect() as conn:
                guard.note_reply(conn, channel_id, self.bot_uid)
            self.chatted = getattr(self, "chatted", 0) + 1
            self.handled += 1
            return

        client.send(channel_id, channel_type, verdict["reply"])
        self.worked = getattr(self, "worked", 0) + 1
        try:
            api = _shell_client(ident["id"], ident["secret"])
            r = api.post("/tasks/from_sentence", json={"text": text})
            if r.status_code >= 400:
                client.send(channel_id, channel_type, f"建任务失败：{r.status_code}")
                return
            task = r.json()
            if os.environ.get("OCTO_AUTORUN", "1") == "1":
                threading.Thread(target=self._run_task, args=(ident, task["task_id"], channel_id,
                                                              channel_type, client),
                                 daemon=True).start()
            client.send(channel_id, channel_type,
                        f"任务 {task['task_id']} 已建（{task['pack_id']}，"
                        f"{len(task['criteria'])} 条判据），开工后把结果发这里。")
            with connect() as conn:
                guard.note_reply(conn, channel_id, self.bot_uid)
            self.handled += 1
        except Exception as exc:                 # noqa: BLE001
            client.send(channel_id, channel_type, f"底座调用失败：{exc}")

    # ------------------------------------------------------------------ task
    def _run_task(self, ident, task_id: str, channel_id: str, channel_type: int,
                  client: OctoBotClient) -> None:
        try:
            api = _shell_client(ident["id"], ident["secret"])
            run = api.post("/verbs/spawn_run", json={"task_id": task_id, "run_seq": 1}).json()
            out = api.post(f"/runs/{run['run_id']}/execute",
                           json={"runner": os.environ.get("OCTO_RUNNER_ENGINE", "acp"),
                                 "budget_tokens": int(os.environ.get("OCTO_TASK_BUDGET", "30000"))
                                 }).json()
            acc = api.post(f"/tasks/{task_id}/acceptance/run").json()
            arts = out.get("artifacts") or []
            lines = [f"{task_id} 执行完成（{out.get('usage_tokens', 0)} tokens）",
                     f"验收：{acc.get('verdict')} / {acc.get('task_state')}"]
            for a in arts[:3]:
                lines.append(f"· 产出 {os.path.basename(a.get('file_path', ''))} "
                             f"{a.get('bytes', '')}B")
            for res in acc.get("results", [])[:4]:
                if res.get("state") == "failed":
                    lines.append(f"✗ {res.get('detail')}")
            client.send(channel_id, channel_type, "\n".join(lines))
        except Exception as exc:                 # noqa: BLE001
            try:
                client.send(channel_id, channel_type, f"{task_id} 执行异常：{exc}")
            except Exception:                    # noqa: BLE001
                pass

    # ------------------------------------------------------------------ dedupe
    def _first_sight(self, conn: sqlite3.Connection, message_id) -> bool:
        if message_id is None:
            return True
        cur = conn.execute("INSERT OR IGNORE INTO octo_seen (message_id) VALUES (?)",
                           (str(message_id),))
        conn.commit()
        return cur.rowcount == 1


_runner: OctoRunner | None = None


def start(bot_uid: str | None = None) -> OctoRunner:
    global _runner
    uid = bot_uid or os.environ.get("OCTO_BOT_UID", "")
    if not uid:
        raise RuntimeError("OCTO_BOT_UID is not set")
    if _runner is None or not _runner.is_alive():
        _runner = OctoRunner(uid)
        _runner.start()
    return _runner


def stop() -> None:
    global _runner
    if _runner is not None:
        _runner.stop()
        _runner = None


def status() -> dict:
    r = _runner
    return {"running": bool(r and r.is_alive()),
            "bot_uid": getattr(r, "bot_uid", None),
            "handled": getattr(r, "handled", 0),
            "dropped": getattr(r, "dropped", 0),
            "chatted": getattr(r, "chatted", 0),
            "worked": getattr(r, "worked", 0),
            "last_error": getattr(r, "last_error", None)}
