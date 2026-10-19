"""Export one task's full audit timeline as Markdown.

Everything the shell knows about a task, in the order it happened: creation,
criteria snapshot, every run (engine, tokens, exit code), acceptance passes,
finance/decision rows, WeCom pushes, and the ai_audit_logs entries between them.

    python scripts/audit_timeline.py T-2059eaa6cb184855 [--out docs/audit_x.md]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_env  # noqa: E402
from app.db import connect  # noqa: E402

load_env()


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _table(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return "_（无）_\n"
    head = "| " + " | ".join(cols) + " |\n"
    sep = "|" + "|".join("---" for _ in cols) + "|\n"
    body = "".join(
        "| " + " | ".join(_fmt(r.get(c)).replace("|", "\\|").replace("\n", " ") for c in cols) + " |\n"
        for r in rows)
    return head + sep + body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task_id")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    tid = args.task_id

    conn = connect()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if task is None:
        raise SystemExit(f"no such task: {tid}")

    crit = [dict(r) for r in conn.execute(
        "SELECT seq, acceptance_layer, state, criterion, check_type, check_ref, note"
        " FROM acceptance_criteria WHERE task_id=? ORDER BY seq", (tid,))]
    runs = [dict(r) for r in conn.execute(
        """SELECT id, run_seq, state, runner, usage_tokens, usage_baseline, exit_code,
                  started_at, ended_at, session_id, actor_identity
           FROM task_runs WHERE task_id=? ORDER BY rowid""", (tid,))]
    arts = [dict(r) for r in conn.execute(
        """SELECT r.id AS run_id, a.file_path, a.bytes, a.created_at
           FROM artifacts a JOIN task_runs r ON r.id=a.run_id
           WHERE r.task_id=? ORDER BY a.created_at""", (tid,))]
    audits = [dict(r) for r in conn.execute(
        """SELECT created_at, verb, action, layer, actor, target, payload, response_json
           FROM ai_audit_logs WHERE task_id=? ORDER BY created_at, rowid""", (tid,))]
    pushes = [dict(r) for r in conn.execute(
        "SELECT kind, wecom_userid, status, msg_id, sent_at FROM wecom_pushes"
        " WHERE target_id=? ORDER BY sent_at", (tid,))]
    budgets = [dict(r) for r in conn.execute(
        "SELECT id, identity_id, limit_tokens, used_tokens, limit_amount, used_amount"
        " FROM budgets WHERE task_id=?", (tid,))]

    total_tokens = sum(r["usage_tokens"] or 0 for r in runs)
    out = []
    out.append(f"# 审计时间轴 · {tid}\n")
    out.append(f"- 标题：{task['title']}")
    out.append(f"- 业务包：{task['pack_id']}　实体：{task['legal_entity_id']}　状态：**{task['state']}**")
    out.append(f"- 创建：{task['created_at']}　更新：{task['updated_at']}")
    out.append(f"- 运行次数：{len(runs)}　累计 tokens：**{total_tokens:,}**　产物：{len(arts)}\n")

    out.append("## 判据快照\n")
    out.append(_table(crit, ["seq", "acceptance_layer", "state", "criterion", "check_type", "note"]))

    out.append("\n## 运行记录\n")
    out.append(_table(runs, ["id", "run_seq", "state", "runner", "usage_tokens", "exit_code",
                             "started_at", "ended_at", "actor_identity"]))
    out.append("\n## 产物\n")
    out.append(_table(arts, ["run_id", "file_path", "bytes", "created_at"]))
    out.append("\n## 预算\n")
    out.append(_table(budgets, ["id", "identity_id", "limit_tokens", "used_tokens",
                                "limit_amount", "used_amount"]))
    out.append("\n## 企微推送\n")
    out.append(_table(pushes, ["kind", "wecom_userid", "status", "sent_at"]))

    out.append("\n## 事件时间轴\n")
    out.append("| 时间 | 动作 | 阶段 | 执行者 | 摘要 |\n|---|---|---|---|---|\n")
    hb = []                                   # consecutive heartbeats, collapsed
    def flush_hb() -> None:
        if not hb:
            return
        first, last = hb[0]["created_at"], hb[-1]["created_at"]
        span = first if first == last else f"{first} → {last}"
        out.append(f"| {span} | 心跳 ×{len(hb)} | system | system | 运行存活探针 |")
        hb.clear()

    for a in audits:
        if "heartbeat" in (a["action"] or ""):
            hb.append(a)
            continue
        flush_hb()
        try:
            payload = json.loads(a["payload"] or "{}")
        except ValueError:
            payload = {"raw": a["payload"]}
        try:
            resp = json.loads(a["response_json"] or "{}")
        except ValueError:
            resp = {}
        bits = []
        for k in ("runner", "exit_code", "event", "reason", "verdict", "criteria_count",
                  "usage_tokens", "artifacts"):
            if k in payload:
                bits.append(f"{k}={payload[k]}")
            if k in resp:
                bits.append(f"{k}={resp[k]}")
        if a["action"] in ("emit_event",):
            bits = [f"event={payload.get('target') or a['target']}"] + bits
        if not bits:
            flat = " ".join(f"{k}={v}" for k, v in list(payload.items())[:3] if v not in (None, ""))
            bits = [f"{a['target']} {flat}".strip()]
        summary = ", ".join(dict.fromkeys(bits))[:150]
        out.append(f"| {a['created_at']} | {a['action']} | {a['layer']} | {a['actor']} | {summary} |")

    text = "\n".join(out) + "\n"
    dest = pathlib.Path(args.out) if args.out else ROOT / "docs" / f"audit_{tid}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(f"wrote {dest} ({len(text)} chars, {len(audits)} audit rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
