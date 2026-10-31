"""Probe the ACP handshake *through* the Low-IL sandbox launcher.

Prints exactly what comes back on stdout/stderr so we can tell whether the child
even started, whether it inherited the pipes, and where it stalls.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine import dsh_adapter as base          # noqa: E402

WS = ROOT / "data" / "sandbox-probe"
WS.mkdir(parents=True, exist_ok=True)
for p in WS.iterdir():
    if p.is_file():
        p.unlink()

cmd = [base.DSH_CLI, "--profile", "acp"]
if os.environ.get("NO_SANDBOX") != "1":
    cmd = base.sandbox_cmd(WS, cmd)

print("cmd:", cmd[:3], "...", f"({len(cmd)} args)")
env = dict(os.environ)
proc = subprocess.Popen(cmd, cwd=str(WS), env=env,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, encoding="utf-8",
                        errors="replace", bufsize=1)

hello = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": 1,
                               "clientCapabilities": {"fs": {"readTextFile": False,
                                                             "writeTextFile": False},
                                                      "terminal": False}}})
print("--> initialize")
assert proc.stdin
proc.stdin.write(hello + "\n")
proc.stdin.flush()

deadline = time.time() + 25
got = False
while time.time() < deadline:
    line = proc.stdout.readline() if proc.stdout else ""
    if not line:
        if proc.poll() is not None:
            print("child exited early rc=", proc.returncode)
            break
        time.sleep(0.2)
        continue
    print("<--", line.rstrip()[:300])
    got = True
    if '"id": 1' in line or '"id":1' in line:
        break

if not got:
    print("NO RESPONSE in 25s; alive=", proc.poll() is None)
    try:
        err = proc.stderr.read() if proc.stderr else ""
        print("stderr:", (err or "")[:800])
    except Exception:                    # noqa: BLE001
        pass

try:
    proc.kill()
except Exception:                        # noqa: BLE001
    pass
