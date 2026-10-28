"""FastAPI entry point for the M0 shell."""
from __future__ import annotations

import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
import asyncio
from .mcp_bridge import api as mcp_bridge_api
from .telemetry import bus
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import load_env

# Runner/model modules resolve launch settings while importing.
load_env()

from .api import decisions, memory, relay, reports, tasks, verbs
from .api_v1 import bots as v1_bots
from .api_v1 import bot_memory as v1_bot_memory
from .api_v1 import routines as v1_routines
from .api_v1 import skills as v1_skills
from .api_v1 import processes as v1_processes
from .api_v1 import artifacts as v1_artifacts
from .api_v1 import computer as v1_computer
from .api_v1 import legacy_sso as v1_legacy_sso
from .api_v1 import local as v1_local
from .api_v1 import misc as v1_misc
from .api_v1 import pairing as v1_pairing
from .api_v1 import search as v1_search
from .api_v1 import stream as v1_stream
from .api_v1 import workspace as v1_workspace
from .octo import api as octo_api
from .ui import api as ui_api
from .ui import admin as ui_admin
from .ui import ops as ui_ops
from .ui import store as ui_store
from .ui import access as ui_access
from .ui import extensions as ui_extensions
from .ui import governance as ui_governance
from .ui import housekeeping as ui_housekeeping
from .octo import runner as octo_runner
from .wecom import api as wecom_api
from .wecom import sign_page
from .wecom import watcher as wecom_watcher
from .db import connect, init_db

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = pathlib.Path(__file__).resolve().parent / "static"

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        bus.set_loop(asyncio.get_running_loop())
    except Exception:
        pass
    try:
        from . import local_bridge as _local_bridge
        _local_bridge.set_loop(asyncio.get_running_loop())
    except Exception:
        pass
    from . import flows, mcp, mailer, notify, pipeline
    from .wecom import robot
    with connect() as conn:
        mcp.ensure(conn)
        mailer.ensure(conn)
        notify.ensure(conn)
        robot.ensure(conn)
        pipeline.ensure(conn)
        flows.ensure(conn)
        pipeline.recover_interrupted(conn)
    if os.environ.get("WECOM_WATCHER", "0") == "1":
        wecom_watcher.start(interval=float(os.environ.get("WECOM_TICK", "3")))
    # Background threads must never start under pytest: they would leak across
    # tests and poll the real corp with the developer's credentials.
    _under_test = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    try:
        from . import routines as _routines, skills as _skills
        with connect() as _c:
            _routines.ensure(_c)
            _skills.ensure(_c)
        if os.environ.get("ROUTINE_SCHEDULER", "1") == "1" and not _under_test:
            _routines.start_scheduler(float(os.environ.get("ROUTINE_TICK", "20")))
    except Exception as _exc:                    # noqa: BLE001
        print(f"[routine] scheduler not started: {_exc}")
    _processes = None
    try:
        from . import processes as _processes_mod
        _processes = _processes_mod
        with connect() as _c:
            _processes.ensure(_c)
            # 上次服务端遗留的 running 行必须在启动时收尾，否则永远卡在 running
            _processes.recover_interrupted(_c)
        if os.environ.get("PROCESS_SUPERVISOR", "1") == "1" and not _under_test:
            _processes.start_supervisor(float(os.environ.get("PROCESS_TICK", "15")))
    except Exception as _exc:                    # noqa: BLE001
        print(f"[processes] supervisor not started: {_exc}")
    try:
        with connect() as _c:
            ui_store.ensure(_c)
    except Exception as _exc:                    # noqa: BLE001
        print(f"[ui] chat tables not ready: {_exc}")
    if os.environ.get("WECOM_APPROVAL_POLLER", "0") == "1" and not _under_test:
        try:
            from .wecom import approval as wecom_approval
            wecom_approval.start(float(os.environ.get("WECOM_APPROVAL_TICK", "60")))
        except Exception as exc:                 # noqa: BLE001
            print(f"[wecom] approval poller not started: {exc}")
    if os.environ.get("UI_CRON", "1") == "1" and not _under_test:
        try:
            ui_ops.start_cron()
        except Exception as exc:                 # noqa: BLE001
            print(f"[ui] cron not started: {exc}")
    if os.environ.get("OCTO_RUNNER", "0") == "1" and not _under_test:
        try:
            octo_runner.start()
        except Exception as exc:                 # noqa: BLE001
            print(f"[octo] runner not started: {exc}")
    yield
    try:
        from . import routines as _routines
        _routines.stop_scheduler()
    except Exception:
        pass
    if _processes is not None:
        _processes.stop_supervisor()             # 只停 tick 线程，绝不杀子进程
    if os.environ.get("WECOM_WATCHER", "0") == "1":
        wecom_watcher.stop()


app = FastAPI(title="Agent OS", version="5.4.2", lifespan=lifespan)

# the desktop shell (tauri://localhost) talks to 127.0.0.1:8077 with a Bearer token,
# so no credentials are involved and a permissive origin list is safe here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://tauri.localhost", "https://tauri.localhost", "tauri://localhost",
                   "http://127.0.0.1:8077", "http://localhost:8077"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(verbs.router)
app.include_router(v1_stream.router)
app.include_router(v1_bots.router)
app.include_router(v1_computer.router)
app.include_router(v1_workspace.router)
app.include_router(v1_search.router)
app.include_router(v1_misc.router)
app.include_router(v1_pairing.router)
app.include_router(v1_legacy_sso.router)
app.include_router(v1_local.router)
app.include_router(v1_bot_memory.router)
app.include_router(v1_routines.router)
app.include_router(v1_skills.router)
app.include_router(v1_processes.router)
app.include_router(v1_artifacts.router)
app.include_router(tasks.router)
app.include_router(decisions.router)
app.include_router(memory.router)
app.include_router(relay.router)
app.include_router(reports.router)
app.include_router(wecom_api.router)
app.include_router(sign_page.router)
app.include_router(octo_api.router)
app.include_router(ui_api.router, dependencies=[Depends(ui_access.request_access)])
app.include_router(ui_admin.router, dependencies=[Depends(ui_access.request_access)])
app.include_router(ui_ops.router, dependencies=[Depends(ui_access.request_access)])
app.include_router(ui_extensions.router)
app.include_router(ui_governance.router, dependencies=[Depends(ui_access.request_access)])
app.include_router(ui_housekeeping.router, dependencies=[Depends(ui_access.request_access)])
app.include_router(mcp_bridge_api.router, dependencies=[Depends(ui_access.request_access)])
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
_UI_DIST = pathlib.Path(__file__).resolve().parent.parent / "web" / "dist"
if _UI_DIST.exists():
    app.mount("/app", StaticFiles(directory=str(_UI_DIST), html=True), name="ui")

# v1 桌面端界面（desktop/ 构建产物），浏览器可直接预览，也是 exe 的内嵌前端
_UI_V1_DIST = pathlib.Path(__file__).resolve().parent.parent / "ui_dist"
if _UI_V1_DIST.exists():
    app.mount("/ui", StaticFiles(directory=str(_UI_V1_DIST), html=True), name="ui-v1")


@app.websocket("/ws/metacorp")
async def websocket_metacorp(websocket: WebSocket):
    await websocket.accept()
    bus.set_loop(asyncio.get_running_loop())
    q = asyncio.Queue()
    bus.register_queue(q)

    async def sender():
        while True:
            msg = await q.get()
            await websocket.send_text(msg)

    async def receiver():
        while True:
            raw = await websocket.receive_text()
            try:
                import json, time
                data = json.loads(raw)
                if data.get("action") == "ping":
                    await q.put(json.dumps({"type": "PONG", "time": time.time()}))
            except Exception:
                pass

    send_task = asyncio.create_task(sender())
    recv_task = asyncio.create_task(receiver())
    try:
        done, pending = await asyncio.wait([send_task, recv_task], return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        bus.unregister_queue(q)


@app.get("/api/telemetry/recent")
def get_recent_telemetry():
    return {"ok": True, "events": bus.recent_events(50), "connections": bus.count_connections()}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "oaagent-m0"}


@app.get("/")
def dashboard():
    """根路径进入新版工作台（未构建时回落到旧工作台）。"""
    from fastapi.responses import RedirectResponse

    if _UI_V1_DIST.exists():
        return RedirectResponse(url="/ui/", status_code=302)
    return FileResponse(str(STATIC / "dashboard.html"), media_type="text/html")


@app.get("/legacy")
def legacy_dashboard():
    """旧版董事长/调度工作台（登录后才用，不在登录页暴露入口）。"""
    return FileResponse(str(STATIC / "dashboard.html"), media_type="text/html")


@app.get("/review")
def review_page() -> FileResponse:
    """Single-task acceptance page (kept for deep links)."""
    return FileResponse(str(STATIC / "review.html"), media_type="text/html")
