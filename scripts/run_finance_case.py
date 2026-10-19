"""Real business case: the finance pack, end to end."""
from __future__ import annotations
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from oa_client import client_from_env  # noqa: E402

C = client_from_env()
TEXT = "做这个月的报销费用审核单：差旅 1200、办公用品 350、餐费 180"

def main() -> int:
    t = C.post("/tasks/from_sentence", json={"text": TEXT}).json()
    print("pack:", t["pack_id"], "| type:", t["task_type"])
    for c in t["criteria"]:
        print(f"  #{c['seq']} {c['acceptance_layer']} {c['criterion'][:40]}")
    run = C.post("/verbs/spawn_run", json={"task_id": t["task_id"], "run_seq": 1}).json()
    out = C.post(f"/runs/{run['run_id']}/execute", json={
        "runner": "acp", "budget_tokens": 30000,
        "prompt": ("在当前目录写 audit.md：把三笔费用（差旅 1200 元、办公用品 350 元、餐费 180 元）"
                   "整理成审核单，写清科目、金额、合计与结论。写完只回复 DONE。")}).json()
    print("exec:", {k: out.get(k) for k in ("exit_code", "task_state", "usage_tokens")},
          "| artifact:", (out.get("artifacts") or [{}])[0].get("file_path"))
    acc = C.post(f"/tasks/{t['task_id']}/acceptance/run").json()
    print("accept:", acc["verdict"], acc["task_state"])
    for r in acc["results"]:
        print("  ", r.get("layer"), r["state"], (r.get("detail") or "")[:52])
    d = C.get(f"/tasks/{t['task_id']}").json()
    for c in d["criteria"]:
        if c["check_type"] == "human_check" and c["state"] == "pending":
            C.post(f"/criteria/{c['id']}/check", json={"passed": True, "checked_by": "cfo",
                 "note": "三笔金额与合计一致", "evidence_snippet": "合计 1730"})
    final = C.post(f"/tasks/{t['task_id']}/acceptance/run").json()
    print("final:", final["verdict"], final["task_state"])
    art = (out.get("artifacts") or [{}])[0].get("file_path")
    if art:
        print("--- audit.md head ---")
        print(pathlib.Path(art).read_text(encoding="utf-8", errors="replace")[:320])
    print("TASK", t["task_id"])
    return 0

if __name__ == "__main__":
    sys.exit(main())
