"""Check that the WeCom middleware is wired end to end.

    python scripts/wecom_selfcheck.py

Verifies: env config, callback handshake (real AES round-trip against the running
server), user binding, watcher state, and what is currently in the outbox.
"""
from __future__ import annotations

import pathlib
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.wecom import crypto  # noqa: E402

BASE = "http://127.0.0.1:8077"


def env() -> dict:
    return dict(line.split("=", 1)
                for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
                if "=" in line and not line.startswith("#"))


def main() -> int:
    e = env()
    sim = {"X-Wecom-Token": e.get("WECOM_SIM_TOKEN", "")}
    ok = True

    def line(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(("  ✓ " if passed else "  ✗ ") + f"{name}" + (f" — {detail}" if detail else ""))

    print("1) 配置")
    for key in ("WECOM_CORP_ID", "WECOM_AGENT_ID", "WECOM_SECRET", "WECOM_TOKEN",
                "WECOM_AES_KEY", "WECOM_CALLBACK_URL"):
        val = e.get(key, "")
        line(key, bool(val), "已配置" if val else "未配置（mock 模式可留空；真实企微必填）")
    mock = e.get("WECOM_MOCK") == "1"
    line("WECOM_MOCK", not mock or not e.get("WECOM_SECRET"),
         f"mock={e.get('WECOM_MOCK')}" + ("（仍为 mock：不会真发消息）" if mock else "（真实发送）"))

    print("2) 回调握手（真实 AES 往返）")
    try:
        enc = crypto.encrypt("selfcheck", e.get("WECOM_AES_KEY", ""), e.get("WECOM_CORP_ID", ""))
        ts, nonce = "1700000000", "selfcheck"
        sig = crypto.signature(e.get("WECOM_TOKEN", ""), ts, nonce, enc)
        r = httpx.get(f"{BASE}/wecom/callback", trust_env=False, timeout=10, params={
            "msg_signature": sig, "timestamp": ts, "nonce": nonce, "echostr": enc})
        line("GET /wecom/callback", r.status_code == 200 and r.text == "selfcheck",
             f"status={r.status_code}")
    except Exception as exc:                       # noqa: BLE001
        line("GET /wecom/callback", False, str(exc))

    print("3) 绑定与看板")
    try:
        st = httpx.get(f"{BASE}/wecom/status", headers=sim, trust_env=False, timeout=10).json()
        line("watcher 在跑", st["watcher"]["running"], str(st["watcher"]))
        line("已绑定用户", bool(st["users"]), str(st["users"]))
        line("已推送卡片", st["pushes"] > 0, f"{st['pushes']} 条")
        line("callback url", bool(st["callback_url"]), st["callback_url"] or "未设置")
    except Exception as exc:                       # noqa: BLE001
        line("status", False, str(exc))

    print("4) 最近发出的消息")
    try:
        ob = httpx.get(f"{BASE}/wecom/outbox?limit=3", headers=sim, trust_env=False,
                       timeout=10).json()
        for m in ob["messages"]:
            print(f"  · {m['msgtype']} -> {m['to_user']}: {str(m['body'])[:80]}…")
    except Exception as exc:                       # noqa: BLE001
        print(f"  · 读不到 outbox: {exc}")

    print("\n结论:", "全部通过" if ok else "有项目未通过（见上）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
