"""Focused real check: one ACP run + one retry, then read the stored columns.

Answers one question: does the second run get charged only its own delta, while
the row keeps the session cumulative total and the baseline it was netted against?
"""
from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from oa_client import client_from_env  # noqa: E402

C = client_from_env()


def main() -> int:
    t = C.post("/tasks/from_sentence", json={"text": "写一篇 500 字秋季护肤短文"}).json()
    tid = t["task_id"]
    run = C.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()
    a = C.post(f"/runs/{run['run_id']}/execute", json={
        "runner": "acp",
        "prompt": "在当前目录写一篇 500 汉字左右的秋季护肤短文，保存为 article.md，写完只回复 DONE。"}).json()
    print("run1", {k: a.get(k) for k in ("exit_code", "usage_tokens", "usage_total",
                                         "usage_baseline", "task_state")})

    crit = next(c for c in C.get(f"/tasks/{tid}").json()["criteria"] if c["seq"] == 5)
    C.post(f"/criteria/{crit['id']}/check", json={
        "passed": False, "checked_by": "e2e", "note": "结尾缺少行动建议",
        "evidence_snippet": "给皮肤一点时间，它会自己找回节奏。"})
    print("reject", C.post(f"/tasks/{tid}/acceptance/run").json()["verdict"])

    r = C.post(f"/tasks/{tid}/retry", json={
        "runner": "acp",
        "prompt": "上一轮被判据5打回：结尾缺少行动建议。保持同一篇稿件，在结尾补一句能马上照做的建议，"
                  "写回 article.md，改完只回复 DONE。"}).json()
    print("run2", {k: r.get(k) for k in ("exit_code", "resumed", "usage_tokens",
                                         "usage_total", "usage_baseline", "task_state")})
    print("gate2", r.get("gate"))

    runs = C.get(f"/tasks/{tid}").json()["runs"]
    for x in runs:
        print("stored", {"seq": x["run_seq"], "state": x["state"],
                         "usage_tokens": x["usage_tokens"], "baseline": x["usage_baseline"],
                         "session": x["session_id"]})
    print("TASK", tid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
