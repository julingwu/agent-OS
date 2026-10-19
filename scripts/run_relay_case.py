"""Real two-agent relay: coder writes a crawler, reviewer runs it and judges."""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from oa_client import client_from_env  # noqa: E402

DB = os.environ.get("DB_PATH") or str(pathlib.Path(__file__).resolve().parents[1] / "data" / "oa.db")
TEXT = "写一个 Python 脚本抓取 https://example.com 的页面文本并保存为 output.txt"


def main() -> int:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    agents = [r["id"] for r in con.execute(
        "SELECT id FROM identities WHERE role='agent' AND state='active' ORDER BY created_at")]
    if len(agents) < 2:
        print("need two agent identities")
        return 2
    coder, reviewer = agents[0], agents[1]

    C = client_from_env()
    out = C.post("/relay/run", json={
        "text": TEXT, "coder_identity_id": coder,
        "reviewer_identity_id": reviewer, "runner": "acp"}).json()

    print("ok:", out.get("ok"), "| conclusion:", out.get("review_conclusion"))
    for role in ("coder", "reviewer"):
        d = out.get(role) or {}
        print(f"{role:>8}: task={d.get('task_id')} state={d.get('state')} "
              f"verdict={d.get('verdict')} usage={d.get('usage')} session={(d.get('session') or '')[:14]}")
        print(f"          artifact={d.get('artifact')}")
    print("copied_to_reviewer:", out.get("copied_to_reviewer"))
    print("locks left:", con.execute("SELECT COUNT(*) FROM run_locks").fetchone()[0])

    rev = (out.get("reviewer") or {}).get("artifact")
    if rev and os.path.exists(rev):
        print("--- review head ---")
        print(pathlib.Path(rev).read_text(encoding="utf-8", errors="replace")[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
