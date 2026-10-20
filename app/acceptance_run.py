"""Acceptance run as a callable, so the relay can drive it without HTTP."""
from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3

from . import tenant
from .acceptance import l0_rules, l2_scripts, task_text
from .audit import new_id, write_audit
from .api import verbs
from .state_machine import IllegalStateTransition, transition


def _evidence(conn, criterion_id, *, snippet, file_path, checksum):
    if not (snippet or file_path):
        return
    conn.execute(
        """INSERT INTO evidence_refs
           (id, owner_type, owner_id, file_path, content_snippet, file_checksum, line_no)
           VALUES (?,?,?,?,?,?,?)""",
        (new_id("EV"), "criterion", criterion_id, file_path, snippet, checksum, None))


def run(conn: sqlite3.Connection, task_id: str, ents: list[str] | str) -> dict:
    ents = [ents] if isinstance(ents, str) else ents
    t = tenant.scoped_task(conn, task_id, ents)
    entity = t["legal_entity_id"]
    criteria = conn.execute(
        "SELECT * FROM acceptance_criteria WHERE task_id = ? AND legal_entity_id = ?"
        " ORDER BY seq", (task_id, entity)).fetchall()
    expected = None
    for c in criteria:
        if c["check_type"] == "script_exec" and c["check_ref"]:
            try:
                expected = json.loads(c["check_ref"]).get("name")
            except (ValueError, AttributeError):
                expected = None
            if expected:
                break
    art = verbs.latest_artifact_path(conn, task_id, expected)
    text = None
    text_path = None
    if art:
        try:
            text = pathlib.Path(art).read_text(encoding="utf-8", errors="replace")
            text_path = art
        except OSError:
            text = None
    if text is None:
        text = task_text(conn, task_id)

    results = []
    for c in criteria:
        if c["check_type"] == "human_check":
            results.append({"criterion_id": c["id"], "state": c["state"],
                            "detail": "待人工", "layer": "L1"})
            continue
        if c["check_type"] == "rule_auto":
            rule_text = text or ""
            if text_path is None and c["check_ref"] == "banned":
                rule_text = task_text(conn, task_id, check_ref="banned") or ""
            v = l0_rules.evaluate(c["check_ref"], rule_text, file_path=text_path)
            state = "passed" if v["ok"] else "failed"
            conn.execute("UPDATE acceptance_criteria SET state = ?, note = ? WHERE id = ?",
                         (state, v.get("detail"), c["id"]))
            if c["evidence_required"]:
                snippet = (text or "")[:200]
                _evidence(conn, c["id"], snippet=snippet, file_path=None,
                          checksum=hashlib.sha256(snippet.encode("utf-8")).hexdigest())
            results.append({"criterion_id": c["id"], "state": state,
                            "detail": v.get("detail"), "layer": "L0"})
        else:  # script_exec
            try:
                v = l2_scripts.evaluate(c["check_ref"], art)
                state = "passed" if v.get("ok") else "failed"
                detail = v.get("detail")
            except l2_scripts.ScriptError as exc:
                state, detail = "failed", f"脚本执行失败: {exc}"
            conn.execute("UPDATE acceptance_criteria SET state = ?, note = ? WHERE id = ?",
                         (state, detail, c["id"]))
            if c["evidence_required"] and art:
                try:
                    snippet = pathlib.Path(art).read_text(encoding="utf-8",
                                                          errors="replace")[:200]
                except OSError:
                    snippet = None
                _evidence(conn, c["id"], snippet=snippet, file_path=str(art),
                          checksum=verbs.sha256_file(art))
            results.append({"criterion_id": c["id"], "state": state,
                            "detail": detail, "layer": "L2"})

    fresh = conn.execute(
        "SELECT * FROM acceptance_criteria WHERE task_id = ? AND legal_entity_id = ?"
        " ORDER BY seq", (task_id, entity)).fetchall()
    blocking = [c for c in fresh if c["is_blocking"] and c["state"] != "na"]
    verdict, event = "pending", None
    if any(c["state"] == "failed" for c in blocking):
        event, verdict = "review_failed", "rejected"
    elif blocking and all(c["state"] == "passed" for c in blocking):
        event, verdict = "review_passed", "completed"

    new_state = t["state"]
    if event:
        try:
            new_state = transition(conn, task_id, event, actor="acceptance")
        except IllegalStateTransition as exc:
            verdict = f"skipped: {exc}"

    write_audit(conn, legal_entity_id=entity, verb="acceptance_run", task_id=task_id,
                layer="system", action="acceptance_run", target=task_id,
                payload={"results": results},
                response={"verdict": verdict, "task_state": new_state})
    if event == "review_failed":
        # v2.4 分层记忆：驳回原因沉淀为组织经验，下个同类项目立项时自动注入
        try:
            from . import experience
            experience.capture_rejection(conn, task_id, verdict=verdict)
        except Exception:                    # noqa: BLE001 - 记忆失败不影响验收
            pass
    conn.commit()
    return {"task_id": task_id, "task_state": new_state, "verdict": verdict,
            "results": results}


def judge(conn: sqlite3.Connection, criterion_id: str, *, passed: bool, checked_by: str,
          note: str | None = None, evidence_snippet: str | None = None,
          file_path: str | None = None) -> dict:
    from .acceptance import l1_manual

    return l1_manual.apply(conn, criterion_id, passed=passed, checked_by=checked_by,
                           note=note, evidence_snippet=evidence_snippet,
                           file_path=file_path)
