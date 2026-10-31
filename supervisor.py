"""AgentOS Dual-Track Supervisor & Hot-Swap Gateway (红绿双轨自愈与热切换引擎).

架构特性：
1. 监听统一固定端口：8077 (用户始终通过 http://127.0.0.1:8077/ui/ 访问，无需改动端口)。
2. 后台双轨运行：
   - 🟢 绿进程 (Green Track): 127.0.0.1:8081
   - 🔴 红进程 (Red Track): 127.0.0.1:8082
3. 共享 SQLite WAL 数据库，会话与状态完全同源持久化。
4. 提供 /v1/supervisor/status、/v1/supervisor/switch、/v1/supervisor/restart_standby 接口。
5. 所有 HTTP 与 WebSocket 流量根据当前活跃轨道透明瞬时转发。
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from typing import Any

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import httpx
import uvicorn
import websockets

GATEWAY_PORT = 8077
GREEN_PORT = 8081
RED_PORT = 8082

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON_EXE = os.path.join(ROOT_DIR, ".venv", "Scripts", "python.exe")
if not os.path.exists(PYTHON_EXE):
    PYTHON_EXE = sys.executable

app = FastAPI(title="AgentOS-Supervisor", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SupervisorState:
    def __init__(self) -> None:
        self.active_track: str = "green"  # "green" | "red"
        self.green_proc: subprocess.Popen | None = None
        self.red_proc: subprocess.Popen | None = None
        self.green_started: float = 0.0
        self.red_started: float = 0.0
        self.http_client: httpx.AsyncClient | None = None

    def get_active_port(self) -> int:
        return GREEN_PORT if self.active_track == "green" else RED_PORT

    def get_standby_track(self) -> str:
        return "red" if self.active_track == "green" else "green"

    def get_standby_port(self) -> int:
        return RED_PORT if self.active_track == "green" else GREEN_PORT


state = SupervisorState()


def _spawn_backend(port: int) -> subprocess.Popen:
    cmd = [
        PYTHON_EXE,
        "-X",
        "utf8",
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT_DIR,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


async def _wait_until_healthy(port: int, max_wait: float = 12.0) -> bool:
    start = time.time()
    url = f"http://127.0.0.1:{port}/health"
    async with httpx.AsyncClient(timeout=1.5) as client:
        while time.time() - start < max_wait:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.3)
    return False


def _terminate_proc(proc: subprocess.Popen | None) -> None:
    if not proc:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            proc.terminate()
            proc.wait(timeout=2.0)
    except Exception:
        pass


@app.on_event("startup")
async def on_startup() -> None:
    state.http_client = httpx.AsyncClient(timeout=60.0)

    # 启动 🟢 绿进程 (Port 8081)
    state.green_proc = _spawn_backend(GREEN_PORT)
    state.green_started = time.time()

    # 启动 🔴 红进程 (Port 8082)
    state.red_proc = _spawn_backend(RED_PORT)
    state.red_started = time.time()

    # 等待至少绿进程就绪
    ok = await _wait_until_healthy(GREEN_PORT, max_wait=10.0)
    print(f"[*] Dual-Track: Green Track (Port {GREEN_PORT}) ready: {ok}")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if state.http_client:
        await state.http_client.aclose()
    _terminate_proc(state.green_proc)
    _terminate_proc(state.red_proc)


# ---------------------------------------------------------------- 管理端点
@app.get("/v1/supervisor/status")
async def supervisor_status() -> dict[str, Any]:
    green_alive = state.green_proc and state.green_proc.poll() is None
    red_alive = state.red_proc and state.red_proc.poll() is None

    return {
        "ok": True,
        "active": state.active_track,
        "active_port": state.get_active_port(),
        "standby": state.get_standby_track(),
        "standby_port": state.get_standby_port(),
        "green": {
            "track": "green",
            "name": "绿进程",
            "color": "#35e08a",
            "port": GREEN_PORT,
            "alive": bool(green_alive),
            "pid": state.green_proc.pid if state.green_proc else None,
            "uptime_seconds": int(time.time() - state.green_started) if state.green_started else 0,
            "role": "主进程" if state.active_track == "green" else "备用热备",
        },
        "red": {
            "track": "red",
            "name": "红进程",
            "color": "#ff5d73",
            "port": RED_PORT,
            "alive": bool(red_alive),
            "pid": state.red_proc.pid if state.red_proc else None,
            "uptime_seconds": int(time.time() - state.red_started) if state.red_started else 0,
            "role": "主进程" if state.active_track == "red" else "备用热备",
        },
    }


@app.post("/v1/supervisor/switch")
async def supervisor_switch() -> dict[str, Any]:
    prev = state.active_track
    target = "red" if prev == "green" else "green"
    target_port = RED_PORT if target == "red" else GREEN_PORT

    # 确认目标进程活着
    target_proc = state.red_proc if target == "red" else state.green_proc
    if not target_proc or target_proc.poll() is not None:
        # 重启该进程
        if target == "red":
            state.red_proc = _spawn_backend(RED_PORT)
            state.red_started = time.time()
        else:
            state.green_proc = _spawn_backend(GREEN_PORT)
            state.green_started = time.time()
        await _wait_until_healthy(target_port, max_wait=5.0)

    state.active_track = target
    return {
        "ok": True,
        "active": target,
        "previous": prev,
        "port": target_port,
        "message": f"已成功平滑切换至 {('红进程' if target == 'red' else '绿进程')} (Port {target_port})",
    }


@app.post("/v1/supervisor/restart_standby")
async def restart_standby() -> dict[str, Any]:
    standby = state.get_standby_track()
    standby_port = state.get_standby_port()

    if standby == "green":
        _terminate_proc(state.green_proc)
        state.green_proc = _spawn_backend(GREEN_PORT)
        state.green_started = time.time()
    else:
        _terminate_proc(state.red_proc)
        state.red_proc = _spawn_backend(RED_PORT)
        state.red_started = time.time()

    ok = await _wait_until_healthy(standby_port, max_wait=8.0)
    return {
        "ok": ok,
        "standby": standby,
        "port": standby_port,
        "message": f"备用轨道 {standby} 重启完成，健康自检：{'正常通过' if ok else '等待超时'}",
    }


# ---------------------------------------------------------------- 透明转发 (HTTP & WebSocket)
@app.websocket("/{full_path:path}")
async def proxy_websocket(websocket: WebSocket, full_path: str) -> None:
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

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_http(request: Request, full_path: str) -> Response:
    target_port = state.get_active_port()
    target_url = f"http://127.0.0.1:{target_port}/{full_path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    req_headers = dict(request.headers)
    req_headers.pop("host", None)
    req_headers.pop("content-length", None)

    body = await request.body()
    try:
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
    except Exception as exc:
        return Response(
            content=f'{{"error": "Gateway proxy error: {exc}", "target_port": {target_port}}}',
            status_code=502,
            media_type="application/json",
        )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=GATEWAY_PORT, log_level="info")
