"""One real DSH headless run through the whole shell (costs tokens)."""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import identity  # noqa: E402
from app.main import app  # noqa: E402
from tests.authclient import SignedClient  # noqa: E402

PROMPT = "在当前工作目录写一篇 500 汉字左右的秋季护肤短文，保存为 article.md，写完后只回复 DONE。"


def main() -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY not set: headless does not read the desktop credential store")
        return 2
    import sqlite3

    with TestClient(app) as raw:
        con = sqlite3.connect(os.environ.get("DB_PATH", "data/oa.db"))
        con.row_factory = sqlite3.Row
        ident = identity.issue(con, name="e2e", role="system")
        con.commit()
        c = SignedClient(raw, ident["id"], ident["secret"])
        t = c.post("/tasks/from_sentence", json={"text": "写一篇 500 字秋季护肤短文"}).json()
        run = c.post("/verbs/spawn_run", json={"task_id": t["task_id"], "run_seq": 1}).json()
        out = c.post(f"/runs/{run['run_id']}/execute",
                     json={"runner": "dsh", "prompt": PROMPT}).json()
        print("EXECUTE", json.dumps({k: v for k, v in out.items() if k != "final"},
                                    ensure_ascii=False, indent=2))
        print("TASK", t["task_id"], "RUN", run["run_id"])
        acc = c.post(f"/tasks/{t['task_id']}/acceptance/run").json()
        print("ACCEPT", json.dumps(acc, ensure_ascii=False, indent=2))
        detail = c.get(f"/tasks/{t['task_id']}").json()
        print("CRITERIA", json.dumps(
            [{"seq": x["seq"], "state": x["state"], "note": x["note"],
              "blocking": x["is_blocking"]} for x in detail["criteria"]],
            ensure_ascii=False, indent=2))
        print("TASK_URL", f"http://127.0.0.1:8077/?task_id={t['task_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
