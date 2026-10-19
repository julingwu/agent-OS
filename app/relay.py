"""Two-agent relay: coder writes, reviewer checks — the shell dispatches.

The relay is deliberately synchronous and dumb: it creates the coder task, runs
it under the coder's identity, copies the artifact into the reviewer's own
workspace (so nobody reads across agent dirs), then creates and runs the review
task under the reviewer's identity. Locks are what stop the two runs from ever
touching the same DSH session.
"""
from __future__ import annotations

import shutil
import sqlite3

from . import acceptance_run, tenant
from .api import verbs
from .engine import dsh_adapter, task_generator
from .models import SpawnRunIn

CODER_PROMPT = ("在当前目录完成任务：{text}。\n"
                "把代码保存为 .py 文件（名字自取），写完只回复 DONE。")

REVIEW_PROMPT = ("待审脚本已放在当前目录 input.py（同一目录下可能有其他文件）。\n"
                 "请阅读并实际运行它（例如 python input.py），把评审写进 review.md：\n"
                 "第一行给出结论（通过 / 不通过），随后列 2-3 条具体问题或建议。\n"
                 "写完只回复 DONE。")


def _identity(conn: sqlite3.Connection, ident_id: str, entity: str) -> dict:
    row = conn.execute("SELECT * FROM identities WHERE id = ? AND state = 'active'",
                       (ident_id,)).fetchone()
    if row is None or (row["legal_entity_id"] or "") != entity:
        raise ValueError(f"identity {ident_id} is not an active member of {entity}")
    return dict(row)


def _spawn(conn: sqlite3.Connection, task_id: str, ident: dict) -> dict:
    return verbs.do_spawn_run(conn, SpawnRunIn(task_id=task_id, run_seq=1), ident=ident)


def _budget(name: str, default: int) -> int:
    import os
    return int(os.environ.get(name, str(default)))


def run_relay(conn: sqlite3.Connection, *, text: str, coder_id: str, reviewer_id: str,
              entity: str, runner: str = "mock") -> dict:
    coder = _identity(conn, coder_id, entity)
    reviewer = _identity(conn, reviewer_id, entity)
    if coder["id"] == reviewer["id"]:
        raise ValueError("coder and reviewer must be different identities")

    # ---- 1. coder
    coder_task = task_generator.generate(conn, text, entity)
    coder_run = _spawn(conn, coder_task["task_id"], coder)
    coder_exec = dsh_adapter.execute_run(
        conn, coder_run["run_id"], prompt=CODER_PROMPT.format(text=text), runner=runner,
        budget_tokens=_budget("M0_RELAY_CODER_BUDGET", 30000))
    coder_accept = acceptance_run.run(conn, coder_task["task_id"], entity)

    artifacts = coder_exec.get("artifacts") or []
    if not artifacts:
        return {"ok": False, "stage": "coder", "reason": "no artifact",
                "coder": {"task_id": coder_task["task_id"], "run_id": coder_run["run_id"],
                          "exec": coder_exec, "acceptance": coder_accept}}

    source = artifacts[0]["file_path"]
    name = source.replace("\\", "/").rstrip("/").split("/")[-1]

    # ---- 2. review task, with the artifact copied into the reviewer's own dir
    review_task = task_generator.generate(conn, f"审查脚本 {name} 并给出评审结论", entity)
    review_run = _spawn(conn, review_task["task_id"], reviewer)

    review_ws = dsh_adapter.task_workspace(review_task["task_id"], reviewer["id"])
    shutil.copy2(source, review_ws / "input.py")

    # the reviewer reads *and runs* the deliverable, so it needs more headroom
    # than the coder (measured: 20k ran out mid-review).
    review_exec = dsh_adapter.execute_run(
        conn, review_run["run_id"], prompt=REVIEW_PROMPT, runner=runner,
        budget_tokens=_budget("M0_RELAY_REVIEW_BUDGET", 45000))
    review_accept = acceptance_run.run(conn, review_task["task_id"], entity)

    review_artifact = (review_exec.get("artifacts") or [{}])[0].get("file_path")
    verdict = None
    if review_artifact:
        try:
            text = open(review_artifact, encoding="utf-8", errors="replace").read()
            # "不通过" contains "通过" — check the negative first
            verdict = "不通过" if "不通过" in text else ("通过" if "通过" in text else None)
        except OSError:
            verdict = None

    return {
        "ok": True,
        "entity": entity,
        "coder": {"identity": coder["id"], "task_id": coder_task["task_id"],
                  "run_id": coder_run["run_id"], "state": coder_accept["task_state"],
                  "verdict": coder_accept["verdict"], "artifact": source,
                  "usage": coder_exec.get("usage_tokens"),
                  "session": coder_exec.get("session_id")},
        "reviewer": {"identity": reviewer["id"], "task_id": review_task["task_id"],
                     "run_id": review_run["run_id"], "state": review_accept["task_state"],
                     "verdict": review_accept["verdict"], "artifact": review_artifact,
                     "usage": review_exec.get("usage_tokens"),
                     "session": review_exec.get("session_id")},
        "review_conclusion": verdict,
        "copied_to_reviewer": str(review_ws / "input.py"),
    }
