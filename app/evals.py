"""离线评测与回归（v2.4）。

- eval_sets      一条基准用例 = 一次已完成 Run 的 task/prompt/产物/判据快照
- eval_runs      一次批量回归（可跨多条基准）
- eval_results   每条基准的新旧产出对比（词面相似度 + 可选大模型打分）

设计取舍：回归时用**当前** prompt 模板与**当前**员工模型重跑，而不是照抄旧 prompt；
否则改了模板/换了模型根本测不出来。旧 prompt 仍保存在 eval_sets.prompt 里供对照。
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
import time

from .audit import new_id, write_audit

DDL = """
CREATE TABLE IF NOT EXISTS eval_sets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  run_id TEXT,
  task_id TEXT,
  project_id TEXT,
  agent_id TEXT,
  model TEXT,
  pack_id TEXT,
  task_title TEXT,
  prompt TEXT,
  output TEXT,
  criteria_json TEXT,
  source TEXT DEFAULT 'run',
  created_by TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id)
);

CREATE TABLE IF NOT EXISTS eval_runs (
  id TEXT PRIMARY KEY,
  name TEXT,
  runner TEXT,
  mode TEXT,
  state TEXT DEFAULT 'running',
  set_count INTEGER DEFAULT 0,
  avg_score REAL DEFAULT 0,
  detail_json TEXT,
  created_by TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  ended_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS eval_results (
  id TEXT PRIMARY KEY,
  eval_run_id TEXT NOT NULL,
  set_id TEXT NOT NULL,
  task_id TEXT,
  run_id TEXT,
  output TEXT,
  similarity REAL DEFAULT 0,
  judge_score REAL,
  judge_reason TEXT,
  score REAL DEFAULT 0,
  state TEXT DEFAULT 'ok',
  detail_json TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_evalres_run ON eval_results(eval_run_id);
"""


def ensure(conn) -> None:
    conn.executescript(DDL)
    conn.commit()


def _artifact_text(conn, *, run_id: str | None, task_id: str | None) -> str | None:
    row = None
    if run_id:
        row = conn.execute(
            "SELECT file_path FROM artifacts WHERE run_id=? ORDER BY art_seq DESC LIMIT 1",
            (run_id,)).fetchone()
    if row is None and task_id:
        row = conn.execute(
            """SELECT a.file_path FROM artifacts a JOIN task_runs r ON r.id=a.run_id
               WHERE r.task_id=? ORDER BY a.created_at DESC LIMIT 1""", (task_id,)).fetchone()
    if row is None:
        return None
    import pathlib
    try:
        return pathlib.Path(row["file_path"]).read_text(encoding="utf-8", errors="replace")[:40000]
    except OSError:
        return None


def create_from_run(conn, run_id: str, *, name: str | None = None,
                    by: str = "system") -> dict:
    ensure(conn)
    run = conn.execute("SELECT * FROM task_runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise ValueError("run not found")
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (run["task_id"],)).fetchone()
    if task is None:
        raise ValueError("task not found")
    existing = conn.execute("SELECT * FROM eval_sets WHERE run_id=?", (run_id,)).fetchone()
    if existing is not None:
        return dict(existing)
    output = _artifact_text(conn, run_id=run_id, task_id=run["task_id"])
    if not output or not output.strip():
        raise ValueError("run 没有可对比的产物，不能设为基准")
    agent = conn.execute("SELECT * FROM ai_employees WHERE id=?",
                         (task["owner_employee_id"],)).fetchone() if task["owner_employee_id"] else None
    from .engine import dsh_adapter
    prompt = (run["prompt_text"] if "prompt_text" in run.keys() else None) \
        or dsh_adapter.default_prompt(task, conn=conn)
    criteria = [dict(r) for r in conn.execute(
        "SELECT criterion, check_type, state, note FROM acceptance_criteria"
        " WHERE task_id=? ORDER BY seq", (run["task_id"],))]
    sid = new_id("EVS")
    conn.execute(
        """INSERT INTO eval_sets
             (id, name, run_id, task_id, project_id, agent_id, model, pack_id,
              task_title, prompt, output, criteria_json, created_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sid, (name or task["title"] or run_id)[:120], run_id, run["task_id"],
         task["project_id"] if "project_id" in task.keys() else None,
         task["owner_employee_id"], (agent["model"] if agent else None),
         task["pack_id"] if "pack_id" in task.keys() else None,
         task["title"], prompt, output,
         json.dumps(criteria, ensure_ascii=False), by))
    write_audit(conn, legal_entity_id=run["legal_entity_id"], verb="eval", task_id=run["task_id"],
                run_id=run_id, layer="system", action="eval_baseline_created", target=sid,
                payload={"name": name or task["title"], "run_id": run_id,
                         "output_chars": len(output)},
                response={"ok": True})
    conn.commit()
    return dict(conn.execute("SELECT * FROM eval_sets WHERE id=?", (sid,)).fetchone())


def _similarity(a: str, b: str) -> float:
    def norm(x: str) -> str:
        return re.sub(r"\s+", "", (x or "").lower())[:20000]
    return round(difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() * 100, 2)


def judge_pair(baseline: str, candidate: str, title: str) -> dict | None:
    """大模型打分（可关：EVAL_JUDGE=0）。返回 {score, reason} 或 None。"""
    if os.environ.get("EVAL_JUDGE", "1") == "0":
        return None
    from . import llm
    prompt = (
        "你是严格的交付质量评审。比较「基准产出」与「新产出」，判断新产出是否至少同等可用。\n"
        f"任务：{title}\n\n【基准产出】\n{baseline[:5000]}\n\n【新产出】\n{candidate[:5000]}\n\n"
        "只输出 JSON：{\"score\": 0-100 的整数, \"reason\": \"不超过80字的理由\"}。"
        "评分标准：任务完成度 40%、准确性 30%、结构清晰 20%、无空话 10%。")
    text = llm.complete(prompt, system="你只输出 JSON，不要 markdown 代码块。",
                        max_tokens=300, temperature=0.1)
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        score = max(0.0, min(100.0, float(data.get("score", 0))))
        return {"score": round(score, 2), "reason": str(data.get("reason", ""))[:300]}
    except (ValueError, TypeError):
        return None


def score_pair(baseline: str, candidate: str, *, title: str = "", use_judge: bool = True) -> dict:
    sim = _similarity(baseline, candidate)
    judged = judge_pair(baseline, candidate, title) if use_judge else None
    if judged:
        score = round(0.5 * sim + 0.5 * judged["score"], 2)
    else:
        score = sim
    return {"similarity": sim, "judge_score": judged["score"] if judged else None,
            "judge_reason": judged["reason"] if judged else "", "score": score,
            "judge": bool(judged)}


def run_case(conn, set_id: str, *, runner: str = "mock_acp",
             prompt_mode: str = "stored", budget_tokens: int | None = None) -> dict:
    """一条基准：建临时任务 → 重跑 → 取产物 → 打分。

    prompt_mode=stored  用基准当时的完整 prompt（含附件正文），只隔离模型变化
    prompt_mode=current 用当前模板重新组装，用来测 Prompt 改版
    """
    ensure(conn)
    s = conn.execute("SELECT * FROM eval_sets WHERE id=?", (set_id,)).fetchone()
    if s is None:
        raise ValueError("eval set not found")
    agent = conn.execute("SELECT * FROM ai_employees WHERE id=?",
                         (s["agent_id"],)).fetchone() if s["agent_id"] else None
    entity = (agent["legal_entity_id"] if agent else None) or "LE-GROUP-HQ"
    from .tenancy import of_entity
    tenant_id = of_entity(conn, entity)
    task_id = new_id("T")
    conn.execute(
        """INSERT INTO tasks (id, legal_entity_id, tenant_id, task_type, title, state,
                              owner_employee_id, pack_id)
           VALUES (?,?,?, 'eval', ?, 'todo', ?, ?)""",
        (task_id, entity, tenant_id, f"[Eval] {s['name']}", s["agent_id"], s["pack_id"]))
    conn.commit()

    from .api import verbs
    from .engine import dsh_adapter
    from .models import SpawnRunIn
    spawned = verbs.do_spawn_run(
        conn, SpawnRunIn(task_id=task_id, run_seq=1, legal_entity_id=entity),
        ident={"id": "ID-EVAL", "role": "ceo", "legal_entity_id": entity})
    run_id = spawned["run_id"]
    conn.commit()
    # 当前员工模型；prompt 模式决定是否照抄基准原文
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    prompt = (s["prompt"] if prompt_mode != "current" else None) or dsh_adapter.default_prompt(task, conn=conn)
    tokens = budget_tokens if budget_tokens is not None \
        else int(os.environ.get("EVAL_BUDGET", "30000"))
    # 实体池不够时按剩余额度压估，避免 eval 还没起跑就被预算闸拒绝
    pool = conn.execute(
        """SELECT COALESCE(SUM(limit_tokens - used_tokens), 0) FROM budgets
           WHERE legal_entity_id=? AND task_id IS NULL AND identity_id IS NULL
             AND COALESCE(limit_tokens,0) > 0""", (entity,)).fetchone()[0]
    if pool and int(pool) > 0:
        tokens = min(tokens, max(4000, int(pool)))
    out = dsh_adapter.execute_run(conn, run_id, prompt=prompt, runner=runner,
                                  budget_tokens=tokens)
    output = _artifact_text(conn, run_id=run_id, task_id=task_id) or ""
    scored = score_pair(s["output"] or "", output, title=s["task_title"] or s["name"])
    if out.get("result") == "denied":
        state = "denied"
    elif not output.strip():
        state = "no_artifact"
    else:
        state = "ok"
    rid = new_id("EVR")
    conn.execute(
        """INSERT INTO eval_results
             (id, eval_run_id, set_id, task_id, run_id, output, similarity,
              judge_score, judge_reason, score, state, detail_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rid, "", set_id, task_id, run_id, output[:40000], scored["similarity"],
         scored["judge_score"], scored["judge_reason"], scored["score"], state,
         json.dumps({"runner": runner, "prompt_mode": prompt_mode,
                     "tokens": out.get("usage_tokens"), "reason": out.get("reason"),
                     "budget": out.get("budget"),
                     "exit_code": out.get("exit_code"), "model": agent["model"] if agent else None},
                    ensure_ascii=False)))
    conn.commit()
    return {"result_id": rid, "set_id": set_id, "task_id": task_id, "run_id": run_id,
            "output": output, "state": state, **scored,
            "tokens": out.get("usage_tokens"), "exit_code": out.get("exit_code")}


def start_regression(conn, *, set_ids: list[str] | None = None, runner: str = "mock_acp",
                     prompt_mode: str = "stored", name: str | None = None,
                     by: str = "system", limit: int = 8) -> str:
    ensure(conn)
    sets = [r["id"] for r in conn.execute(
        "SELECT id FROM eval_sets ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()]
    ids = set_ids or sets
    if not ids:
        raise ValueError("没有基准用例")
    bid = new_id("EVRUN")
    conn.execute(
        """INSERT INTO eval_runs (id, name, runner, mode, state, set_count, created_by)
           VALUES (?,?,?,?, 'running', ?, ?)""",
        (bid, name or f"回归 {time.strftime('%Y-%m-%d %H:%M')}", runner, prompt_mode,
         len(ids), by))
    conn.commit()
    return bid


def execute_regression(conn, batch_id: str, *, set_ids: list[str], runner: str,
                       prompt_mode: str = "stored") -> dict:
    results = []
    for sid in set_ids:
        try:
            r = run_case(conn, sid, runner=runner, prompt_mode=prompt_mode)
            r["eval_run_id"] = batch_id
            conn.execute("UPDATE eval_results SET eval_run_id=? WHERE id=?",
                         (batch_id, r["result_id"]))
            conn.commit()
            results.append(r)
        except Exception as exc:                 # noqa: BLE001
            results.append({"set_id": sid, "state": "error", "error": str(exc),
                            "score": 0.0})
    ok = [r for r in results if r.get("state") == "ok"]
    avg = round(sum(r["score"] for r in ok) / len(ok), 2) if ok else 0.0
    state = "done" if ok else "failed"
    conn.execute("UPDATE eval_runs SET state=?, avg_score=?, detail_json=?,"
                 " ended_at=CURRENT_TIMESTAMP WHERE id=?",
                 (state, avg, json.dumps(results, ensure_ascii=False), batch_id))
    write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="eval", task_id=None,
                layer="system", action="eval_regression_done", target=batch_id,
                payload={"runner": runner, "sets": len(set_ids)},
                response={"avg_score": avg, "ok": len(ok), "state": state})
    conn.commit()
    return {"batch_id": batch_id, "state": state, "avg_score": avg, "results": results}


def list_sets(conn, limit: int = 100) -> list[dict]:
    ensure(conn)
    rows = conn.execute(
        """SELECT s.*, (SELECT score FROM eval_results r WHERE r.set_id=s.id
                          AND r.state='ok' ORDER BY r.rowid DESC LIMIT 1) AS last_score
           FROM eval_sets s ORDER BY s.rowid DESC LIMIT ?""", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["output_chars"] = len(d.pop("output", "") or "")
        out.append(d)
    return out


def list_batches(conn, limit: int = 30) -> list[dict]:
    ensure(conn)
    rows = conn.execute("SELECT * FROM eval_runs ORDER BY rowid DESC LIMIT ?",
                        (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.pop("detail_json", None)
        out.append(d)
    return out


def batch_detail(conn, batch_id: str) -> dict:
    ensure(conn)
    b = conn.execute("SELECT * FROM eval_runs WHERE id=?", (batch_id,)).fetchone()
    if b is None:
        return {}
    results = [dict(r) for r in conn.execute(
        """SELECT r.*, s.name AS set_name, s.task_title
           FROM eval_results r LEFT JOIN eval_sets s ON s.id=r.set_id
           WHERE r.eval_run_id=? ORDER BY r.rowid""", (batch_id,))]
    for r in results:
        r["output_chars"] = len(r.pop("output", "") or "")
    return {"batch": dict(b), "results": results}
