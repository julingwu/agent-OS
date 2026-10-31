"""FastAPI router for MetaCorp MCP Multi-World Exchange."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..db import connect
from .mcp_client import hire_remote_mcp
from .mcp_server import handle_jsonrpc, list_tools
from .pack_exporter import export_department_pack, import_department_pack

router = APIRouter(prefix="/mcp-exchange", tags=["mcp-exchange"])


class RemoteHireIn(BaseModel):
    endpoint_url: str = "local_mock"
    tool_name: str = "metacorp.run_l2_audit"
    arguments: dict[str, Any] = {}
    project_id: str
    stage_key: str = "wbs"


@router.get("/tools")
def get_mcp_tools():
    """List exposed MCP tools."""
    return {"ok": True, "tools": list_tools()}


@router.post("/rpc")
async def mcp_jsonrpc_endpoint(request: Request):
    """Standard JSON-RPC 2.0 MCP server endpoint."""
    try:
        body = await request.json()
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    return handle_jsonrpc(body)


@router.get("/packs")
def list_exchange_packs():
    """List packs available for blueprint packaging."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM packs ORDER BY id").fetchall()
        packs = [dict(r) for r in rows]
        if not packs:
            packs = [
                {"id": "content-v1", "name": "内容营销包", "template_name": "content", "default_budget": 25000, "description": "文案、文章、投放素材"},
                {"id": "code-v1", "name": "代码交付包", "template_name": "code", "default_budget": 30000, "description": "脚本、工具、自动化交付"},
                {"id": "review-v1", "name": "质检评审包", "template_name": "review", "default_budget": 45000, "description": "质量与合规性严格审查"},
                {"id": "finance-v1", "name": "财务审核包", "template_name": "finance", "default_budget": 30000, "description": "费用核算、对账、报销复核"},
            ]
        # Attach experiences count
        for p in packs:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM org_experiences WHERE state='approved' AND (tags LIKE ? OR title LIKE ?)",
                (f"%{p['id']}%", f"%{p['name']}%")
            ).fetchone()[0]
            p["experiences_count"] = cnt
    return {"ok": True, "packs": packs}


@router.get("/export/{pack_id}")
def export_blueprint(pack_id: str):
    """Export a pack blueprint as a `.metacorp.json` download."""
    with connect() as conn:
        try:
            blueprint = export_department_pack(conn, pack_id)
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc))
    content = json.dumps(blueprint, ensure_ascii=False, indent=2)
    filename = f"{pack_id}.metacorp.json"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/import")
async def import_blueprint(request: Request):
    """Import a `.metacorp.json` blueprint."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(400, detail="Invalid JSON payload")
    with connect() as conn:
        try:
            res = import_department_pack(conn, body)
        except Exception as exc:
            raise HTTPException(400, detail=f"Import failed: {exc}")
    return res


@router.post("/hire-remote")
def hire_external_player(payload: RemoteHireIn):
    """Hire an external MCP player to execute a subtask and merge deliverable."""
    try:
        res = hire_remote_mcp(
            endpoint_url=payload.endpoint_url,
            tool_name=payload.tool_name,
            arguments=payload.arguments,
            project_id=payload.project_id,
            stage_key=payload.stage_key
        )
        return res
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))
