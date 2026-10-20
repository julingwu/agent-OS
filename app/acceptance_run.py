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


def validate_mandatory(conn, task_id: str, *, entity: str | None = None,
                       tenant_id: str | None = None, commit: bool = True) -> dict:
    """Re-evaluate current delivery and retain evidence before any human override.

    A clean retry overwrites the current criterion, never historical scan records.
    Only artifacts attached to the latest run belong to this submission.
    """
    from . import redline, tenancy

    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if task is None:
        raise KeyError(task_id)
    actual_entity = task["legal_entity_id"]
    actual_tenant = dict(task).get("tenant_id") or tenancy.of_entity(conn, actual_entity)
    if (entity is not None and entity != actual_entity
            or tenant_id is not None and tenant_id != actual_tenant):
        raise PermissionError("MandatoryScopeMismatch")
    entity, tenant_id = actual_entity, actual_tenant
    run_row = conn.execute("SELECT id FROM task_runs WHERE task_id=?"
                           " ORDER BY run_seq DESC,rowid DESC LIMIT 1", (task_id,)).fetchone()
    run_id = run_row["id"] if run_row else None
    criterion = conn.execute("SELECT id FROM acceptance_criteria WHERE task_id=?"
                             " AND check_ref='redline'", (task_id,)).fetchone()
    hits, artifacts, rules = [], [], []
    error = None
    try:
        rules = redline.rules(conn, tenant_id=tenant_id, entity=entity)
        if rules:
            rows = (conn.execute("""SELECT * FROM artifacts WHERE run_id=?
                AND COALESCE(state,'active')='active' ORDER BY art_seq,rowid""",
                (run_id,)).fetchall() if run_id else [])
            if not rows:
                raise ValueError("当前执行没有可验收的交付物")
            for row in rows:
                path = pathlib.Path(row["file_path"])
                # Known textual deliverables are checked; HTML uses our existing
                # visible-copy extractor instead of CSS and JavaScript source.
                if path.suffix.lower() not in {
                    ".txt", ".md", ".markdown", ".html", ".htm", ".csv", ".json",
                    ".xml", ".yaml", ".yml", ".rst", ".log",
                    ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".sql", ".sh", ".ps1",
                }:
                    continue
                if path.is_file():
                    if path.stat().st_size > 4 * 1024 * 1024:
                        raise ValueError("交付物超过红线扫描大小限制")
                    raw = path.read_bytes()
                    text = raw.decode("utf-8", errors="strict")
                elif row["content"] is not None:
                    text = row["content"]
                    raw = text.encode("utf-8")
                else:
                    raise OSError("当前交付物不可读取")
                copy = l0_rules.text_for_rule("banned", text, str(path))
                artifact_hits = []
                for rule in rules:
                    found = [term for term in rule["terms"] if term and term in copy]
                    if found:
                        first = copy.index(found[0])
                        hit = {"experience_id": rule["id"], "version": rule["version"],
                               "content_hash": rule["content_hash"], "terms": sorted(set(found)),
                               "artifact_id": row["id"],
                               "snippet": copy[max(0, first - 80):first + 200]}
                        hits.append(hit)
                        artifact_hits.append(hit)
                artifacts.append({"id": row["id"], "name": path.name,
                                  "checksum": hashlib.sha256(raw).hexdigest(),
                                  "registered_checksum": row["checksum"],
                                  "bytes": len(raw), "hits": artifact_hits})
            if not artifacts:
                raise ValueError("当前交付物没有可扫描的文本，需补充可验收文本")
    except (OSError, ValueError, sqlite3.Error, PermissionError) as exc:
        # Persist a blocking failure instead of silently allowing delivery.
        error = "红线校验未完成：" + (str(exc) if type(exc) is ValueError else type(exc).__name__)
    if not rules and not criterion and error is None:
        return {"ok": True, "state": "passed", "criterion_id": None,
                "detail": "无适用企业红线", "hits": []}
    ok = not hits and error is None
    terms = sorted({term for hit in hits for term in hit["terms"]})
    detail = error or ("命中企业红线：" + "、".join(terms) if hits else "当前交付物未命中企业红线")
    state = "passed" if ok else "failed"
    if criterion:
        criterion_id = criterion["id"]
        conn.execute("UPDATE acceptance_criteria SET state=?,note=? WHERE id=?",
                     (state, detail, criterion_id))
    else:
        criterion_id = new_id("C")
        seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM acceptance_criteria"
                           " WHERE task_id=?", (task_id,)).fetchone()[0]
        conn.execute("""INSERT INTO acceptance_criteria
            (id,legal_entity_id,task_id,criterion,check_type,check_ref,is_blocking,
             evidence_required,is_compliance,acceptance_layer,state,note,seq)
            VALUES (?,?,?,'企业红线检查','rule_auto','redline',1,0,1,'L0',?,?,?)""",
            (criterion_id, entity, task_id, state, detail, seq))
    conn.execute("""CREATE TABLE IF NOT EXISTS redline_scans (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, run_id TEXT,
        tenant_id TEXT NOT NULL, legal_entity_id TEXT NOT NULL,
        state TEXT NOT NULL, snapshot TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    snapshot = {"rules": rules, "artifacts": artifacts, "hits": hits, "error": error}
    scan_id = new_id("RLS")
    conn.execute("INSERT INTO redline_scans"
                 "(id,task_id,run_id,tenant_id,legal_entity_id,state,snapshot)"
                 " VALUES (?,?,?,?,?,?,?)",
                 (scan_id, task_id, run_id, tenant_id, entity, state,
                  json.dumps(snapshot, ensure_ascii=False)))
    write_audit(conn, legal_entity_id=entity, verb="acceptance_run", task_id=task_id,
                run_id=run_id, layer="system", action="redline_checked",
                target=criterion_id, payload={"scan_id": scan_id, **snapshot},
                response={"ok": ok, "state": state})
    if commit:
        conn.commit()
    return {"ok": ok, "state": state, "criterion_id": criterion_id,
            "detail": detail, "hits": hits, "scan_id": scan_id}


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

    mandatory = validate_mandatory(conn, task_id, entity=entity, commit=False)
    results = ([{"criterion_id": mandatory["criterion_id"], "state": mandatory["state"],
                 "detail": mandatory["detail"], "layer": "L0"}]
               if mandatory["criterion_id"] else [])
    for c in criteria:
        if c["check_ref"] == "redline":
            continue
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
    # v4.0：红线 L0 拦截实时推企微群。红线没有人工审批入口（只能改稿重试），
    # 所以「谁被拦、命中了哪个词」必须第一时间到群里；推送失败不影响已提交的驳回。
    if mandatory.get("criterion_id") and mandatory.get("state") == "failed":
        try:
            from . import notify
            last_run = conn.execute(
                "SELECT id FROM task_runs WHERE task_id=? ORDER BY run_seq DESC LIMIT 1",
                (task_id,)).fetchone()
            notify.redline_blocked(
                conn, task_id=task_id, run_id=last_run["id"] if last_run else None,
                detail=mandatory.get("detail") or "",
                hits=mandatory.get("hits") or [])
        except Exception:                    # noqa: BLE001 - 通知不是闸门
            pass
    return {"task_id": task_id, "task_state": new_state, "verdict": verdict,
            "results": results}


def judge(conn: sqlite3.Connection, criterion_id: str, *, passed: bool, checked_by: str,
          note: str | None = None, evidence_snippet: str | None = None,
          file_path: str | None = None) -> dict:
    from .acceptance import l1_manual

    return l1_manual.apply(conn, criterion_id, passed=passed, checked_by=checked_by,
                           note=note, evidence_snippet=evidence_snippet,
                           file_path=file_path)
