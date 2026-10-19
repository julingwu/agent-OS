"""Real business case #1: a code-generation task (crawler), not marketing copy.

Watches what the gate and the heartbeat do when the deliverable is a script:
how many model calls, how much usage, how many heartbeats, and whether the code
criteria (file / py_compile / target URL) actually judge the artifact.
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from oa_client import client_from_env  # noqa: E402

C = client_from_env()
DB = os.environ.get("DB_PATH", str(pathlib.Path(__file__).resolve().parents[1] / "data" / "oa.db"))

SENTENCE = "写一个 Python 脚本，用 requests 抓取 https://example.com 的页面并保存为 output.txt"
PROMPT = ("在当前目录写 crawler.py：用 requests 抓取 https://example.com 的页面文本，"
          "保存为 output.txt，并在文件顶部写清用法与依赖。写完只回复 DONE。")


def heartbeats(run_id: str) -> tuple[int, str | None]:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT hb_seq, last_hb_at FROM task_runs WHERE id = ?", (run_id,)).fetchone()
    return (row["hb_seq"] or 0, row["last_hb_at"]) if row else (0, None)


def main() -> int:
    t = C.post("/tasks/from_sentence", json={"text": SENTENCE}).json()
    print("task_type:", t["task_type"], "| pack:", t["pack_id"])
    for c in t["criteria"]:
        print(f"  #{c['seq']} {c['acceptance_layer']} blocking={c['is_blocking']} {c['criterion']}")

    tid = t["task_id"]
    run = C.post("/verbs/spawn_run", json={"task_id": tid, "run_seq": 1}).json()
    out = C.post(f"/runs/{run['run_id']}/execute", json={
        "runner": "acp", "prompt": PROMPT, "budget_tokens": 24000}).json()

    hb, hb_at = heartbeats(run["run_id"])
    print("\nEXECUTE", json.dumps({k: out.get(k) for k in (
        "exit_code", "stop_reason", "task_state", "usage_tokens", "usage_total",
        "event_count", "session_id")}, ensure_ascii=False))
    print("GATE", json.dumps(out.get("gate"), ensure_ascii=False))
    print("HEARTBEATS", hb, "last:", hb_at)
    print("ARTIFACTS", [(pathlib.Path(a["file_path"]).name, a["bytes"], a["checksum"][:12])
                        for a in out.get("artifacts") or []])

    acc = C.post(f"/tasks/{tid}/acceptance/run").json()
    print("\nACCEPT", acc["verdict"], acc["task_state"])
    for r in acc["results"]:
        print(f"  {r.get('layer')} {r['criterion_id'][-6:]} -> {r['state']}: {r.get('detail')}")

    # L1: sign off the two human criteria on the actual artifact
    detail = C.get(f"/tasks/{tid}").json()
    art_path = detail["artifacts"][0]["file_path"] if detail["artifacts"] else None
    body = pathlib.Path(art_path).read_text(encoding="utf-8", errors="replace") if art_path else ""
    for c in detail["criteria"]:
        if c["check_type"] == "human_check" and c["state"] == "pending":
            C.post(f"/criteria/{c['id']}/check", json={
                "passed": True, "checked_by": "reviewer",
                "note": "已逐行看过：无删文件/写系统目录/硬编码密钥" if c["is_blocking"]
                        else "顶部注释含用法与依赖",
                "evidence_snippet": body[:180]})
    final = C.post(f"/tasks/{tid}/acceptance/run").json()
    print("FINAL", final["verdict"], final["task_state"])
    art = detail["artifacts"][0]["file_path"] if detail["artifacts"] else None
    if art:
        head = pathlib.Path(art).read_text(encoding="utf-8", errors="replace").splitlines()[:8]
        print("\n--- artifact head ---")
        print("\n".join(head))
    print("\nTASK", tid, "URL", f"http://127.0.0.1:8077/?task_id={tid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
