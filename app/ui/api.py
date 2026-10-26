"""BFF for the self-hosted console.

The browser never holds an HMAC secret: it authenticates once (identity id +
secret), gets an HttpOnly session cookie, and every later call is signed
server-side by this process.
"""
from __future__ import annotations

from fastapi import APIRouter

from .common import (
    COOKIE, SESSION_TTL, _secret, _sign, _make_cookie, _read_cookie,
    _token, _require, _tenant_of, _task_actor, _task_write_entities,
    _task_allowed_actions, _task_execution_summary, _budget_for,
)
from .routers import orgs, chats, tasks_runs, projects, evals

router = APIRouter(prefix="/ui", tags=["ui"])

# Mount domain sub-routers
router.include_router(orgs.router)
router.include_router(chats.router)
router.include_router(tasks_runs.router)
router.include_router(projects.router)
router.include_router(evals.router)

# Re-export models, routes, and internal handlers from sub-routers for compatibility
# Explicitly avoid re-exporting sub-router `router` attribute to preserve main router.
for _mod in (orgs, chats, tasks_runs, projects, evals):
    for _k, _v in _mod.__dict__.items():
        if not _k.startswith("__") and _k != "router":
            globals()[_k] = _v
