"""M0.5 demo: five decision packets through the CEO filter, no model calls.

Shows the three outcomes that matter: auto-approved, manager, CEO — plus the
finance override and the full audit trail of one CEO decision.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from oa_client import client_from_env  # noqa: E402

C = client_from_env()


def packet(title: str, **kw) -> dict:
    body = {"title": title, "amount": 1000.0, "risk_level": "low",
            "reversibility": "reversible", "confidence": 0.9}
    body.update(kw)
    d = C.post("/decisions", json=body).json()
    out = C.post(f"/decisions/{d['id']}/evaluate").json()
    fin = out["finance"][-1]
    print(f"{title:<22} 金额 {out['amount']:>9.0f}  ->  {out['state']:<18}"
          f" 规则 {str(out['matched_rule']):<6} 财务 {fin['verdict']}")
    return out


def main() -> int:
    print("=== 决策过滤 ===")
    small = packet("小额可逆投放", amount=1200.0)
    packet("合规条款变更", amount=800.0, is_compliance=1)
    packet("大额采购", amount=60000.0)
    packet("中额试验", amount=30000.0, risk_level="medium")
    over = packet("超额追加预算", amount=120000.0)

    inbox = C.get("/decisions", params={"state": "escalated_ceo"}).json()
    print(f"\nCEO 收件箱：{inbox['count']} 条，只有过线决策进来")
    for d in inbox["decisions"]:
        print(f"  - {d['title']:<10} {d['amount']:>9.0f}  {d['matched_rule']}  {d['filter_reason']}")

    assert small["state"] == "auto_approved", small["state"]
    assert over["finance"][-1]["verdict"] == "over_budget"

    print("\n=== CEO 批一条，财务轨结算 ===")
    target = next(d for d in inbox["decisions"] if d["title"] == "大额采购")
    decided = C.post(f"/decisions/{target['id']}/decide",
                     json={"approved": True, "decided_by": "ceo", "note": "预算内，批"}).json()
    print("state:", decided["state"], "decided_by:", decided["decided_by"])
    trail = [(a["verb"], json.loads(a["payload"] or "{}")) for a in decided["audit"]]
    for verb, payload in trail:
        print(f"  audit {verb}: {str(payload)[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
