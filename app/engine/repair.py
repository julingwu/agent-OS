"""Automatic length repair.

The model reliably writes ~15–25% fewer characters than asked (DESIGN_GAPS §47),
so a word-count criterion rejects work that is otherwise fine. Instead of
handing that to a human, the shell spends one more cheap turn: resume the same
session and tell it the exact number.

    run -> acceptance -> word_count failed -> repair run (max 2) -> acceptance

If two repairs still miss, the task is rejected for real — the human sees it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3

from .. import acceptance_run
from ..audit import write_audit

log = logging.getLogger("engine.repair")

MAX_ATTEMPTS = int(os.environ.get("REPAIR_MAX_ATTEMPTS", "2"))


def _word_rule(criteria: list[sqlite3.Row]) -> tuple[int, int] | None:
    for c in criteria:
        ref = c["check_ref"] or ""
        if "word_count.py" not in ref:
            continue
        try:
            cfg = json.loads(ref)
        except ValueError:
            continue
        return int(cfg.get("min", 0)), int(cfg.get("max", 10**9))
    return None


def _actual_words(conn: sqlite3.Connection, task_id: str) -> int | None:
    row = conn.execute(
        """SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id = a.run_id
           WHERE r.task_id = ? ORDER BY a.created_at DESC LIMIT 1""", (task_id,)).fetchone()
    if not row:
        return None
    try:
        text = open(row["file_path"], encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk + words


def maybe_repair(conn: sqlite3.Connection, task_id: str, ident: str | None = None) -> dict:
    """Run up to MAX_ATTEMPTS repair turns; returns what happened."""
    from ..engine import dsh_adapter

    out = {"attempts": 0, "repaired": False, "reason": ""}
    # 字数自愈只对内容包有意义；代码/UI 任务触发它只会把状态机搅乱
    # v1.5：取消字数自动重试（浪费 token 且把 900 字的好稿改成 1300 字的注水稿）
    out["reason"] = "auto-repair disabled (v1.5)"
    return out

    row = conn.execute("SELECT pack_id, state FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row and (row["state"] or "") in ("failed", "aborted"):
        out["reason"] = f"任务状态 {row['state']}，不再自愈（避免状态机非法流转）"
        return out
    pack = row
    if not pack or (pack["pack_id"] or "") != "content-v1":
        out["reason"] = f"pack={(pack['pack_id'] if pack else '?')} 非 content-v1，跳过字数修复"
        return out
    for attempt in range(1, MAX_ATTEMPTS + 1):
        criteria = conn.execute(
            "SELECT * FROM acceptance_criteria WHERE task_id=? ORDER BY seq", (task_id,)).fetchall()
        rule = _word_rule(criteria)
        if rule is None:
            out["reason"] = "no word-count criterion"
            return out
        lo, hi = rule
        words = _actual_words(conn, task_id)
        if words is None:
            out["reason"] = "no artifact"
            return out
        if lo <= words <= hi:
            out["repaired"] = True
            out["reason"] = f"{words} 字已达标"
            return out

        target = (lo + hi) // 2 if hi < 10**8 else int(lo * 1.2)
        prompt = (f"上一版正文只有 {words} 字，要求 {lo}-{hi} 字，未达标。\n"
                  f"请精确扩写到约 {target} 字（务必落在 {lo}-{hi} 之间），保持原有结构与观点，"
                  f"只做扩写和补充细节。覆盖写原文件，完成后只回复 DONE。")
        run_id = "RUN-" + os.urandom(4).hex()
        seq = (conn.execute("SELECT COALESCE(MAX(run_seq),0)+1 n FROM task_runs WHERE task_id=?",
                            (task_id,)).fetchone()["n"])
        entity = conn.execute("SELECT legal_entity_id FROM tasks WHERE id=?",
                              (task_id,)).fetchone()["legal_entity_id"]
        prev = conn.execute(
            """SELECT session_id FROM task_runs WHERE task_id=?
               ORDER BY rowid DESC LIMIT 1""", (task_id,)).fetchone()
        conn.execute(
            """INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id, state, runner,
                                      actor_identity, session_id)
               VALUES (?,?,?,?, 'spawned', 'acp', ?, ?)""",
            (run_id, task_id, seq, entity, ident, prev["session_id"] if prev else None))
        conn.commit()
        write_audit(conn, legal_entity_id=entity, verb="repair", task_id=task_id,
                    run_id=run_id, layer="system", action="repair_attempt", target=run_id,
                    payload={"attempt": attempt, "words": words, "target": target,
                             "range": [lo, hi]},
                    response={"prompt": prompt[:200]})
        conn.commit()

        out["attempts"] = attempt
        log.info("repair %s attempt %s: %s words -> target %s", task_id, attempt, words, target)
        try:
            want = int(os.environ.get("REPAIR_BUDGET", "30000"))
            pool = conn.execute(
                """SELECT limit_tokens, used_tokens FROM budgets
                   WHERE legal_entity_id=? AND task_id IS NULL AND identity_id IS NULL
                   ORDER BY rowid DESC LIMIT 1""", (entity,)).fetchone()
            if pool and pool["limit_tokens"]:
                want = max(3000, min(want, int(pool["limit_tokens"]) - int(pool["used_tokens"] or 0)))
            ex = dsh_adapter.execute_run(
                conn, run_id, runner="acp", prompt=prompt,
                resume_session=prev["session_id"] if prev else None,
                budget_tokens=want)
        except Exception as exc:                 # noqa: BLE001
            out["reason"] = f"repair run failed: {exc}"
            return out

        acc = acceptance_run.run(conn, task_id, [entity])
        words2 = _actual_words(conn, task_id)
        write_audit(conn, legal_entity_id=entity, verb="repair", task_id=task_id,
                    run_id=run_id, layer="system", action="repair_result", target=run_id,
                    payload={"attempt": attempt, "words_after": words2,
                             "verdict": acc.get("verdict")},
                    response={"state": acc.get("task_state")})
        conn.commit()
        if acc.get("verdict") in ("pending", "completed", "passed"):
            out["repaired"] = True
            out["reason"] = f"第 {attempt} 次扩写后 {words2} 字，{acc.get('verdict')}"
            return out
        if attempt == MAX_ATTEMPTS:
            out["reason"] = f"扩写 {attempt} 次后仍 {words2} 字，交人工"
    return out
