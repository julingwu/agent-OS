"""进程 supervisor 的 REST 接口（W4 / 契约 02）。

路由只做三件事：鉴权（设备令牌）、参数校验、把异常翻译成 4xx。生命周期逻辑全部
在 ``app/processes.py``，这里不碰 Popen。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db, processes
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["processes"])


class ProcessIn(BaseModel):
    bot_id: str = Field(min_length=1, description="哪个 Bot 起的进程")
    cmd: str | list[str] = Field(description="字符串（按原生命令行规则切分）或 argv 列表")
    session_id: str | None = None
    name: str | None = Field(default=None, max_length=80)
    cwd: str | None = Field(default=None, description="默认 Bot 工作区；相对路径按工作区解析")
    env: dict[str, str] | None = None


class StopIn(BaseModel):
    timeout_s: float | None = Field(default=None, ge=0.1, le=60,
                                    description="优雅信号的等待秒数，超时后强杀")


@router.get("/processes")
def list_processes(
    bot_id: str | None = Query(default=None),
    state: str | None = Query(default=None, description="running|exited|failed|stopped|lost"),
    limit: int = Query(default=100, ge=1, le=500),
    device: dict = Depends(require_device),
) -> dict:
    with db.get_conn() as conn:
        items = processes.list_processes(conn, bot_id=bot_id, state=state, limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/processes")
def create_process(body: ProcessIn,
                   device: dict = Depends(require_device)) -> dict:
    try:
        with db.get_conn() as conn:
            item = processes.start(conn, bot_id=body.bot_id, cmd=body.cmd,
                                   session_id=body.session_id, name=body.name,
                                   cwd=body.cwd, env=body.env)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:                            # 可执行文件不存在等启动失败
        raise HTTPException(status_code=400, detail=f"spawn failed: {exc}") from exc
    return {"item": item}


@router.get("/processes/{process_id}")
def get_process(process_id: str, device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        item = processes.get(conn, process_id)
    if item is None:
        raise HTTPException(status_code=404, detail="process not found")
    return {"item": item}


@router.post("/processes/{process_id}/stop")
def stop_process(process_id: str, body: StopIn | None = None,
                 device: dict = Depends(require_device)) -> dict:
    timeout = processes.DEFAULT_STOP_TIMEOUT
    payload = body or StopIn()
    if payload.timeout_s:
        timeout = float(payload.timeout_s)
    with db.get_conn() as conn:
        item = processes.stop(conn, process_id, timeout=timeout, by="api")
    if item is None:
        raise HTTPException(status_code=404, detail="process not found")
    # stopped 的含义是"本次调用结束时它已不在 running"，所以重复调用同样返回 True
    # （幂等），但不会重复写 process.killed 事件。
    return {"stopped": item["state"] != "running", "item": item}


@router.get("/processes/{process_id}/log")
def process_log(process_id: str,
                tail: int = Query(default=200, ge=1, le=processes.MAX_LOG_TAIL_LINES),
                device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        out = processes.log_tail(conn, process_id, tail=tail)
    if out is None:
        raise HTTPException(status_code=404, detail="process not found")
    return out
