"""Artifact 读取 API（设备令牌鉴权）。

图片在聊天里显示、在原窗口里放大，都走这里的 ``GET /v1/artifacts/{id}``：
``artifact.attached`` 事件里只放 url（payload 上限 8KB，见 app/events.py）。

为什么不用「上传」接口：
    exe 已经有到服务端的出站 WS（/v1/device），截图直接从那条连接推
    ``{"t":"artifact",...}`` 更省事，也不用再把设备令牌暴露成可上传的公网入口。
    落盘在 app/artifacts.py，本文件只负责读。
"""

from __future__ import annotations

import pathlib
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import artifacts
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["artifacts"])

# 一次最多吐给浏览器的字节数（截图 2MB 就够看了；再大前端也会缩略）
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024


@router.get("/artifacts")
def list_artifacts(
    limit: int = Query(default=50, ge=1, le=200),
    device: dict = Depends(require_device),
) -> dict:
    """最近的 artifact 列表（最新在前）。资料库/排查用。"""
    items = artifacts.list_artifacts(limit=limit)
    return {"items": items, "count": len(items)}


@router.get("/artifacts/{artifact_id}")
def download_artifact(
    artifact_id: str,
    raw: str | None = Query(default=None),
    download: str | None = Query(default=None),
    device: dict = Depends(require_device),
) -> Response:
    try:
        path, kind = artifacts.resolve(artifact_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="artifact not found") from None
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid artifact id") from None

    is_raw = str(raw or "").lower() in ("1", "true", "yes") or str(download or "").lower() in ("1", "true", "yes")

    # 如果请求显式带了 raw=1/download=1，或者产物本身就是图片，返回原始二进制
    if is_raw or (kind and kind.startswith("image/")):
        size = path.stat().st_size
        if size > MAX_DOWNLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"artifact too large: {size} bytes")
        data = path.read_bytes()
        return Response(
            content=data,
            media_type=kind or "application/octet-stream",
            headers={
                "Cache-Control": "private, max-age=86400",
                "Content-Disposition": f'inline; filename="{pathlib.Path(path.name).name}"',
            },
        )

    # 非图片产物在桌面端直接放入 <img src={url} /> 会触发破图。未指定 raw=1 时返回精美伴生预览图
    preview_bytes = artifacts.ensure_doc_preview(artifact_id)
    meta = artifacts.describe(artifact_id)
    orig_name = meta.get("name") or path.name
    safe_name = urllib.parse.quote(orig_name)
    return Response(
        content=preview_bytes,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=86400",
            "Content-Disposition": f'inline; filename="{pathlib.Path(path.name).stem}.preview.png"',
            "X-Artifact-Original-Kind": kind or "application/octet-stream",
            "X-Artifact-Original-Name": safe_name,
        },
    )


@router.get("/artifacts/{artifact_id}/raw")
def download_artifact_raw(
    artifact_id: str,
    device: dict = Depends(require_device),
) -> Response:
    """直接下载/查看原始产物二进制文件。"""
    try:
        path, kind = artifacts.resolve(artifact_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="artifact not found") from None
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid artifact id") from None

    size = path.stat().st_size
    if size > MAX_DOWNLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"artifact too large: {size} bytes")
    data = path.read_bytes()
    return Response(
        content=data,
        media_type=kind or "application/octet-stream",
        headers={
            "Cache-Control": "private, max-age=86400",
            "Content-Disposition": f'inline; filename="{pathlib.Path(path.name).name}"',
        },
    )
