"""集团业务大演练：两实体并发派单 + 隔离验证 + 大额决策拦截.

    python scripts/drill_group.py

Runs the two business tasks in parallel (each signed by its own entity manager),
then proves the walls: cross-entity task/memory invisibility, and that the CEO
approval cannot be forced without a signature or with the wrong role.

The tasks are left in pending_review on purpose — the chairman accepts them in
the workbench.
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

from oa_client import OAClient as SignedClient  # noqa: E402

BASE = "http://127.0.0.1:8077"

CONTENT_TASK = ("写一篇 400-600 字的双十一营销短文：产品是秋季护肤套装，"
                "要有卖点、价格和行动号召")
CONTENT_PROMPT = ("在当前目录写 article.md：一篇 400-600 字的双十一营销短文，产品是秋季护肤套装，"
                  "包含卖点、价格（如 299 元）与行动号召。写完只回复 DONE。")
QUANT_TASK = "写一个 Python 脚本抓取股票数据并保存为 CSV"
QUANT_PROMPT = ("在当前目录写 fetch_quote.py：用公开接口抓取 AAPL 最近 5 个交易日的收盘价，"
                "保存为 prices.csv；网络不可用时写入内置样本并说明。写完只回复 DONE。")


def load_creds() -> dict:
    out: dict[str, dict] = {}
    for line in (ROOT / ".env.drill").read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        role, _, field = k.partition("_")
        out.setdefault(role.lower(), {})[field.lower()] = v.strip()
    return out


def dispatch(name: str, cred: dict, text: str, pack: str, prompt: str, budget: int,
             results: dict) -> None:
    """One entity's whole cycle, run in its own thread."""
    c = SignedClient(BASE, cred["id"], cred["secret"])
    try:
        t0 = time.time()
        task = c.post("/tasks/from_sentence", json={"text": text, "pack_id": pack}).json()
        run = c.post("/verbs/spawn_run", json={"task_id": task["task_id"], "run_seq": 1}).json()
        ex = c.post(f"/runs/{run['run_id']}/execute",
                    json={"runner": "acp", "budget_tokens": budget, "prompt": prompt}).json()
        results[name] = {
            "task_id": task["task_id"], "pack": task["pack_id"],
            "criteria": len(task["criteria"]),
            "state": ex.get("task_state"), "exit_code": ex.get("exit_code"),
            "tokens": ex.get("usage_tokens"),
            "artifact": (ex.get("artifacts") or [{}])[0].get("file_path"),
            "seconds": round(time.time() - t0, 1),
        }
    except Exception as exc:                      # noqa: BLE001 - drill reports, never dies
        results[name] = {"error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    creds = load_creds()
    raw = httpx.Client(base_url=BASE, trust_env=False, timeout=30)
    content = SignedClient(BASE, creds["content"]["id"], creds["content"]["secret"])
    quant = SignedClient(BASE, creds["quant"]["id"], creds["quant"]["secret"])
    ceo = SignedClient(BASE, creds["ceo"]["id"], creds["ceo"]["secret"])

    report: dict = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": {}}

    # ---- 1. concurrent dispatch, two entities, two packs
    print("[1/4] 并发派单…", flush=True)
    got: dict = {}
    threads = [
        threading.Thread(target=dispatch, args=("content", creds["content"], CONTENT_TASK,
                                                "content-v1", CONTENT_PROMPT, 30000, got)),
        threading.Thread(target=dispatch, args=("quant", creds["quant"], QUANT_TASK,
                                                "code-v1", QUANT_PROMPT, 45000, got)),
    ]
    t0 = time.time()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    report["steps"]["dispatch"] = {"wall_seconds": round(time.time() - t0, 1), **got}
    print(json.dumps(report["steps"]["dispatch"], ensure_ascii=False, indent=2), flush=True)

    # ---- 2. entity walls: tasks and memory
    print("[2/4] 隔离验证…", flush=True)
    c_tasks = content.get("/tasks").json()
    q_tasks = quant.get("/tasks").json()
    q_task_id = report["steps"]["dispatch"].get("quant", {}).get("task_id")
    cross = content.get(f"/tasks/{q_task_id}")
    mem = quant.post("/memory", json={
        "scope": "entity:LE-QUANT",
        "content": "量化实体私有：AAPL 抓取脚本的接口密钥轮换周期为 30 天。"}).json()
    seen_by_content = content.get("/memory").json()
    peek = content.get(f"/memory/{mem.get('id')}") if mem.get("id") else None
    report["steps"]["isolation"] = {
        "content_sees_tasks": c_tasks["total"],
        "quant_sees_tasks": q_tasks["total"],
        "quant_task_visible_to_content": cross.status_code,
        "content_entities": sorted({t["legal_entity_id"] for t in c_tasks["tasks"]}),
        "quant_entities": sorted({t["legal_entity_id"] for t in q_tasks["tasks"]}),
        "quant_memory_id": mem.get("id"),
        "content_memory_count": seen_by_content["count"],
        "content_peeking_quant_memory": peek.status_code if peek else None,
    }
    print(json.dumps(report["steps"]["isolation"], ensure_ascii=False, indent=2), flush=True)

    # ---- 3. big decision + unsigned attack
    print("[3/4] 大额决策与越权尝试…", flush=True)
    dec = content.post("/decisions", json={
        "title": "内容实体申请追加 15 万 token 预算（双十一大促）",
        "summary": "双十一大促需要 3 条内容线并行，现有 10 万 token 池不够，申请追加 15 万。",
        "amount": 150000, "currency": "token",
        "risk_level": "medium", "reversibility": "costly",
        "recommendation": "大促结束后回收剩余额度",
    }).json()
    ev = content.post(f"/decisions/{dec['id']}/evaluate").json()
    dec = content.get(f"/decisions/{dec['id']}").json()

    unsigned = raw.post(f"/decisions/{dec['id']}/decide", json={"approved": True})
    forged = raw.post(f"/decisions/{dec['id']}/decide", json={"approved": True},
                      headers={"X-OA-Key": creds["ceo"]["id"], "X-OA-Ts": "1",
                               "X-OA-Nonce": "forged", "X-OA-Sig": "00" * 32})
    wrong_role = content.post(f"/decisions/{dec['id']}/decide", json={"approved": True})

    report["steps"]["decision"] = {
        "decision_id": dec["id"], "state": dec["state"],
        "evaluation": ev,
        "reason": dec.get("filter_reason"),
        "amount": dec.get("amount"), "currency": dec.get("currency"),
        "unsigned_status": unsigned.status_code,
        "forged_signature_status": forged.status_code,
        "wrong_role_status": wrong_role.status_code,
        "wrong_role_detail": (wrong_role.json().get("detail")
                              if wrong_role.headers.get("content-type", "").startswith("application/json")
                              else wrong_role.text[:80]),
    }
    print(json.dumps(report["steps"]["decision"], ensure_ascii=False, indent=2), flush=True)

    # ---- 4. hand over
    print("[4/4] 交接…", flush=True)
    report["handover"] = {
        "workbench": BASE + "/",
        "ceo_id": creds["ceo"]["id"],
        "pending_tasks": {k: v.get("task_id") for k, v in got.items() if v.get("task_id")},
        "pending_decision": dec["id"],
    }
    out = ROOT / "docs" / "drill_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport -> {out}")
    print(f"CEO login: {creds['ceo']['id']}  (secret in .env.drill)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
