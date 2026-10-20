"""FastAPI entry point for the M0 shell."""
from __future__ import annotations

import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import decisions, memory, relay, reports, tasks, verbs
from .octo import api as octo_api
from .ui import api as ui_api
from .ui import admin as ui_admin
from .ui import ops as ui_ops
from .ui import store as ui_store
from .octo import runner as octo_runner
from .wecom import api as wecom_api
from .wecom import sign_page
from .wecom import watcher as wecom_watcher
from .config import load_env
from .db import connect, init_db

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = pathlib.Path(__file__).resolve().parent / "static"

load_env()          # .env must be in os.environ before any dsh child is spawned


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if os.environ.get("WECOM_WATCHER", "0") == "1":
        wecom_watcher.start(interval=float(os.environ.get("WECOM_TICK", "3")))
    # Background threads must never start under pytest: they would leak across
    # tests and poll the real corp with the developer's credentials.
    _under_test = bool(os.environ.get("PYTEST_CURRENT_TEST"))
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
    if os.environ.get("WECOM_WATCHER", "0") == "1":
        wecom_watcher.stop()


app = FastAPI(title="OA Agent M0", version="3.2.0", lifespan=lifespan)

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
app.include_router(tasks.router)
app.include_router(decisions.router)
app.include_router(memory.router)
app.include_router(relay.router)
app.include_router(reports.router)
app.include_router(wecom_api.router)
app.include_router(sign_page.router)
app.include_router(octo_api.router)
app.include_router(ui_api.router)
app.include_router(ui_admin.router)
app.include_router(ui_ops.router)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
_UI_DIST = pathlib.Path(__file__).resolve().parent.parent / "web" / "dist"
if _UI_DIST.exists():
    app.mount("/app", StaticFiles(directory=str(_UI_DIST), html=True), name="ui")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "oaagent-m0"}


@app.get("/")
def dashboard() -> FileResponse:
    """Chairman/scheduling workbench (SPA)."""
    return FileResponse(str(STATIC / "dashboard.html"), media_type="text/html")


@app.get("/review")
def review_page() -> FileResponse:
    """Single-task acceptance page (kept for deep links)."""
    return FileResponse(str(STATIC / "review.html"), media_type="text/html")
