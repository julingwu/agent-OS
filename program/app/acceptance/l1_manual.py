"""L1: a human decision with mandatory evidence."""
from __future__ import annotations

import hashlib
import pathlib
import sqlite3

from ..audit import new_id, write_audit


class EvidenceRequired(ValueError):
    pass


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: str) -> str | None:
    p = pathlib.Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def apply(
    conn: sqlite3.Connection,
    criterion_id: str,
    *,
    passed: bool,
    checked_by: str = "human",
    note: str | None = None,
    evidence_snippet: str | None = None,
    file_path: str | None = None,
    line_no: int | None = None,
) -> dict:
    c = conn.execute(
        "SELECT * FROM acceptance_criteria WHERE id = ?", (criterion_id,)
    ).fetchone()
    if c is None:
        raise KeyError(f"criterion not found: {criterion_id}")
    if c["check_ref"] == "redline":
        raise ValueError("企业红线必须重新执行自动校验，不能人工改判")
    if c["evidence_required"] and not (evidence_snippet or file_path):
        raise EvidenceRequired(f"判据 {criterion_id} 要求证据（content_snippet 或 file_path）")

    checksum = _sha256_file(file_path) if file_path else (
        _sha256_text(evidence_snippet) if evidence_snippet else None
    )
    state = "passed" if passed else "failed"
    conn.execute(
        "UPDATE acceptance_criteria SET state = ?, checked_by = ?, note = ? WHERE id = ?",
        (state, checked_by, note, criterion_id),
    )
    if checksum:
        conn.execute(
            """INSERT INTO evidence_refs
               (id, owner_type, owner_id, file_path, content_snippet, file_checksum, line_no)
               VALUES (?,?,?,?,?,?,?)""",
            (new_id("EV"), "criterion", criterion_id, file_path, evidence_snippet, checksum, line_no),
        )
    write_audit(
        conn,
        legal_entity_id=c["legal_entity_id"],
        verb="l1_manual",
        task_id=c["task_id"],
        layer="L1",
        actor=checked_by,
        action="manual_check",
        target=criterion_id,
        payload={"passed": passed, "note": note, "file_path": file_path},
        response={"state": state, "file_checksum": checksum},
    )
    return {"criterion_id": criterion_id, "state": state, "checked_by": checked_by,
            "file_checksum": checksum}
