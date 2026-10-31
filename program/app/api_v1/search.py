"""搜索（Cmd+K）：Bot / 会话 / 消息 / 工作区文件 一把搜。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Query

from .. import db
from .. import events as ev
from ..agent import tools
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["search"])


def _like(q: str) -> str:
    return f"%{q}%"


@router.get("/search")
def search(
    q: str = Query(min_length=1, max_length=80),
    limit: int = Query(default=6, ge=1, le=20),
    device: dict = Depends(require_device),
) -> dict:
    needle = _like(q)
    out: dict = {"query": q, "bots": [], "sessions": [], "messages": [], "files": []}

    with db.get_conn() as conn:
        out["bots"] = [
            {"id": r["id"], "name": r["name"], "role": r["role"]}
            for r in conn.execute(
                "SELECT id, name, role FROM bots WHERE deleted_at IS NULL"
                " AND (name LIKE ? OR role LIKE ? OR IFNULL(persona,'') LIKE ?) LIMIT ?",
                (needle, needle, needle, limit),
            ).fetchall()
        ]
        out["sessions"] = [
            {"id": r["id"], "title": r["title"], "kind": r["kind"]}
            for r in conn.execute(
                "SELECT id, title, kind FROM sessions WHERE deleted_at IS NULL"
                " AND IFNULL(title,'') LIKE ? LIMIT ?",
                (needle, limit),
            ).fetchall()
        ]
        rows = conn.execute(
            "SELECT * FROM events WHERE kind IN ('message.user','message.bot')"
            " AND payload_json LIKE ? ORDER BY id DESC LIMIT ?",
            (needle, limit),
        ).fetchall()
        out["messages"] = [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "kind": r["kind"],
                "text": ev._row_to_event(r).payload.get("text", "")[:200],
                "ts": r["ts"],
            }
            for r in rows
        ]

    root = tools.WORKSPACE
    if root.exists():
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(os.path.relpath(dirpath, root).split(os.sep))
            if depth >= 3:
                dirnames[:] = []
            for name in filenames:
                if q.lower() in name.lower():
                    path = os.path.join(dirpath, name)
                    found.append({
                        "path": os.path.relpath(path, root).replace(os.sep, "/"),
                        "bytes": os.path.getsize(path),
                    })
                    if len(found) >= limit:
                        break
            if len(found) >= limit:
                break
        out["files"] = found

    return out
