"""Single-executor dual-track gateway with persisted, drain-before-switch releases.

8077 is the only public entry. Standby validates imports/database access only.
Promotion waits for active work, restarts the candidate as sole executor, and
reconnects sockets. This is controlled maintenance, not live thread migration.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from typing import Any

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import JSONResponse
import httpx
import uvicorn
import websockets
from app.config import load_env

ROOT_DIR = str(Path(__file__).resolve().parent)
GATEWAY_PORT = int(os.environ.get("AGENTOS_GATEWAY_PORT", "8077"))
GREEN_PORT = int(os.environ.get("AGENTOS_GREEN_PORT", "8081"))
RED_PORT = int(os.environ.get("AGENTOS_RED_PORT", "8082"))
PYTHON_EXE = str(Path(ROOT_DIR) / ".venv" / "Scripts" / "python.exe")
if not Path(PYTHON_EXE).exists():
    PYTHON_EXE = sys.executable
CONTROL_TOKEN = secrets.token_urlsafe(32)
STATE_PATH = Path(os.environ.get("AGENTOS_RELEASE_STATE") or Path(ROOT_DIR) / "data" / "supervisor-release.json")
app = FastAPI(title="AgentOS-Supervisor", docs_url=None, redoc_url=None)


class SupervisorState:
    def __init__(self):
        self.active_track = "green"
        self.green_proc = self.red_proc = None
        self.green_started = self.red_started = 0.0
        self.http_client = None
        self.lock = asyncio.Lock()
        self.release = None
        self.prepared = None
        self.maintenance = False
        self.inflight = 0
        self.sockets = set()
        self.worker = None
        self.running_revision = None

    def get_active_port(self):
        return port_for(self.active_track)

    def get_standby_track(self):
        return "red" if self.active_track == "green" else "green"

    def get_standby_port(self):
        return port_for(self.get_standby_track())


state = SupervisorState()


def port_for(track):
    return GREEN_PORT if track == "green" else RED_PORT


def save_state():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump({"active": state.active_track, "release": state.release}, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, STATE_PATH)


def fingerprint():
    digest = hashlib.sha256()
    root = Path(ROOT_DIR)
    files = [root / "supervisor.py"]
    files += list((root / "app").rglob("*.py"))
    for folder in (root / "ui_dist", root / "web" / "dist"):
        if folder.exists():
            files += [p for p in folder.rglob("*") if p.is_file()]
    for path in sorted(files):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _spawn_backend(port, role="standby"):
    env = os.environ.copy()
    instance_id = secrets.token_hex(16)
    env.update(AGENTOS_TRACK_ROLE=role, AGENTOS_CONTROL_TOKEN=CONTROL_TOKEN,
               AGENTOS_INSTANCE_ID=instance_id,
               DB_PATH=os.environ.get("DB_PATH") or str(Path(ROOT_DIR) / "data" / "oa.db"))
    logdir = Path(ROOT_DIR) / "logs"
    logdir.mkdir(exist_ok=True)
    with (logdir / f"backend-{port}.log").open("ab") as log:
        proc = subprocess.Popen(
            [PYTHON_EXE, "-X", "utf8", "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=ROOT_DIR, env=env, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0)
        # Windows venv python.exe may launch the interpreter under a different PID.
        proc.instance_id = instance_id
        return proc


def spawn(track, role):
    proc = _spawn_backend(port_for(track), role)
    setattr(state, track + "_proc", proc)
    setattr(state, track + "_started", time.time())
    return proc


def _terminate_proc(proc):
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        proc.terminate()
    proc.wait(timeout=10)


async def runtime(track, action=None):
    client = state.http_client
    url = f"http://127.0.0.1:{port_for(track)}/internal/runtime"
    headers = {"X-AgentOS-Control": CONTROL_TOKEN}
    response = await client.request("POST" if action else "GET",
                                    url + ("/" + action if action else ""), headers=headers, timeout=5)
    response.raise_for_status()
    data = response.json()
    if not action:
        proc = getattr(state, track + "_proc")
        if (not proc or proc.poll() is not None or not data.get("ok")
                or data.get("instance_id") != proc.instance_id):
            raise RuntimeError("backend identity/readiness mismatch")
    return data


async def _wait_until_healthy(port, max_wait=20.0, role=None):
    track = "green" if port == GREEN_PORT else "red"
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            info = await runtime(track)
            if role is None or info.get("role") == role:
                return True
        except Exception:
            pass
        await asyncio.sleep(.25)
    return False


@app.on_event("startup")
async def on_startup():
    load_env()
    state.running_revision = await asyncio.to_thread(fingerprint)
    state.http_client = httpx.AsyncClient(timeout=60)
    if STATE_PATH.exists():
        saved = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if saved.get("active") in ("green", "red"):
            state.active_track = saved["active"]
        state.release = saved.get("release")
        if state.release and state.release.get("phase") not in ("completed", "failed", "rolled_back"):
            state.release.update(phase="failed", message="网关重启中断发布；已恢复记录中的主轨，请重新验证备用轨。")
    # Refuse occupied ports: never mistake an unrelated backend for our child.
    import socket
    for port in (GREEN_PORT, RED_PORT):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"backend port {port} already occupied")
    try:
        spawn(state.active_track, "active")
        if not await _wait_until_healthy(state.get_active_port(), role="active"):
            raise RuntimeError("active backend readiness failed; inspect logs/backend-*.log")
        spawn(state.get_standby_track(), "standby")
        save_state()
        state.worker = asyncio.create_task(release_worker())
    except BaseException:
        _terminate_proc(state.green_proc)
        _terminate_proc(state.red_proc)
        await state.http_client.aclose()
        raise


@app.on_event("shutdown")
async def on_shutdown():
    if state.worker:
        state.worker.cancel()
        await asyncio.gather(state.worker, return_exceptions=True)
    for ws in list(state.sockets):
        await ws.close(code=1012)
    _terminate_proc(state.green_proc)
    _terminate_proc(state.red_proc)
    if state.http_client:
        await state.http_client.aclose()


def pending():
    return state.release and state.release["phase"] not in ("completed", "failed", "rolled_back")


@app.get("/v1/supervisor/status")
async def supervisor_status():
    # One coherent view: do not combine the old active track with a new phase
    # while readiness probes yield to a concurrent promotion.
    async with state.lock:
        return copy.deepcopy(await _supervisor_status())


async def _supervisor_status():
    result = {"ok": True, "mode": "drain-before-switch", "active": state.active_track,
              "project_root": str(Path(ROOT_DIR).parent), "gateway_pid": os.getpid(),
              "running_revision": state.running_revision,
              "active_port": state.get_active_port(), "standby": state.get_standby_track(),
              "standby_port": state.get_standby_port(), "release": state.release,
              "maintenance": state.maintenance, "prepared": state.prepared}
    for track, color in (("green", "#35e08a"), ("red", "#ff5d73")):
        proc = getattr(state, track + "_proc")
        info = {"track": track, "port": port_for(track), "color": color,
                "name": "绿进程" if track == "green" else "红进程",
                "pid": proc.pid if proc else None, "alive": bool(proc and proc.poll() is None),
                "role": "主进程" if track == state.active_track else "只读验证备用轨",
                "uptime_seconds": int(time.time() - getattr(state, track + "_started"))}
        try:
            info["runtime"] = await runtime(track)
            info["ready"] = True
        except Exception:
            info["ready"] = False
        result[track] = info
    return result


@app.post("/v1/supervisor/restart_standby")
async def restart_standby():
    async with state.lock:
        if pending() or state.maintenance:
            return JSONResponse({"ok": False, "message": "发布进行中，请等待完成。"}, status_code=409)
        track = state.get_standby_track()
        state.prepared = None
        revision = await asyncio.to_thread(fingerprint)
        await asyncio.to_thread(_terminate_proc, getattr(state, track + "_proc"))
        proc = spawn(track, "standby")
        ok = await _wait_until_healthy(port_for(track), role="standby")
        ok = ok and revision == await asyncio.to_thread(fingerprint)
        if ok:
            info = await runtime(track)
            state.prepared = {"track": track, "pid": info["pid"], "revision": revision}
        return JSONResponse({"ok": ok, "standby": track, "revision": revision,
                             "message": "备用轨导入与数据库检查通过，可提交受控切换。" if ok else "备用轨验证失败或验证期间代码变化；主轨保持不变。"},
                            status_code=200 if ok else 503)


@app.post("/v1/supervisor/switch")
async def supervisor_switch():
    async with state.lock:
        if pending():
            return JSONResponse({"ok": True, "accepted": True, "release": state.release,
                                 "message": "已有发布请求，正在等待当前任务结束。"}, status_code=202)
        target = state.get_standby_track()
        prepared = state.prepared
        if not prepared or prepared["track"] != target:
            return JSONResponse({"ok": False, "message": "请先重启并验证备用轨。"}, status_code=409)
        try:
            info = await runtime(target)
            valid = (info["role"] == "standby" and info["pid"] == prepared["pid"]
                     and prepared["revision"] == await asyncio.to_thread(fingerprint))
        except Exception:
            valid = False
        if not valid:
            state.prepared = None
            return JSONResponse({"ok": False, "message": "备用轨不健康或代码已变化，请重新验证。"}, status_code=409)
        state.release = {"id": secrets.token_hex(8), "source": state.active_track,
                         "target": target, "revision": prepared["revision"], "phase": "queued",
                         "message": "切换已排队；当前任务完成后发布，尚未切换。"}
        save_state()
        return JSONResponse({"ok": True, "accepted": True, "release": state.release,
                             "message": state.release["message"]}, status_code=202)


def phase(value, message):
    state.release.update(phase=value, message=message)
    save_state()


async def release_step():
    async with state.lock:
        if not pending():
            return
        source, target = state.release["source"], state.release["target"]
        if state.release["phase"] == "queued":
            await runtime(source, "drain")
            phase("draining", "等待任务、工具调用、审批和后台进程结束；新任务暂缓。")
        info = await runtime(source)
        state.release["busy"] = {k: info[k] for k in ("sessions", "processes", "pending_calls", "workers")}
        if info["busy"] or state.inflight:
            return
        # All task admission and schedulers are stopped. No new executor work can start.
        state.maintenance = True
        phase("promoting", "当前任务已结束，正在受控切换并重连客户端。")
        try:
            if state.release["revision"] != await asyncio.to_thread(fingerprint):
                raise RuntimeError("candidate changed after validation")
            await asyncio.to_thread(_terminate_proc, getattr(state, target + "_proc"))
            await asyncio.to_thread(_terminate_proc, getattr(state, source + "_proc"))
            spawn(target, "active")
            if not await _wait_until_healthy(port_for(target), role="active"):
                raise RuntimeError("candidate active readiness failed")
            if state.release["revision"] != await asyncio.to_thread(fingerprint):
                raise RuntimeError("candidate changed during promotion")
            state.active_track = target
            state.prepared = None
            phase("completed", "受控切换完成；客户端连接将重建。")
            spawn(source, "standby")
        except Exception as exc:
            state.active_track = source
            state.prepared = None
            state.release["error"] = str(exc)
            try:
                await asyncio.to_thread(_terminate_proc, getattr(state, target + "_proc"))
                await asyncio.to_thread(_terminate_proc, getattr(state, source + "_proc"))
                spawn(source, "active")
                ok = await _wait_until_healthy(port_for(source), role="active")
            except Exception as recovery_error:
                state.release["recovery_error"] = str(recovery_error)
                ok = False
            phase("rolled_back" if ok else "failed",
                  f"发布失败（{type(exc).__name__}）；" + ("已恢复原轨。" if ok else "原轨恢复失败，请检查后端日志。"))
        finally:
            for ws in list(state.sockets):
                try:
                    await ws.close(code=1012)
                except Exception:
                    pass
            state.maintenance = False


async def release_worker():
    while True:
        try:
            await release_step()
        except Exception as exc:
            # Fail closed: keep current routing, expose the issue, never silently kill busy work.
            if pending():
                state.release["message"] = f"发布等待中：运行状态检查失败（{type(exc).__name__}），未强制切换。"
                save_state()
        await asyncio.sleep(1)


# ---------------------------------------------------------------- 透明转发 (HTTP & WebSocket)
@app.websocket("/{full_path:path}")
async def proxy_websocket(websocket: WebSocket, full_path: str) -> None:
    if state.maintenance or full_path.startswith("internal/"):
        await websocket.close(code=1013)
        return
    state.sockets.add(websocket)
    target_port = state.get_active_port()
    query_str = websocket.scope.get("query_string", b"").decode("utf-8")
    target_url = f"ws://127.0.0.1:{target_port}/{full_path}"
    if query_str:
        target_url += f"?{query_str}"

    headers: dict[str, str] = {}
    for k, v in websocket.headers.items():
        if k.lower() not in (
            "upgrade",
            "connection",
            "sec-websocket-key",
            "sec-websocket-version",
            "sec-websocket-extensions",
            "host",
        ):
            headers[k] = v

    connect_kwargs = {"max_size": 16 * 1024 * 1024}
    if headers:
        import inspect
        sig = inspect.signature(websockets.connect)
        if "additional_headers" in sig.parameters:
            connect_kwargs["additional_headers"] = headers
        elif "extra_headers" in sig.parameters:
            connect_kwargs["extra_headers"] = headers

    await websocket.accept()

    try:
        async with websockets.connect(target_url, **connect_kwargs) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg["type"] == "websocket.receive":
                            if "text" in msg:
                                await upstream.send(msg["text"])
                            elif "bytes" in msg:
                                await upstream.send(msg["bytes"])
                        elif msg["type"] == "websocket.disconnect":
                            await upstream.close()
                            break
                except Exception:
                    pass

            async def upstream_to_client():
                try:
                    async for message in upstream:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                except Exception:
                    pass

            tasks = [asyncio.create_task(client_to_upstream()),
                     asyncio.create_task(upstream_to_client())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass

    finally:
        try:
            await websocket.close(code=1012)
        except Exception:
            pass
        state.sockets.discard(websocket)


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_http(request: Request, full_path: str) -> Response:
    if full_path.startswith("internal/"):
        return JSONResponse({"detail": "private endpoint"}, status_code=404)
    if state.maintenance:
        return JSONResponse({"detail": "controlled release in progress"}, status_code=503,
                            headers={"Retry-After": "2"})
    target_port = state.get_active_port()
    target_url = f"http://127.0.0.1:{target_port}/{full_path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    req_headers = dict(request.headers)
    req_headers.pop("host", None)
    req_headers.pop("content-length", None)

    state.inflight += 1
    try:
        body = await request.body()
        client = state.http_client or httpx.AsyncClient(timeout=60.0)
        resp = await client.request(
            method=request.method,
            url=target_url,
            headers=req_headers,
            content=body,
            follow_redirects=True,
        )

        excluded = ("content-length", "content-encoding", "transfer-encoding", "connection")
        resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded}
        if full_path in ("ui", "ui/", "ui/index.html") or full_path.endswith(".html"):
            resp_headers["cache-control"] = "no-cache, no-store, must-revalidate"
            resp_headers["pragma"] = "no-cache"
        return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)
    except Exception:
        return JSONResponse({"error": "backend unavailable", "target_port": target_port}, status_code=502)
    finally:
        state.inflight -= 1


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=GATEWAY_PORT, log_level="info")
