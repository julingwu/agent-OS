"""Blueprint & Experience Pack Exporter/Importer for MetaCorp Exchange.

Packs mature business blueprints (WBS templates, experiences, acceptance criteria,
and evaluation baselines) into shareable `.metacorp.json` blueprint artifacts.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import time
from typing import Any

from ..acceptance import l0_rules
from ..audit import new_id, write_audit


def _calc_checksum(data: dict[str, Any]) -> str:
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def export_department_pack(conn: sqlite3.Connection, pack_id: str, dest_dir: str | pathlib.Path | None = None) -> dict[str, Any]:
    """Export a business pack as a standardized `.metacorp.json` blueprint."""
    # 1. Fetch pack definition
    pack_row = conn.execute("SELECT * FROM packs WHERE id=?", (pack_id,)).fetchone()
    if pack_row is None:
        # Fallback to predefined packs if not in DB table
        builtins = {
            "content-v1": {"name": "内容营销包", "template": "content", "budget": 25000, "desc": "文案、文章、投放素材"},
            "code-v1": {"name": "代码交付包", "template": "code", "budget": 30000, "desc": "脚本、工具、自动化交付"},
            "review-v1": {"name": "质检评审包", "template": "review", "budget": 45000, "desc": "质量与合规性严格审查"},
            "finance-v1": {"name": "财务审核包", "template": "finance", "budget": 30000, "desc": "费用核算、对账、报销复核"},
        }
        if pack_id in builtins:
            b = builtins[pack_id]
            pack = {"id": pack_id, "name": b["name"], "template_name": b["template"],
                    "default_budget": b["budget"], "description": b["desc"]}
        else:
            raise KeyError(f"pack not found: {pack_id}")
    else:
        pack = dict(pack_row)

    # 2. Gather associated flow templates
    flow_rows = conn.execute(
        "SELECT * FROM flow_templates WHERE id=? OR kind=? OR name LIKE ?",
        (pack_id, pack.get("template_name") or "", f"%{pack.get('name')}%")
    ).fetchall()
    flow_templates = [dict(r) for r in flow_rows]

    # 3. Gather experiences (org_experiences)
    # Search by tags or content mentioning the pack or template
    exp_rows = conn.execute(
        """SELECT id, kind, title, content, tags, weight, state
           FROM org_experiences
           WHERE state='approved' AND (tags LIKE ? OR title LIKE ? OR content LIKE ?)
           ORDER BY created_at DESC LIMIT 30""",
        (f"%{pack_id}%", f"%{pack.get('name')}%", f"%{pack.get('template_name')}%")
    ).fetchall()
    experiences = [dict(r) for r in exp_rows]
    if not experiences:
        # Export top approved general experiences if specific ones are empty
        experiences = [dict(r) for r in conn.execute(
            "SELECT id, kind, title, content, tags, weight, state FROM org_experiences WHERE state='approved' LIMIT 10"
        ).fetchall()]

    # 4. Gather eval sets
    eval_rows = conn.execute(
        "SELECT id, name, run_id, task_id, pack_id FROM eval_sets ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    eval_sets = [dict(r) for r in eval_rows]

    # 5. Package L0 rules
    l0_config = {
        "keyword_groups": l0_rules.KEYWORD_GROUPS,
        "banned_phrases": l0_rules.BANNED_PHRASES[:12],
    }

    blueprint_payload = {
        "pack": pack,
        "flow_templates": flow_templates,
        "experiences": experiences,
        "eval_sets": eval_sets,
        "l0_rules": l0_config,
    }
    checksum = _calc_checksum(blueprint_payload)

    blueprint = {
        "$schema": "https://agent-os.metacorp/v1/blueprint.schema.json",
        "format": "metacorp.blueprint",
        "version": "5.0",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "publisher": "AgentOS-MetaCorp-Node",
        "blueprint_id": f"BP-{pack_id}-{int(time.time())}",
        "checksum": checksum,
        **blueprint_payload
    }

    if dest_dir:
        dest_p = pathlib.Path(dest_dir)
        dest_p.mkdir(parents=True, exist_ok=True)
        file_path = dest_p / f"{pack_id}.metacorp.json"
        file_path.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding="utf-8")
        blueprint["_file_path"] = str(file_path)

    return blueprint


def import_department_pack(conn: sqlite3.Connection, data_or_str: dict[str, Any] | str) -> dict[str, Any]:
    """Import a `.metacorp.json` blueprint into the local MetaCorp registry."""
    if isinstance(data_or_str, str):
        blueprint = json.loads(data_or_str)
    else:
        blueprint = data_or_str

    if blueprint.get("format") != "metacorp.blueprint":
        raise ValueError("Invalid format: expected metacorp.blueprint")

    pack = blueprint.get("pack") or {}
    pack_id = pack.get("id")
    if not pack_id:
        raise ValueError("Missing pack.id in blueprint")

    # 1. Upsert pack
    conn.execute(
        """INSERT OR REPLACE INTO packs (id, name, template, default_budget, description)
           VALUES (?, ?, ?, ?, ?)""",
        (pack_id, pack.get("name", pack_id), pack.get("template_name", "general"),
         pack.get("default_budget", 30000), pack.get("description", "Imported from MetaCorp Blueprint"))
    )

    # 2. Upsert flow templates
    for ft in blueprint.get("flow_templates", []):
        if ft.get("id"):
            conn.execute(
                """INSERT OR REPLACE INTO flow_templates (id, name, kind, description, nodes, enabled)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ft["id"], ft.get("name", ft["id"]), ft.get("kind", "custom"),
                 ft.get("description", ""), json.dumps(ft.get("nodes", []), ensure_ascii=False) if isinstance(ft.get("nodes"), (list, dict)) else str(ft.get("nodes", "[]")), 1)
            )

    # 3. Import experiences (with deduplication)
    imported_exps = 0
    for exp in blueprint.get("experiences", []):
        exists = conn.execute("SELECT 1 FROM org_experiences WHERE title=?", (exp.get("title", ""),)).fetchone()
        if not exists and exp.get("title") and exp.get("content"):
            eid = exp.get("id") or new_id("EXP")
            tags = exp.get("tags")
            tags_json = json.dumps(tags, ensure_ascii=False) if isinstance(tags, list) else str(tags or "[]")
            conn.execute(
                """INSERT OR IGNORE INTO org_experiences
                   (id, tenant_id, legal_entity_id, kind, title, content, tags, weight, state)
                   VALUES (?, 'TENANT-HQ', 'LE-GROUP-HQ', ?, ?, ?, ?, ?, 'approved')""",
                (eid, exp.get("kind", "consensus"), exp.get("title"), exp.get("content"),
                 tags_json, float(exp.get("weight") or 1.0))
            )
            imported_exps += 1

    conn.commit()

    try:
        from .. import telemetry
        telemetry.emit("MCP_EXCHANGE_EVENT",
                       action="import_pack",
                       pack_id=pack_id,
                       pack_name=pack.get("name"),
                       experiences_added=imported_exps)
    except Exception:
        pass

    return {
        "ok": True,
        "pack_id": pack_id,
        "pack_name": pack.get("name"),
        "experiences_imported": imported_exps,
        "flow_templates_imported": len(blueprint.get("flow_templates", [])),
    }
