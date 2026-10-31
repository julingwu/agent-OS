"""Real-process smoke test using a temporary DB and three temporary local ports.

Run: .venv/Scripts/python.exe scripts/verify_controlled_release.py
No model calls, production database access, or production service restarts.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def free_ports():
    sockets = [socket.socket() for _ in range(3)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


async def verify(base, token):
    checks = []
    async with httpx.AsyncClient(base_url=base, timeout=30,
                                 headers={"Authorization": f"Bearer {token}"}) as client:
        async def status():
            response = await client.get("/v1/supervisor/status")
            response.raise_for_status()
            return response.json()

        deadline = time.monotonic() + 45
        while True:
            try:
                initial = await status()
                assert initial[initial["active"]]["ready"]
                break
            except (httpx.HTTPError, AssertionError):
                if time.monotonic() > deadline:
                    raise RuntimeError("gateway did not become ready")
                await asyncio.sleep(.25)
        checks.append("active backend ready")
        source = initial["active"]
        target = initial["standby"]
        # Establish both kinds of long-lived socket before promotion.
        ws_base = base.replace("http://", "ws://")
        # Keep protocol reads unpaused while this probe deliberately holds the
        # event socket without consuming every progress frame. Otherwise the
        # client's bounded queue can hide the close frame behind its backlog.
        async with websockets.connect(f"{ws_base}/v1/stream?token={token}", max_queue=None) as stream:
            assert json.loads(await stream.recv())["t"] == "hello"
            async with websockets.connect(f"{ws_base}/v1/device?token={token}") as device:
                await device.send(json.dumps({"t": "hello", "caps": ["exec", "info"]}))
                assert json.loads(await device.recv())["t"] == "hello_ack"
                proc = await client.post("/v1/processes", json={
                    "bot_id": "bot_chief", "session_id": "ses_chief",
                    "cmd": [sys.executable, "-c", "import time; time.sleep(8)"],
                    "name": "release-smoke-sleeper"})
                proc.raise_for_status()
                process_id = proc.json()["item"]["id"]
                response = await client.post("/v1/supervisor/restart_standby")
                response.raise_for_status()
                process = (await client.get(f"/v1/processes/{process_id}")).json()["item"]
                assert process["state"] == "running", process["state"]
                checks.append("standby restart leaves active process running")
                response = await client.post("/v1/supervisor/switch")
                assert response.status_code == 202
                deadline = time.monotonic() + 8
                while True:
                    waiting = await status()
                    if waiting["release"]["phase"] == "draining":
                        break
                    assert time.monotonic() < deadline
                    await asyncio.sleep(.1)
                assert waiting["active"] == source
                assert waiting[source]["runtime"]["processes"] == 1
                rejected = await client.post("/v1/sessions/ses_chief/messages", json={"text": "must not run"})
                assert rejected.status_code == 503
                checks.append("busy executor retained; new work rejected while draining")
                # Stopping existing work must remain available during drain.
                stopped = await client.post(f"/v1/processes/{process_id}/stop", json={})
                stopped.raise_for_status()
                deadline = time.monotonic() + 40
                while True:
                    current = await status()
                    if current["release"]["phase"] in ("completed", "failed", "rolled_back"):
                        assert current["release"]["phase"] == "completed", current["release"]
                        break
                    if time.monotonic() > deadline:
                        raise AssertionError(f"release did not drain: {current}")
                    await asyncio.sleep(.25)
                assert current["active"] == target
                await asyncio.wait_for(device.wait_closed(), 8)
                await asyncio.wait_for(stream.wait_closed(), 8)
                checks.append("promotion completed; old stream and device sockets closed")
        async with websockets.connect(f"{ws_base}/v1/device?token={token}") as device:
            await device.send(json.dumps({"t": "hello", "caps": ["exec", "info"]}))
            assert json.loads(await device.recv())["t"] == "hello_ack"
            devices = (await client.get("/v1/local/devices")).json()
            assert devices["count"] == 1
            checks.append("device reconnected to new active track")
        response = await client.post("/v1/sessions/ses_chief/messages", json={"text": "release verification"})
        response.raise_for_status()
        messages = (await client.get("/v1/sessions/ses_chief/messages")).json()["items"]
        assert any(m["text"] == "release verification" for m in messages)
        checks.append("new track accepts messages and reads persisted history")
        deadline = time.monotonic() + 15
        while (await client.get("/v1/sessions/ses_chief")).json()["running"]:
            assert time.monotonic() < deadline
            await asyncio.sleep(.1)
        # The test model requests release *inside* an actual Agent turn, then
        # finishes normally. The gateway must wait rather than kill that turn.
        previous_release = current["release"]["id"]
        response = await client.post("/v1/sessions/ses_chief/messages", json={"text": "self-release-check"})
        response.raise_for_status()
        deadline = time.monotonic() + 50
        saw_waiting = False
        while True:
            current = await status()
            operation = current.get("release")
            if operation and operation["id"] != previous_release:
                if operation["phase"] == "draining":
                    saw_waiting = True
                    assert current["active"] == target
                if operation["phase"] in ("completed", "failed", "rolled_back"):
                    assert operation["phase"] == "completed", operation
                    break
            if time.monotonic() > deadline:
                raise AssertionError(f"agent self-release did not finish: {current}")
            await asyncio.sleep(.15)
        assert saw_waiting
        assert current["active"] == source
        messages = (await client.get("/v1/sessions/ses_chief/messages")).json()["items"]
        assert any(m["text"] == "self-release task completed" for m in messages)
        checks.append("Agent requests release inside its own turn; gateway waits until it finishes")
        checks.append("both green-to-red and red-to-green releases completed")
        checks.append("no model/network inference calls (temporary deterministic model fixture)")
        return {"ok": True, "checks": checks, "release": current["release"]}


def main():
    with tempfile.TemporaryDirectory(prefix="agentos-release-") as directory:
        temp = Path(directory)
        ports = free_ports()
        env = os.environ.copy()
        env.pop("PYTEST_CURRENT_TEST", None)
        env.update(DB_PATH=str(temp / "test.db"), M0_WORKSPACE=str(temp / "workspace"),
                   AGENTOS_GATEWAY_PORT=str(ports[0]), AGENTOS_GREEN_PORT=str(ports[1]),
                   AGENTOS_RED_PORT=str(ports[2]), AGENTOS_RELEASE_STATE=str(temp / "release.json"),
                   WECOM_MOCK="1", WECOM_AUTORUN="0", WECOM_WATCHER="0",
                   WECOM_APPROVAL_POLLER="0", OCTO_RUNNER="0", OCTO_AUTORUN="0",
                   UI_CRON="0", ROUTINE_SCHEDULER="0", PROCESS_SUPERVISOR="1",
                   PROCESS_TICK="0.2", AGENT_AUTORUN="1", PYTHONUTF8="1")
        # Only these child processes see the fixture. No production source/config
        # or credentials are modified; no real model request can be issued.
        (temp / "sitecustomize.py").write_text('''
import os, time
from app import llm

def fake_stream(messages, **kwargs):
    self_release = messages[-1].get("content") == "self-release-check"
    if self_release:
        import httpx
        base = "http://127.0.0.1:" + os.environ["AGENTOS_GATEWAY_PORT"]
        with httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
            client.post("/v1/supervisor/restart_standby").raise_for_status()
            response = client.post("/v1/supervisor/switch")
            assert response.status_code == 202
        time.sleep(2)
    yield "final", {"message": {"content": "self-release task completed" if self_release else "smoke reply"}, "usage": {}}

llm.chat_stream = fake_stream
llm.chat = lambda *a, **kw: {"message": {"content": "[]"}, "usage": {}}
''', encoding="utf-8")
        env["PYTHONPATH"] = os.pathsep.join([str(temp), str(ROOT)])
        os.environ["DB_PATH"] = env["DB_PATH"]
        from app import db, devices
        db.init_db()
        with db.get_conn() as conn:
            token = devices.issue(conn, name="isolated-release-verifier")["token"]
        with (temp / "gateway.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, "-X", "utf8", "supervisor.py"],
                                       cwd=ROOT, env=env, stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            try:
                result = asyncio.run(verify(f"http://127.0.0.1:{ports[0]}", token))
                output = ROOT / "outputs" / "controlled-release-verification.json"
                output.parent.mkdir(exist_ok=True)
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(result, ensure_ascii=False, indent=2))
            finally:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    process.terminate()
                process.wait(timeout=15)


if __name__ == "__main__":
    main()
