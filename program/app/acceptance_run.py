"""Acceptance run as a callable, so the relay can drive it without HTTP."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys

from . import tenant
from .acceptance import l0_rules, l2_scripts, task_text
from .audit import new_id, write_audit
from .api import verbs
from .state_machine import IllegalStateTransition, transition

# 任务自己生成的「验收自检脚本」不是对外文案。它必然包含禁用词清单本身
# （例如 test_check.py 里 banned = ["100%", "绝对", ...]），把它当交付文案
# 送去扫红线必然 100% 命中 —— 这是自指陷阱，与模型能力无关。
# 实测：生产库 501 个 web-v1 任务里 431 次同一失败，归因 40/40 全部落在 test_check.py。
# 所以扫描时跳过工具脚本，并把跳过项写进快照，让审计看得见"跳过了什么"。
_TOOLING_FILE = re.compile(
    r"^(?:test_.*|.*_test|.*_check|check_.*|verify_.*|lint_.*|conftest)\.py$", re.I)


def failure_digest(conn: sqlite3.Connection, task_id: str) -> dict:
    """v5.2 TASK-2.2：把「失败断言 / 期望产出文件 / 错误输出摘要」结构化取出来。

    物理事实：失败信息本来就落在 acceptance_criteria.state='failed' 与 task_runs.error，
    这里只做只读聚合，不再复制一份状态，避免两处真相互相打架。下一轮 Prompt 用它
    做单点修正，而不是让模型把整篇稿子重写一遍。HTTP 重试与流水线内部重跑共用它。
    """
    failed = conn.execute(
        """SELECT seq, criterion, note, check_type, check_ref, acceptance_layer
           FROM acceptance_criteria
           WHERE task_id = ? AND state = 'failed' ORDER BY seq""",
        (task_id,)).fetchall()
    last_run = conn.execute(
        """SELECT error FROM task_runs WHERE task_id = ?
           ORDER BY run_seq DESC, rowid DESC LIMIT 1""",
        (task_id,)).fetchone()
    items: list[dict] = []
    expected: list[str] = []
    for r in failed:
        name = None
        if r["check_type"] == "script_exec" and r["check_ref"]:
            try:
                cfg = json.loads(r["check_ref"])
            except (ValueError, TypeError):
                cfg = None
            if isinstance(cfg, dict):
                name = cfg.get("name")
        if name and name not in expected:
            expected.append(name)
        detail = (r["note"] or "判定未通过").strip()
        stderr = ""
        # l2_scripts.ScriptError 把子进程 stderr 拼在 "exit=N: ..." 里，拆出来单独成行，
        # 模型才能一眼看到语法错在第几行，而不是从一段长中文里猜。
        if " exit=" in detail:
            detail, _, stderr = detail.partition(" exit=")
            stderr = ("exit=" + stderr).strip()
        items.append({
            "seq": int(r["seq"]),
            "criterion": r["criterion"],
            "layer": r["acceptance_layer"] or
                     ("L2" if r["check_type"] == "script_exec" else "L0"),
            "check_type": r["check_type"],
            "expected_name": name,
            "detail": detail.strip(),
            "stderr": stderr[:400],
        })
    actual: list[str] = []
    for name in expected:
        try:
            path = verbs.latest_artifact_path(conn, task_id, name)
        except sqlite3.Error:
            path = None
        if path and path not in actual:
            actual.append(path)
    return {
        "items": items,
        "expected_names": expected,
        "actual_paths": actual,
        "run_error": (last_run["error"] if last_run and last_run["error"] else None),
    }


def failure_prompt(conn: sqlite3.Connection, task_id: str) -> str:
    """下一轮 Prompt 顶部的结构化修正清单（HTTP 重试与流水线内部重跑共用）。"""
    digest = failure_digest(conn, task_id)
    if not digest["items"] and not digest["run_error"]:
        return ""
    lines = ["【上一轮验收失败 · 逐项修正清单】"]
    for item in digest["items"]:
        lines.append(f"判据{item['seq']}（{item['criterion']}）｜{item['layer']}")
        lines.append(f"  - 失败断言：{item['detail']}")
        if item["expected_name"]:
            lines.append(f"  - 期望产出文件：{item['expected_name']}")
        if item["stderr"]:
            lines.append(f"  - 错误输出摘要：{item['stderr']}")
    if digest["run_error"]:
        lines.append(f"【执行异常】{digest['run_error']}")
    if digest["actual_paths"]:
        lines.append("【上一轮实际产物】" + "；".join(digest["actual_paths"]))
    lines.append("上一轮指令未通过。本轮只做单点修正：在原文件上改，不要新建文件；"
                 "改完只回复 DONE。")
    return "\n".join(lines)


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
    skipped_tooling: list[str] = []
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
                if _TOOLING_FILE.match(path.name):
                    # 自检脚本不是交付文案，扫它必命中它自己要检的词
                    skipped_tooling.append(str(path))
                    continue
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
    snapshot = {"rules": rules, "artifacts": artifacts, "hits": hits, "error": error,
                "skipped_tooling": skipped_tooling}
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


def _blocking_criteria(conn, task_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, check_type, check_ref, state, is_blocking FROM acceptance_criteria"
        " WHERE task_id=? AND is_blocking=1 AND state<>'na'", (task_id,)).fetchall()]


def pending_human_signoffs(conn, project_id: str, *, entity: str | None = None) -> list[dict]:
    """项目范围内仍待人工签批的 blocking L1 `human_check` 判据。

    v5.4 规则 2：这个列表非空时，验收阶段必须挂起等人工，严禁自动置 passed。
    """
    sql = ("SELECT c.id, c.task_id, c.criterion, c.state, c.seq, t.title AS task_title"
           " FROM acceptance_criteria c JOIN tasks t ON t.id = c.task_id"
           " WHERE t.project_id = ? AND c.is_blocking = 1"
           " AND c.check_type = 'human_check' AND c.state NOT IN ('passed','na')")
    args: list = [project_id]
    if entity:
        sql += " AND c.legal_entity_id = ?"
        args.append(entity)
    sql += " ORDER BY c.task_id, c.seq"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def task_pending_human_signoffs(conn, task_id: str) -> list[dict]:
    """单个任务上仍待签批的 blocking L1 判据。"""
    return [c for c in _blocking_criteria(conn, task_id)
            if c["check_type"] == "human_check" and c["state"] != "passed"]


def awaiting_human_signoff(conn, task_id: str) -> bool:
    """任务是否「L0/L2 全绿、只差 blocking L1 人工签批」。

    这是 v5.4 规则 1 的判定单元：满足它时，build/test 不得把任务算作失败，
    但也不许把它当成 completed —— 任务停在 pending_review，等第 7 阶段统一验收。
    """
    blocking = _blocking_criteria(conn, task_id)
    if not blocking:
        return False
    if any(c["state"] == "failed" for c in blocking):
        return False
    auto = [c for c in blocking if c["check_type"] != "human_check"]
    human = [c for c in blocking if c["check_type"] == "human_check"]
    if not human:
        return False
    return (all(c["state"] == "passed" for c in auto)
            and any(c["state"] != "passed" for c in human))


def task_ready_to_settle(conn, task_id: str) -> bool:
    """人工签批后：blocking 判据是否已全部 passed（可以 pending_review -> completed）。"""
    blocking = _blocking_criteria(conn, task_id)
    return bool(blocking) and all(c["state"] == "passed" for c in blocking)


AUTO_SIGN_ENV = "M0_AUTO_SIGN_L1_FOR_TEST"

# 仓库内的生产库绝对路径。自动签批的唯一合法例外是「pytest + 非生产库」，
# 所以这里需要一条不可被 DB_PATH 覆盖的基准线。
_PRODUCTION_DB = pathlib.Path(__file__).resolve().parents[1] / "data" / "oa.db"

_SECURITY_VIOLATION = (
    f"SecurityViolation: {AUTO_SIGN_ENV} is strictly forbidden outside pytest "
    "or on the production database")

# 自建进程桩：即便有人篡改 sys.modules，也拿不到真实的 pytest 模块对象。
_REAL_PYTEST = sys.modules.get("pytest") if "pytest" in sys.modules else None


def _main_db_file(conn) -> str | None:
    """连接真正落到哪个文件。比读 DB_PATH 可靠：线程里 DB_PATH 可能已经漂移。"""
    try:
        for row in conn.execute("PRAGMA database_list").fetchall():
            if row[1] == "main":
                return row[2] or ""
    except sqlite3.Error:
        return None
    return None


def _sign_target_is_production(conn=None) -> bool:
    """这次自动签批会把 passed 写到哪个库？只有内存库能确定不是生产库。"""
    live = _main_db_file(conn) if conn is not None else None
    if live is not None and live in ("", ":memory:"):
        return False
    if live is None:
        from .db import db_path
        live = db_path()
    try:
        return pathlib.Path(live).resolve() == _PRODUCTION_DB.resolve()
    except (OSError, ValueError):
        return True              # 解析不了一律按危险处理（fail closed）


def auto_sign_for_test_enabled(conn=None) -> bool:
    """测试通道的双因子硬锁：**pytest 进程内** + **非生产库**，缺一即熔断。

    设计取舍：条件不满足时**抛异常**而不是静默返回 False。静默降级的后果是
    运维以为开关生效了、代码以为没生效，两边都拿不到真信号；直接炸出来才能
    让「有人把伪造签批后门开进了生产」当场暴露。变量本身没开时返回 False，
    不影响任何正常路径。
    """
    if os.environ.get(AUTO_SIGN_ENV) != "1":
        return False
    if _REAL_PYTEST is None or sys.modules.get("pytest") is not _REAL_PYTEST:
        raise RuntimeError(_SECURITY_VIOLATION + " [pytest module missing]")
    if _sign_target_is_production(conn):
        raise RuntimeError(_SECURITY_VIOLATION + " [target db is data/oa.db]")
    return True


def auto_sign_pending_l1(conn, task_id: str, *, reason: str = "flow_stage_accept") -> int:
    """**测试专用**：把仍待签批的 blocking L1 直接置 passed，返回改动条数。

    只在 `M0_AUTO_SIGN_L1_FOR_TEST=1`（生产严禁开启，且只应出现在 conftest 里）时生效，
    且受 `auto_sign_for_test_enabled()` 的双因子熔断约束。
    即使是测试通道也必然写 `l1_auto_signed_for_test` 审计，checked_by 明确标成
    `test-auto` —— 一旦有人把它开进生产，审计台账上会留下无法抵赖的痕迹。
    """
    if not auto_sign_for_test_enabled(conn):
        return 0
    rows = [dict(r) for r in conn.execute(
        "SELECT id, legal_entity_id FROM acceptance_criteria WHERE task_id=?"
        " AND is_blocking=1 AND check_type='human_check' AND state='pending'",
        (task_id,)).fetchall()]
    for r in rows:
        conn.execute("UPDATE acceptance_criteria SET state='passed', checked_by='test-auto',"
                     " note=? WHERE id=?", (f"auto-signed for test ({reason})", r["id"]))
        write_audit(conn, legal_entity_id=r["legal_entity_id"], verb="l1_manual",
                    task_id=task_id, layer="L1", actor="test-auto",
                    action="l1_auto_signed_for_test", target=r["id"],
                    payload={"env": AUTO_SIGN_ENV, "reason": reason},
                    response={"state": "passed"})
    return len(rows)


def auto_sign_project_l1(conn, project_id: str, *, reason: str = "accept_stage") -> int:
    """**测试专用**：把整个项目里仍待签批的 blocking L1 一次性放行，返回改动条数。

    M0_PIPELINE_EXEC=0 的 stub 分支和部分存量用例不会经过 acceptance_run.run，
    判据会一直停在 pending。生产路径必须挂起等人工；只有测试通道允许在进入验收时
    统一放行，且同样逐条写 l1_auto_signed_for_test 审计。
    """
    if not auto_sign_for_test_enabled(conn):
        return 0
    rows = conn.execute(
        "SELECT DISTINCT c.task_id FROM acceptance_criteria c JOIN tasks t ON t.id=c.task_id"
        " WHERE t.project_id=? AND c.is_blocking=1 AND c.check_type='human_check'"
        " AND c.state NOT IN ('passed','na')", (project_id,)).fetchall()
    return sum(auto_sign_pending_l1(conn, r["task_id"], reason=reason) for r in rows)


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
                detail = v.get("detail") or v.get("error") or v.get("reason")
                if not v.get("ok") and not detail:
                    detail = "脚本断言未通过"
            except l2_scripts.ScriptError as exc:
                state, detail = "failed", f"脚本执行失败: {exc}"
            except AssertionError as exc:
                state, detail = "failed", f"断言失败: {exc}"
            except Exception as exc:
                state, detail = "failed", f"执行异常: {type(exc).__name__}: {exc}"
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

    # v5.4 规则 4：仅测试通道（M0_AUTO_SIGN_L1_FOR_TEST=1）允许自动过 L1。
    auto_sign_pending_l1(conn, task_id, reason="acceptance_run")
    fresh = conn.execute(
        "SELECT * FROM acceptance_criteria WHERE task_id = ? AND legal_entity_id = ?"
        " ORDER BY seq", (task_id, entity)).fetchall()
    blocking = [c for c in fresh if c["is_blocking"] and c["state"] != "na"]
    verdict, event = "pending", None
    if any(c["state"] == "failed" for c in blocking):
        event, verdict = "review_failed", "rejected"
    elif blocking and all(c["state"] == "passed" for c in blocking):
        event, verdict = "review_passed", "completed"
        prior_runs = conn.execute("SELECT COUNT(*) c FROM task_runs WHERE task_id=?", (task_id,)).fetchone()["c"]
        if prior_runs > 1:
            try:
                from . import experience
                experience.capture_self_healed(conn, task_id)
            except Exception:
                pass

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
        failed_notes = [c["note"] for c in fresh if c["is_blocking"] and c["state"] == "failed" and c["note"]]
        failure_summary = "；".join(failed_notes) if failed_notes else "验收未通过"
        conn.execute(
            "UPDATE task_runs SET error=? WHERE task_id=? AND rowid=(SELECT MAX(rowid) FROM task_runs WHERE task_id=?)",
            (failure_summary[:500], task_id, task_id)
        )
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
            "results": results,
            "awaiting_human": awaiting_human_signoff(conn, task_id)}


def judge(conn: sqlite3.Connection, criterion_id: str, *, passed: bool, checked_by: str,
          note: str | None = None, evidence_snippet: str | None = None,
          file_path: str | None = None) -> dict:
    from .acceptance import l1_manual

    return l1_manual.apply(conn, criterion_id, passed=passed, checked_by=checked_by,
                           note=note, evidence_snippet=evidence_snippet,
                           file_path=file_path)
