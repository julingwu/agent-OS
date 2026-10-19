"""Octo bridge endpoints: status, demo, mute, scoped artifacts."""
from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse

from ..db import get_conn
from . import agent, guard, runner, tools

router = APIRouter(tags=["octo"], prefix="/octo")


def _guard_token(x_octo_token: str | None = Header(default=None)) -> None:
    if (x_octo_token or "") != os.environ.get("OCTO_SIM_TOKEN", "dev-token"):
        raise HTTPException(401, detail={"code": "BadOctoToken"})


@router.get("/status")
def status(x_octo_token: str | None = Header(default=None)):
    _guard_token(x_octo_token)
    with get_conn() as conn:
        bots = [dict(r) for r in conn.execute(
            """SELECT bot_uid, display_name, role, identity_id, budget_calls, used_calls,
                      muted_until, state FROM octo_bots ORDER BY role""")]
        seen = conn.execute("SELECT COUNT(*) FROM octo_seen").fetchone()[0]
        chains = [dict(r) for r in conn.execute("SELECT * FROM octo_chains")]
    return {"runner": runner.status(), "bots": bots, "events_seen": seen,
            "chains": chains, "api": os.environ.get("OCTO_API_URL", ""),
            "autonomous": os.environ.get("OCTO_AUTORUN", "1") == "1"}


@router.post("/demo/ceo_group")
def demo(payload: dict, x_octo_token: str | None = Header(default=None)):
    """The demo the chairman asked for: the PM bot creates a group and pulls agents in."""
    _guard_token(x_octo_token)
    text = (payload.get("text") or "双十一内容投放：要一个落地页和一段营销文案").strip()
    human_uid = payload.get("human_uid") or os.environ.get("OCTO_HUMAN_UID", "")
    if not human_uid:
        raise HTTPException(400, detail={"code": "MissingHumanUid"})
    with get_conn() as conn:
        try:
            out = agent.dispatch(conn, text=text, human_uid=human_uid,
                                 group_name=payload.get("group_name"))
        except Exception as exc:                 # noqa: BLE001
            raise HTTPException(500, detail={"code": "DispatchFailed", "message": str(exc)})
    return out


@router.post("/mute/{bot_uid}")
def mute(bot_uid: str, payload: dict | None = None,
         x_octo_token: str | None = Header(default=None)):
    _guard_token(x_octo_token)
    minutes = int((payload or {}).get("minutes", 60))
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM octo_bots WHERE bot_uid = ?", (bot_uid,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "UnknownBot", "bot_uid": bot_uid})
        conn.execute("UPDATE octo_bots SET muted_until = datetime('now', ?) WHERE bot_uid = ?",
                     (f"+{minutes} minutes", bot_uid))
        conn.commit()
    return {"bot_uid": bot_uid, "muted_minutes": minutes}


@router.get("/artifacts")
def artifacts(bot_uid: str, x_octo_token: str | None = Header(default=None)):
    """Permission isolation: a bot only ever sees artifacts of its own identity's tasks."""
    _guard_token(x_octo_token)
    with get_conn() as conn:
        bot = conn.execute("SELECT * FROM octo_bots WHERE bot_uid = ?", (bot_uid,)).fetchone()
        if bot is None:
            raise HTTPException(404, detail={"code": "UnknownBot", "bot_uid": bot_uid})
        rows = [dict(r) for r in conn.execute(
            """SELECT a.file_path, a.bytes, t.id AS task_id, t.pack_id
               FROM artifacts a JOIN task_runs r ON r.id = a.run_id
               JOIN tasks t ON t.id = r.task_id
               WHERE r.actor_identity = ? ORDER BY a.created_at DESC LIMIT 20""",
            (bot["identity_id"],))]
    return {"bot_uid": bot_uid, "identity_id": bot["identity_id"], "count": len(rows),
            "artifacts": rows}


@router.post("/runner/start")
def runner_start(x_octo_token: str | None = Header(default=None)):
    _guard_token(x_octo_token)
    try:
        r = runner.start()
    except RuntimeError as exc:
        raise HTTPException(400, detail={"code": "RunnerUnavailable", "message": str(exc)})
    return {"started": True, "bot_uid": r.bot_uid}


@router.post("/runner/stop")
def runner_stop(x_octo_token: str | None = Header(default=None)):
    _guard_token(x_octo_token)
    runner.stop()
    return {"stopped": True}


@router.post("/chains/reset")
def chains_reset(x_octo_token: str | None = Header(default=None)):
    _guard_token(x_octo_token)
    with get_conn() as conn:
        guard.reset_chains(conn)
    return {"ok": True}


@router.get("/watch", response_class=HTMLResponse)
def watch(token: str = ""):
    """Live board for a running demo: refresh every 3s. Loopback only by design."""
    if token != os.environ.get("OCTO_SIM_TOKEN", "dev-token"):
        raise HTTPException(401, detail={"code": "BadOctoToken"})
    with get_conn() as conn:
        runs = conn.execute(
            """SELECT r.id, r.task_id, r.state, r.runner, r.usage_tokens, r.started_at,
                      r.ended_at, r.actor_identity, t.title
               FROM task_runs r LEFT JOIN tasks t ON t.id = r.task_id
               ORDER BY r.started_at DESC, r.rowid DESC LIMIT 12""").fetchall()
        tasks = conn.execute(
            """SELECT id, state, pack_id, substr(title,1,60) AS title, created_at
               FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT 8""").fetchall()
        pools = conn.execute(
            """SELECT legal_entity_id, limit_tokens, used_tokens, limit_amount, used_amount
               FROM budgets WHERE task_id IS NULL""").fetchall()
        chains = conn.execute("SELECT * FROM octo_chains").fetchall()

    st = runner.status()
    rows_run = "".join(
        f"<tr><td>{r['task_id']}</td><td>{r['state']}</td><td>{r['runner'] or '-'}</td>"
        f"<td class=n>{r['usage_tokens'] or 0}</td><td>{r['started_at'] or ''}</td>"
        f"<td>{r['ended_at'] or '…'}</td></tr>" for r in runs)
    rows_task = "".join(
        f"<tr><td>{t['id']}</td><td>{t['state']}</td><td>{t['pack_id']}</td>"
        f"<td>{t['title']}</td></tr>" for t in tasks)
    rows_pool = "".join(
        f"<tr><td>{p['legal_entity_id']}</td><td class=n>{p['used_tokens'] or 0}</td>"
        f"<td class=n>{p['limit_tokens']}</td><td class=n>{p['used_amount'] or 0}</td>"
        f"<td class=n>{p['limit_amount'] or 0}</td></tr>" for p in pools)
    rows_chain = "".join(
        f"<tr><td>{c['channel_id'][:12]}</td><td>{c['last_from']}</td>"
        f"<td class=n>{c['bot_replies']}</td><td>{c['window_start']}</td></tr>" for c in chains)
    engine = os.environ.get("OCTO_RUNNER_ENGINE", "mock")
    budget = os.environ.get("OCTO_TASK_BUDGET", "-")
    html = f"""<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=3>
<title>octo bridge · live</title>
<style>
 body{{background:#0f1115;color:#e6e6e6;font:13px/1.5 ui-monospace,Consolas,monospace;margin:18px}}
 h2{{font-size:14px;letter-spacing:.08em;text-transform:uppercase;color:#8ab4f8;margin:22px 0 6px}}
 table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid #262b36;padding:4px 8px;text-align:left}}
 th{{color:#7d8590;font-weight:500}} .n{{text-align:right}}
 .ok{{color:#5ddc8a}} .warn{{color:#f5c451}} .bad{{color:#ff6b6b}}
</style>
<h1 style="font-size:16px">OCTO BRIDGE · LIVE</h1>
<div>runner: <span class={'ok' if st['running'] else 'bad'}>{st['running']}</span>
 · bot {st['bot_uid']} · engine <span class=warn>{engine}</span> · budget/run <span class=warn>{budget}</span>
 · handled {st['handled']} / dropped {st['dropped']}
 · inbox {os.environ.get('OCTO_INBOX','db')} · err {st['last_error'] or '-'}</div>
<h2>runs</h2><table><tr><th>task</th><th>state</th><th>engine</th><th class=n>tokens</th><th>start</th><th>end</th></tr>{rows_run}</table>
<h2>tasks</h2><table><tr><th>id</th><th>state</th><th>pack</th><th>title</th></tr>{rows_task}</table>
<h2>budgets</h2><table><tr><th>entity</th><th class=n>used tok</th><th class=n>limit tok</th><th class=n>used ¥</th><th class=n>limit ¥</th></tr>{rows_pool}</table>
<h2>reply chains</h2><table><tr><th>channel</th><th>last from</th><th class=n>replies</th><th>window start</th></tr>{rows_chain}</table>
"""
    return HTMLResponse(html)
