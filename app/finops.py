"""FinOps：token → 人民币（v2.4）。

计价口径写在 .env：
  MODEL_PRICES_JSON={"deepseek-chat":{"in":2,"out":8}, ...}   元 / 百万 token
  FINOPS_INPUT_RATIO=0.75                                     输入占比（DSH 只回总量）

真实计费需要输入/输出分开，而 ACP 的 `usage_update` 只给累计 used。这里按输入占比
估算，并在前端明确标注「估算口径」——不把估算伪装成账单。
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict

DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "deepseek-chat": {"in": 2.0, "out": 8.0},
    "deepseek-reasoner": {"in": 4.0, "out": 16.0},
    "gpt-4o": {"in": 18.0, "out": 72.0},
    "claude-sonnet-4": {"in": 21.0, "out": 105.0},
}


def normalize_model(model: str | None) -> str:
    return (model or "").strip().lower().split("/")[-1] or default_model()


def default_model() -> str:
    return os.environ.get("LLM_MODEL") or os.environ.get("UI_DEFAULT_MODEL") or "deepseek-chat"


def input_ratio() -> float:
    try:
        r = float(os.environ.get("FINOPS_INPUT_RATIO", "0.75"))
    except ValueError:
        r = 0.75
    return min(0.95, max(0.05, r))


def prices() -> dict[str, dict[str, float]]:
    table = {k: dict(v) for k, v in DEFAULT_PRICES.items()}
    raw = os.environ.get("MODEL_PRICES_JSON", "").strip()
    if raw:
        try:
            got = json.loads(raw)
            for model, p in got.items():
                if isinstance(p, dict) and "in" in p and "out" in p:
                    table[normalize_model(model)] = {"in": float(p["in"]), "out": float(p["out"])}
        except (ValueError, TypeError):
            pass
    # 单模型覆盖：MODEL_PRICE_DEEPSEEK_CHAT_IN / _OUT
    for model in list(table):
        key = model.upper().replace("-", "_").replace(".", "_").replace("/", "_")
        if os.environ.get(f"MODEL_PRICE_{key}_IN"):
            try:
                table[model]["in"] = float(os.environ[f"MODEL_PRICE_{key}_IN"])
            except ValueError:
                pass
        if os.environ.get(f"MODEL_PRICE_{key}_OUT"):
            try:
                table[model]["out"] = float(os.environ[f"MODEL_PRICE_{key}_OUT"])
            except ValueError:
                pass
    return table


def blended_price(model: str | None) -> float:
    p = prices().get(normalize_model(model)) or prices().get(default_model()) \
        or {"in": 2.0, "out": 8.0}
    r = input_ratio()
    return r * p["in"] + (1 - r) * p["out"]


def estimate(model: str | None, tokens: int) -> float:
    return round(max(0, int(tokens or 0)) * blended_price(model) / 1_000_000, 4)


def overview(conn: sqlite3.Connection, days: int = 30,
             tenant_id: str | None = None) -> dict:
    days = max(1, min(365, int(days)))
    where = "r.usage_tokens > 0 AND date(r.started_at) >= date('now', ?)"
    args: list = [f"-{days - 1} days"]
    if tenant_id:
        where += " AND COALESCE(t.tenant_id,'TENANT-HQ')=?"
        args.append(tenant_id)
    rows = conn.execute(
        f"""SELECT r.id AS run_id, r.task_id, r.usage_tokens, r.usage_baseline,
                  r.started_at, r.state, t.title,
                  t.owner_employee_id AS agent_id, e.name AS agent_name, e.model,
                  COALESCE(e.legal_entity_id, t.legal_entity_id) AS dept_id,
                  le.name AS dept_name
           FROM task_runs r JOIN tasks t ON t.id = r.task_id
           LEFT JOIN ai_employees e ON e.id = t.owner_employee_id
           LEFT JOIN legal_entities le ON le.id = COALESCE(e.legal_entity_id, t.legal_entity_id)
           WHERE {where}
           ORDER BY r.started_at""", args).fetchall()

    total_tokens = 0.0
    total_cost = 0.0
    by_agent: dict[str, dict] = {}
    by_dept: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = defaultdict(lambda: {"date": "", "tokens": 0, "cost": 0.0})
    runs = []

    for r in rows:
        tokens = max(0, int(r["usage_tokens"] or 0) - int(r["usage_baseline"] or 0))
        if tokens <= 0:
            continue
        model = normalize_model(r["model"])
        cost = estimate(model, tokens)
        day = (r["started_at"] or "")[:10]
        total_tokens += tokens
        total_cost += cost
        runs.append({"run_id": r["run_id"], "task_id": r["task_id"], "title": r["title"],
                     "agent_id": r["agent_id"], "agent_name": r["agent_name"],
                     "dept_id": r["dept_id"], "dept_name": r["dept_name"],
                     "model": model, "tokens": tokens, "cost": round(cost, 2),
                     "state": r["state"], "at": r["started_at"]})

        akey = r["agent_id"] or "system"
        a = by_agent.setdefault(akey, {"agent_id": akey,
                                       "agent_name": r["agent_name"] or "未归属",
                                       "dept_id": r["dept_id"],
                                       "dept_name": r["dept_name"] or "未分配",
                                       "tokens": 0, "cost": 0.0, "runs": 0})
        a["tokens"] += tokens
        a["cost"] += cost
        a["runs"] += 1

        dkey = r["dept_id"] or "LE-UNKNOWN"
        d = by_dept.setdefault(dkey, {"dept_id": dkey, "dept_name": r["dept_name"] or "未分配",
                                      "tokens": 0, "cost": 0.0, "runs": 0, "agents": set()})
        d["tokens"] += tokens
        d["cost"] += cost
        d["runs"] += 1
        d["agents"].add(akey)

        m = by_model.setdefault(model, {"model": model, "tokens": 0, "cost": 0.0, "runs": 0})
        m["tokens"] += tokens
        m["cost"] += cost
        m["runs"] += 1

        by_day[day]["date"] = day
        by_day[day]["tokens"] += tokens
        by_day[day]["cost"] += cost

    # 补齐日期，前端画柱状图不用自己补空
    series = []
    import datetime as _dt
    today = _dt.date.today()
    for i in range(days - 1, -1, -1):
        d = (today - _dt.timedelta(days=i)).isoformat()
        item = by_day.get(d) or {"date": d, "tokens": 0, "cost": 0.0}
        series.append({"date": d, "tokens": int(item["tokens"]),
                       "cost": round(item["cost"], 2)})

    for coll in (by_agent, by_dept, by_model):
        for v in coll.values():
            v["cost"] = round(v["cost"], 2)
            v["tokens"] = int(v["tokens"])
            if isinstance(v.get("agents"), set):
                v["agents"] = len(v["agents"])

    pool = conn.execute(
        """SELECT COALESCE(SUM(limit_tokens),0) AS lim, COALESCE(SUM(used_tokens),0) AS used
           FROM budgets WHERE task_id IS NULL AND identity_id IS NULL
             AND COALESCE(limit_tokens,0) > 0""").fetchone()
    lim, used = int(pool["lim"] or 0), int(pool["used"] or 0)
    default_blend = blended_price(default_model())
    budget = {"limit_tokens": lim, "used_tokens": used,
              "left_tokens": max(0, lim - used),
              "limit_cny": round(lim * default_blend / 1_000_000, 2),
              "used_cny": round(used * default_blend / 1_000_000, 2)}

    return {
        "days": days,
        "currency": "CNY",
        "totals": {"tokens": int(total_tokens), "cost": round(total_cost, 2),
                   "runs": len(runs), "agents": len(by_agent),
                   "avg_run_cost": round(total_cost / len(runs), 2) if runs else 0},
        "by_agent": sorted(by_agent.values(), key=lambda x: -x["cost"]),
        "by_dept": sorted(by_dept.values(), key=lambda x: -x["cost"]),
        "by_model": sorted(by_model.values(), key=lambda x: -x["cost"]),
        "series": series,
        "budget": budget,
        "pricing": {"input_ratio": input_ratio(), "default_model": default_model(),
                    "models": [{"model": k, "in": v["in"], "out": v["out"]}
                               for k, v in sorted(prices().items())],
                    "note": "按输入占比估算；真实账单以模型供应商为准"},
        "recent": sorted(runs, key=lambda x: x["at"] or "", reverse=True)[:20],
    }
