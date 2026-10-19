"""Real ACP end-to-end: clean run, mid-turn cancel, retry-on-same-session.

Costs real tokens. Prints a compact table so the evidence is readable.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from oa_client import client_from_env  # noqa: E402

BASE = os.environ.get("M0_BASE", "http://127.0.0.1:8077")
C = client_from_env()

def show(label: str, d: dict, keys: tuple[str, ...]) -> None:
    print(label, {k: d.get(k) for k in keys})


def main() -> int:
    # ---------- A: clean ACP run ----------
    t = C.post("/tasks/from_sentence", json={"text": "写一篇 500 字秋季护肤短文"}).json()
    tid = t["task_id"]
    run = C.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()
    a = C.post(f"/runs/{run['run_id']}/execute", json={
        "runner": "acp",
        "prompt": "在当前目录写一篇 500 汉字左右的秋季护肤短文，保存为 article.md，写完只回复 DONE。"}).json()
    show("A_clean", a, ("exit_code", "stop_reason", "task_state", "session_id",
                        "usage_tokens", "event_count"))
    print("A_gate", a.get("gate"), "A_artifacts", len(a.get("artifacts") or []))

    # ---------- B: mid-turn cancel (cap far below the first call) ----------
    t2 = C.post("/tasks/from_sentence", json={"text": "写一篇 500 字秋季护肤短文"}).json()
    run2 = C.post("/verbs/spawn_run", json={"task_id": t2["task_id"], "run_seq": 1}).json()
    b = C.post(f"/runs/{run2['run_id']}/execute", json={
        "runner": "acp", "prompt": "只回复 OK", "budget_tokens": 2000}).json()
    show("B_capped", b, ("exit_code", "stop_reason", "task_state", "usage_tokens"))
    print("B_gate", b.get("gate"))

    # ---------- C: L1 reject -> retry on the same session ----------
    data = C.get(f"/tasks/{tid}").json()
    c5 = next(c for c in data["criteria"] if c["seq"] == 5)
    C.post(f"/criteria/{c5['id']}/check", json={
        "passed": False, "checked_by": "e2e", "note": "末尾缺少明确的行动建议",
        "evidence_snippet": "给皮肤一点时间，它会自己找回节奏。"})
    verdict = C.post(f"/tasks/{tid}/acceptance/run").json()
    print("C_reject", {"verdict": verdict["verdict"], "state": verdict["task_state"]})

    r = C.post(f"/tasks/{tid}/retry", json={
        "runner": "acp",
        "prompt": "上一轮被判据5打回：末尾缺少明确的行动建议。保持同一篇稿件，"
                  "在结尾补一句可以马上照做的建议，写回 article.md，改完只回复 DONE。"}).json()
    show("C_retry", r, ("exit_code", "stop_reason", "task_state", "session_id", "resumed",
                        "usage_tokens"))
    runs = C.get(f"/tasks/{tid}").json()["runs"]
    print("C_runs", [(x["run_seq"], x["state"], x["session_id"]) for x in runs])
    print("C_same_session", len({x["session_id"] for x in runs}) == 1)

    acc = C.post(f"/tasks/{tid}/acceptance/run").json()
    print("C_accept_after_retry", {"verdict": acc["verdict"], "state": acc["task_state"]})

    # reviewer signs off the new version -> completed
    fresh = next(c for c in C.get(f"/tasks/{tid}").json()["criteria"] if c["seq"] == 5)
    C.post(f"/criteria/{fresh['id']}/check", json={
        "passed": True, "checked_by": "e2e", "note": "结尾已补行动建议",
        "evidence_snippet": "今晚就换成温和洁面，把面霜涂厚一点。"})
    final = C.post(f"/tasks/{tid}/acceptance/run").json()
    print("C_final", {"verdict": final["verdict"], "state": final["task_state"]})
    print("TASK_URL", f"{BASE}/?task_id={tid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
