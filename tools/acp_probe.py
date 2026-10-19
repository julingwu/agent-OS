"""Probe the DSH ACP surface: initialize -> session/new -> session/prompt.

Prints every JSON-RPC message with a timestamp so the real protocol shape is
recorded rather than assumed.
"""
from __future__ import annotations

import json
import os
import pathlib
import queue
import subprocess
import sys
import threading
import time

DSH_CLI = os.environ.get(
    "DSH_CLI",
    str(pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DeepSeek Harness"
        / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd"),
)

messages: "queue.Queue[dict]" = queue.Queue()


def reader(stream) -> None:
    for raw in stream:
        line = raw.strip()
        if not line:
            continue
        try:
            messages.put(json.loads(line))
        except json.JSONDecodeError:
            messages.put({"__raw__": line[:300]})


def main() -> int:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "只回复 OK 两个字母"
    proc = subprocess.Popen(
        [DSH_CLI, "--profile", "acp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1, shell=False,
    )
    threading.Thread(target=reader, args=(proc.stdout,), daemon=True).start()
    threading.Thread(target=lambda: [sys.stderr.write(l) for l in proc.stderr], daemon=True).start()

    def send(obj: dict) -> None:
        proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def pump(seconds: float, label: str) -> list[dict]:
        out, end = [], time.time() + seconds
        while time.time() < end:
            try:
                out.append(messages.get(timeout=0.3))
            except queue.Empty:
                continue
        for m in out:
            print(f"[{label}] {json.dumps(m, ensure_ascii=False)[:400]}")
        return out

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": 1,
                     "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                            "terminal": False}}})
    pump(20, "init")

    send({"jsonrpc": "2.0", "id": 2, "method": "session/new",
          "params": {"cwd": str(pathlib.Path.cwd()), "mcpServers": []}})
    got = pump(20, "new")
    session_id = None
    for m in got:
        if m.get("id") == 2 and isinstance(m.get("result"), dict):
            session_id = m["result"].get("sessionId") or m["result"].get("session_id")
    print("SESSION", session_id)
    if not session_id:
        proc.kill()
        return 1

    send({"jsonrpc": "2.0", "id": 3, "method": "session/prompt",
          "params": {"sessionId": session_id,
                     "prompt": [{"type": "text", "text": prompt}]}})
    pump(90, "prompt")

    proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
