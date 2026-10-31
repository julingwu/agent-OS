"""MCP Client for cross-company hiring and external MetaCorp integration.

Routes subtasks to external MCP endpoints and automatically merges verified
deliverables into the project's release folder for final `Bundle.zip` export.
"""
from __future__ import annotations

import json
import logging
import pathlib
import time
from typing import Any

import httpx

from ..db import connect
from ..engine import dsh_adapter

log = logging.getLogger("metacorp.mcp_client")


def hire_remote_mcp(
    endpoint_url: str,
    tool_name: str,
    arguments: dict[str, Any],
    project_id: str,
    stage_key: str = "wbs"
) -> dict[str, Any]:
    """Call an external MCP server to execute a subtask and merge output into project bundle."""
    req_body = {
        "jsonrpc": "2.0",
        "id": f"hire-{int(time.time())}",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }

    log.info("Hiring external MCP: url=%s, tool=%s, project=%s", endpoint_url, tool_name, project_id)

    # In mock or offline tests, or when testing locally:
    if "mock" in endpoint_url or endpoint_url == "local_mock":
        from .mcp_server import call_tool
        result = call_tool(tool_name, arguments)
    else:
        try:
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                resp = client.post(endpoint_url, json=req_body)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    raise RuntimeError(f"Remote MCP error: {data['error']}")
                content = data.get("result", {}).get("content", [])
                text = content[0].get("text", "{}") if content else "{}"
                try:
                    result = json.loads(text)
                except Exception:
                    result = {"raw_output": text}
        except Exception as exc:
            log.warning("Remote MCP request failed (%s): %s", endpoint_url, exc)
            raise RuntimeError(f"Failed to communicate with remote MCP: {exc}") from exc

    # Merge into release folder for Bundle.zip
    ws = dsh_adapter.workspace()
    release_dir = ws / "projects" / project_id / "release"
    release_dir.mkdir(parents=True, exist_ok=True)

    filename = result.get("filename") or f"remote_mcp_{stage_key}_{int(time.time())}.md"
    file_path = release_dir / filename

    content_str = result.get("deliverable") or json.dumps(result, ensure_ascii=False, indent=2)
    file_path.write_text(content_str, encoding="utf-8")

    # Save to stage_deliverables in DB
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO stage_deliverables
               (id, project_id, stage_key, kind, file_path, bytes, created_at)
               VALUES (?, ?, ?, 'mcp_remote', ?, ?, CURRENT_TIMESTAMP)""",
            (f"DELIV-MCP-{int(time.time())}", project_id, stage_key, str(file_path), len(content_str.encode("utf-8")))
        )
        conn.commit()

    try:
        from .. import telemetry
        telemetry.emit(
            "MCP_EXCHANGE_EVENT",
            action="remote_hire_completed",
            project_id=project_id,
            stage_key=stage_key,
            tool=tool_name,
            endpoint=endpoint_url,
            filename=filename,
            bytes=len(content_str.encode("utf-8"))
        )
    except Exception:
        pass

    return {
        "ok": True,
        "project_id": project_id,
        "stage_key": stage_key,
        "tool_name": tool_name,
        "filename": filename,
        "file_path": str(file_path),
        "result": result,
    }
