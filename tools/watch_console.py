"""盯盘：每 20 秒打印一次任务/运行/群消息增量，专盯 ACP 与 npm 报错。

    python tools/watch_console.py            # 前台
    python tools/watch_console.py --once     # 打一拍就退
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3
import time

ROOT = pathlib.Path(r"C:\oaagent-m0")
DB = ROOT / "data" / "oa.db"
LOGS = ROOT / "data" / "logs"
RUNS = ROOT / "docs" / "runs"

DEP_HINTS = re.compile(
    r"(npm ERR|ERR!|EACCES|EPERM|Cannot find module|ENOENT|node_modules|"
    r"vite|webpack|pnpm|yarn|rollup|esbuild|postinstall)", re.I)


def snap() -> dict:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    out = {"tasks": [], "runs": [], "msgs": [], "errors": []}
    for r in c.execute("""SELECT id,title,state,pack_id,created_at FROM tasks
                          ORDER BY created_at DESC LIMIT 5"""):
        out["tasks"].append(dict(r))
    for r in c.execute("""SELECT id,task_id,state,usage_tokens,exit_code FROM task_runs
                          ORDER BY rowid DESC LIMIT 6"""):
        out["runs"].append(dict(r))
    for r in c.execute("""SELECT chat_id,author,kind,substr(content,1,90) t,created_at
                          FROM chat_messages ORDER BY id DESC LIMIT 6"""):
        out["msgs"].append(dict(r))
    for p in sorted(RUNS.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)[:3]:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[-40000:]
        except OSError:
            continue
        for line in text.splitlines()[-40:]:
            if DEP_HINTS.search(line):
                out["errors"].append({"file": p.name, "line": line[:200]})
                break
    c.close()
    return out


def show(s: dict) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"\n===== {ts} =====", flush=True)
    print("任务:", flush=True)
    for t in s["tasks"][:3]:
        print(f"  {t['id']} {t['state']:<12} {t['pack_id']:<10} {(t['title'] or '')[:38]}", flush=True)
    print("运行:", flush=True)
    for r in s["runs"][:3]:
        print(f"  {r['id']} {r['state']:<10} {str(r['usage_tokens']).rjust(6)} tok  exit={r['exit_code']}", flush=True)
    print("最新消息:", flush=True)
    for m in s["msgs"][:4]:
        print(f"  [{m['author'] or '系统'}/{m['kind']}] {(m['t'] or '')[:70]}", flush=True)
    if s["errors"]:
        print("⚠ 依赖/构建报错:", flush=True)
        for e in s["errors"]:
            print(f"  {e['file']}: {e['line'][:160]}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=20)
    a = ap.parse_args()
    last = None
    while True:
        s = snap()
        key = json.dumps(s, ensure_ascii=False, sort_keys=True)
        if a.once or key != last:
            show(s)
            last = key
        if a.once:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
