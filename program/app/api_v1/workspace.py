"""资料库 API：Bot 工作区的文件浏览（只读，锁在 M0_WORKSPACE 内）。

"资料库"页第一版就是文件树 + 文本预览；记忆/技能聚合等后续加。
"""

from __future__ import annotations

import pathlib

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request

from ..agent import tools
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["workspace"])

PREVIEW_LIMIT = 200_000


def _jail(path: str | None) -> pathlib.Path:
    root = tools.WORKSPACE.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / (path or ".")).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="path escapes workspace")
    return target


@router.get("/workspace")
def list_workspace(
    path: str = Query(default=""),
    device: dict = Depends(require_device),
) -> dict:
    target = _jail(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    if target.is_file():
        return {"path": str(target.relative_to(tools.WORKSPACE.resolve())),
                "type": "file", "bytes": target.stat().st_size}
    items = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))[:300]:
        item = {"name": child.name, "type": "dir" if child.is_dir() else "file"}
        if child.is_file():
            item["bytes"] = child.stat().st_size
            item["suffix"] = child.suffix.lower()
        items.append(item)
    return {
        "path": str(target.relative_to(tools.WORKSPACE.resolve()) or "."),
        "type": "dir",
        "items": items,
    }


@router.get("/workspace/raw")
def workspace_raw(
    path: str = Query(min_length=1),
    device: dict = Depends(require_device),
) -> Response:
    target = _jail(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not a file")
    data = target.read_bytes()[:PREVIEW_LIMIT]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="binary file; use download") from None
    return Response(content=text, media_type="text/plain; charset=utf-8")

import base64
import mimetypes
import os
import re
import time
from typing import Optional
from fastapi import File, Form, UploadFile
from pydantic import BaseModel
from .. import artifacts, db, events as ev


class WorkspaceUploadBody(BaseModel):
    filename: str
    data_b64: str
    session_id: Optional[str] = None
    bot_id: Optional[str] = None


def _clean_filename(name: str) -> str:
    raw = os.path.basename((name or "").replace("\\", "/")).strip()
    raw = re.sub(r'[<>:"|?*]', "", raw).strip("./ ")
    if not raw:
        raw = f"upload_{int(time.time())}.bin"
    return raw


@router.post("/workspace/upload")
async def workspace_upload(
    request: Request,
    device: dict = Depends(require_device),
) -> dict:
    """上传文件到 Bot 工作区 (uploads/ 目录) 并同时发布为 Artifact。支持 JSON Base64 与 Multipart。"""
    data = b""
    filename = ""
    target_session = None
    target_bot = None

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        file_obj = form.get("file")
        if file_obj is not None:
            filename = getattr(file_obj, "filename", "upload.bin")
            data = await file_obj.read()
        target_session = form.get("session_id")
        target_bot = form.get("bot_id")
    else:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid json body: {exc}")
        filename = body.get("filename", "")
        target_session = body.get("session_id")
        target_bot = body.get("bot_id")
        raw_b64 = str(body.get("data_b64") or "").strip()
        if raw_b64.startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[-1]
        try:
            data = base64.b64decode(raw_b64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid base64: {exc}")

    if not data or not filename:
        raise HTTPException(status_code=400, detail="missing filename or empty data")

    clean_name = _clean_filename(filename)
    root = tools.WORKSPACE.resolve()
    uploads_dir = root / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    dest = uploads_dir / clean_name
    if dest.exists():
        stem, ext = os.path.splitext(clean_name)
        clean_name = f"{stem}_{int(time.time())}{ext}"
        dest = uploads_dir / clean_name

    dest.write_bytes(data)

    mime, _ = mimetypes.guess_type(clean_name)
    mime = mime or "application/octet-stream"

    art = artifacts.save_bytes(clean_name, data, kind=mime)

    if target_session:
        try:
            with db.get_conn() as conn:
                event = ev.emit(
                    conn,
                    bot_id=target_bot or "bot_chief",
                    session_id=str(target_session),
                    kind="artifact.attached",
                    origin="user",
                    payload={
                        "artifact_id": art["id"],
                        "name": art["name"],
                        "kind": art["kind"],
                        "bytes": art["bytes"],
                        "url": art["url"],
                        "workspace_path": f"uploads/{clean_name}",
                    },
                )
                conn.commit()
                ev.broadcaster.publish(event)
        except Exception:
            pass

    return {
        "ok": True,
        "filename": clean_name,
        "path": f"uploads/{clean_name}",
        "bytes": len(data),
        "artifact_id": art["id"],
        "url": art["url"],
    }
