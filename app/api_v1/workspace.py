"""资料库 API：Bot 工作区的文件浏览（只读，锁在 M0_WORKSPACE 内）。

"资料库"页第一版就是文件树 + 文本预览；记忆/技能聚合等后续加。
"""

from __future__ import annotations

import pathlib

from fastapi import APIRouter, Depends, HTTPException, Query, Response

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
