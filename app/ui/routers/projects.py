"""UI Sub-router: Projects & Pipelines."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time

from fastapi import (APIRouter, Cookie, Depends, File, Header, HTTPException, Query,
                      Response, UploadFile)
from pydantic import BaseModel, Field

from ...audit import write_audit
from ...db import connect as db_connect, db_path, get_conn, spawn_bg_thread
from ...engine import dsh_adapter
from ... import tenant, tenancy
from .. import intel, store
from ..common import (
    COOKIE, SESSION_TTL, _secret, _sign, _make_cookie, _read_cookie,
    _token, _require, _tenant_of, _task_actor, _task_write_entities,
    _task_allowed_actions, _task_execution_summary, _budget_for,
)

router = APIRouter()

class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    requirement: str = Field(..., min_length=1)
    flow: str | None = "FLOW-SOFTWARE"
    use_llm_docs: int = 1


@router.post("/projects")
def create_project_route(payload: ProjectCreate, oa_ui: str | None = Depends(_token)):
    """主动创建流水线项目：支持指定模板（FLOW-SOFTWARE/FLOW-ANALYSIS 等）与立项预算初始化"""
    who = _require(oa_ui)
    with get_conn() as conn:
        from ... import pipeline, flows, finance
        pipeline.ensure(conn)
        actor = _task_actor(conn, oa_ui, "task:write")
        entity_id = actor.get("legal_entity_id") or "LE-GROUP-HQ"
        tenant_id = actor.get("tenant_id") or "TENANT-HQ"

        flow_id = payload.flow or "FLOW-SOFTWARE"
        created = pipeline.create_project(
            conn,
            name=payload.name.strip(),
            requirement=payload.requirement.strip(),
            entity=entity_id,
            owner=actor["id"],
            use_llm_docs=payload.use_llm_docs,
            tenant_id=tenant_id,
            flow=flow_id,
        )
        pid = created["project_id"]

        try:
            tmpl = flows.template(conn, flow_id) if flow_id else None
            nodes = tmpl.get("nodes") if tmpl else None
            if not nodes:
                nodes = [{"key": k, "kind": k, "role": "engineer", "title": t} for k, t in pipeline.STAGES]
            finance.open_project(conn, pid, nodes, requirement=payload.requirement,
                                 flow_id=flow_id or "", tenant_id=tenant_id)
        except Exception:
            pass

        from ...audit import write_audit
        write_audit(conn, legal_entity_id=entity_id, verb="project", task_id=None,
                    actor=actor["id"], layer="system", action="project_created_ui",
                    target=pid, payload={"name": payload.name, "flow": flow_id},
                    response={"ok": True, "project_id": pid})
        conn.commit()

        # 自动点火：立即拉起后台异步调度线程，推进流水线依次执行阶段！
        req_text = payload.requirement.strip()
        # v5.3：把「本次请求所属的库」固定下来。后台线程若晚于 DB_PATH 变更才执行
        # （测试里下一个用例就会改），用 get_conn() 会连到别人的库，既造成跨用例写污染，
        # 也会让新用例建库时撞 database is locked。生产只有一个库，行为完全等价。
        owner_db = db_path()

        def _worker() -> None:
            import traceback
            from ... import pipeline as _pipe
            c2 = db_connect(owner_db)
            try:
                _pipe.run_project(c2, pid, req_text)
            except Exception:
                traceback.print_exc()
            try:
                c2.commit()
            finally:
                c2.close()

        # v5.4.2 §79：统一派发口负责「绑定 owner_db + 登记线程」，用例 teardown
        # 靠 wait_for_bg_threads() 排队清，线程不会再活到下一个用例。
        spawn_bg_thread(_worker, name=f"oa-pipe-{pid}", db=owner_db)

        proj = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        stages = conn.execute("SELECT * FROM project_stages WHERE project_id=? ORDER BY seq ASC", (pid,)).fetchall()

    return {"ok": True, "project_id": pid, "project": dict(proj) if proj else None,
            "stages_count": len(stages), "flow_id": flow_id}


@router.get("/projects")
def list_projects(oa_ui: str | None = Depends(_token), limit: int = 50):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import pipeline
        pipeline.ensure(conn)
        actor = _task_actor(conn, oa_ui, "task:read")
        where, args = tenant.clause(conn, actor, "p.legal_entity_id")
        rows = conn.execute(
            f"""SELECT p.*, (SELECT COUNT(*) FROM project_stages s
                            WHERE s.project_id=p.id AND s.state='done') AS done_stages
               FROM projects p WHERE {where} AND p.tenant_id=?
               ORDER BY p.created_at DESC LIMIT ?""",
            (*args, actor["tenant_id"], max(1, min(200, limit)))).fetchall()
    return {"projects": [dict(r) for r in rows]}


class ProjectPatch(BaseModel):
    use_llm_docs: int | None = None
    state: str | None = None


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, payload: ProjectPatch,
                  oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import pipeline
        pipeline.ensure(conn)
        actor = _task_actor(conn, oa_ui, "task:*")
        if actor["role"] not in {"ceo", "system"}:
            raise HTTPException(403, detail={"code": "Forbidden"})
        if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        if payload.use_llm_docs is not None:
            conn.execute("UPDATE projects SET use_llm_docs=? WHERE id=?",
                         (1 if payload.use_llm_docs else 0, project_id))
        if payload.state:
            raise HTTPException(422, detail={"code": "ProjectRecoveryRequired",
                                             "message": "请提交修改后的需求重新检查项目。"})
        conn.commit()
        p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return {"ok": True, "project": dict(p)}


def _in_clause(col: str, ids: list) -> tuple[str, list]:
    return f"{col} IN ({','.join('?' * len(ids))})", list(ids)


def _under(path, root) -> bool:
    """只允许清理工作区/数据区内的路径，绝不碰系统目录。"""
    import pathlib
    try:
        pathlib.Path(path).resolve().relative_to(pathlib.Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, oa_ui: str | None = Depends(_token)):
    """彻底删除项目：数据库关联行 + 磁盘交付物 + Run 轨迹一次清干净。

    不可恢复，只允许 ceo / system 执行；项目仍有 Run 在跑时直接拒绝。
    """
    import pathlib
    import shutil

    _require(oa_ui)
    from ...engine import dsh_adapter
    repo = pathlib.Path(__file__).resolve().parents[2]
    root = pathlib.Path(dsh_adapter.workspace())

    with get_conn() as conn:
        from ... import pipeline
        pipeline.ensure(conn)
        from ..access import is_deployment_admin
        actor = _task_actor(conn, oa_ui, "task:*")
        if actor["role"] not in {"ceo", "system", "operator", "admin"} and not is_deployment_admin(actor):
            raise HTTPException(403, detail={"code": "Forbidden",
                                             "message": "只有管理员可以删除项目"})
        proj = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if proj is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        tenant_where, tenant_args = tenant.clause(conn, actor, "p.legal_entity_id")
        if conn.execute(
                f"SELECT 1 FROM projects p WHERE p.id=? AND p.tenant_id=? AND {tenant_where}",
                (project_id, actor["tenant_id"], *tenant_args)).fetchone() is None:
            raise HTTPException(403, detail={"code": "Forbidden"})

        running = conn.execute(
            "SELECT COUNT(*) FROM task_runs r JOIN tasks t ON t.id=r.task_id"
            " WHERE t.project_id=? AND r.state IN ('spawned','running','paused')",
            (project_id,)).fetchone()[0]
        if running:
            raise HTTPException(409, detail={
                "code": "ProjectRunning",
                "message": f"该项目还有 {running} 个 Run 在执行，请先终止或等它结束再删除。"})

        task_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM tasks WHERE project_id=?", (project_id,))]

        def rows(sql: str, params=(list,)) -> list:
            """老库可能还没有 v4.x 的某些表，缺失就当空表处理。"""
            try:
                return conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []

        run_ids = []
        if task_ids:
            sql, params = _in_clause("task_id", task_ids)
            run_ids = [r["id"] for r in rows(f"SELECT id FROM task_runs WHERE {sql}", params)]
        trace_ids = [r["trace_id"] for r in rows(
            "SELECT trace_id FROM agent_traces WHERE project_id=?", (project_id,))]

        # 删库前先把磁盘路径收集齐
        paths: set[str] = set()
        if run_ids:
            sql, params = _in_clause("run_id", run_ids)
            for r in rows(f"SELECT file_path FROM artifacts WHERE {sql}", params):
                if r["file_path"]:
                    paths.add(r["file_path"])
        for r in rows("SELECT file_path FROM stage_deliverables WHERE project_id=?", (project_id,)):
            if r["file_path"]:
                paths.add(r["file_path"])
        for r in rows("SELECT file_path FROM project_artifacts WHERE project_id=?"
                      " AND file_path IS NOT NULL", (project_id,)):
            if r["file_path"]:
                paths.add(r["file_path"])

        counts: dict[str, int] = {}

        def drop(table: str, sql: str, params) -> None:
            try:
                cur = conn.execute(sql, params)
                if (cur.rowcount or 0) > 0:
                    counts[table] = counts.get(table, 0) + cur.rowcount
            except sqlite3.OperationalError:
                pass          # 老库可能还没有这张表

        if run_ids:
            sql, params = _in_clause("run_id", run_ids)
            for t in ("artifacts", "run_locks", "run_stream", "run_diagnoses",
                      "redline_scans", "tool_approvals", "eval_results", "ai_audit_logs"):
                drop(t, f"DELETE FROM {t} WHERE {sql}", params)

        if task_ids:
            sql, params = _in_clause("task_id", task_ids)
            for t in ("acceptance_criteria", "budgets", "wbs_items", "agent_traces",
                      "chat_messages", "decision_packages", "wecom_approvals",
                      "eval_sets", "eval_results", "tool_approvals", "run_diagnoses",
                      "redline_scans", "ai_audit_logs", "task_runs"):
                drop(t, f"DELETE FROM {t} WHERE {sql}", params)

        if trace_ids:
            sql, params = _in_clause("trace_id", trace_ids)
            drop("trace_spans", f"DELETE FROM trace_spans WHERE {sql}", params)

        for t in ("project_stages", "stage_gates", "stage_deliverables",
                  "project_artifacts", "wbs_items", "project_budgets",
                  "stage_budgets", "budget_approvals", "budget_events",
                  "eval_sets", "agent_traces", "flows"):
            column = "id" if t == "flows" else "project_id"
            drop(t, f"DELETE FROM {t} WHERE {column}=?", (project_id,))

        ws_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM workshops WHERE project_id=?", (project_id,))]
        if ws_ids:
            sql, params = _in_clause("workshop_id", ws_ids)
            drop("workshop_turns", f"DELETE FROM workshop_turns WHERE {sql}", params)
        drop("workshops", "DELETE FROM workshops WHERE project_id=?", (project_id,))

        drop("tasks", "DELETE FROM tasks WHERE project_id=?", (project_id,))
        drop("projects", "DELETE FROM projects WHERE id=?", (project_id,))

        write_audit(conn, legal_entity_id=proj["legal_entity_id"], verb="admin",
                    actor=actor["id"], layer="system", action="project_deleted",
                    target=project_id,
                    payload={"project_id": project_id, "name": proj["name"],
                             "task_ids": task_ids, "run_ids": run_ids},
                    response={"deleted": counts})
        conn.commit()

    # 磁盘清理放在提交之后：库里已经删掉了，磁盘失败也不回滚
    removed_files = removed_dirs = 0
    file_targets = [pathlib.Path(raw) for raw in paths
                    if (_under(raw, root) or _under(raw, repo / "data"))
                    and pathlib.Path(raw).is_file()]
    dirs = [root / "projects" / project_id]
    for tid in task_ids:
        dirs.extend(d for d in root.glob(f"*/{tid}") if d.is_dir())
    for d in dirs:
        if _under(d, root) and d.exists():
            removed_files += sum(1 for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
            removed_dirs += 1

    # 目录外的散落交付物（例如直接写在 data/ 下）单独删
    for fp in file_targets:
        if fp.exists():
            try:
                fp.unlink()
                removed_files += 1
            except OSError:
                pass

    runs_dir = repo / "docs" / "runs"
    if runs_dir.is_dir():
        for rid in run_ids:
            for f in runs_dir.glob(f"{rid}*.jsonl"):
                if _under(f, runs_dir):
                    try:
                        f.unlink()
                        removed_files += 1
                    except OSError:
                        pass

    return {"ok": True, "project_id": project_id, "deleted": counts,
            "files_removed": removed_files, "dirs_removed": removed_dirs}

class BatchProjectsDeleteIn(BaseModel):
    project_ids: list[str]


@router.post("/projects/batch-delete")
def batch_delete_projects(payload: BatchProjectsDeleteIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    results = []
    errors = []
    for pid in payload.project_ids:
        try:
            delete_project(pid, oa_ui=oa_ui)
            results.append(pid)
        except Exception as e:
            errors.append(f"{pid}: {str(e)}")
    return {"ok": True, "deleted": results, "errors": errors}


@router.get("/projects/{project_id}")
def project_detail(project_id: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import pipeline
        pipeline.ensure(conn)
        p = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        actor = _task_actor(conn, oa_ui, "task:read")
        stages = [dict(r) for r in conn.execute(
            "SELECT * FROM project_stages WHERE project_id=? ORDER BY seq", (project_id,))]
        gates = [dict(r) for r in conn.execute(
            "SELECT * FROM stage_gates WHERE project_id=? ORDER BY rowid", (project_id,))]
        raw = [dict(r) for r in conn.execute(
            "SELECT * FROM stage_deliverables WHERE project_id=? ORDER BY rowid", (project_id,))]
        # 按 (stage, kind) 只留最新一条 —— 重跑阶段会留下旧记录
        seen: dict[tuple, dict] = {}
        for d in raw:
            seen[(d["stage_key"], d["kind"])] = d
        dels = list(seen.values())
        wbs = [dict(r) for r in conn.execute(
            "SELECT * FROM wbs_items WHERE project_id=? ORDER BY code", (project_id,))]
        ws = conn.execute("SELECT * FROM workshops WHERE project_id=? LIMIT 1",
                          (project_id,)).fetchone()
        turns = [dict(r) for r in conn.execute(
            "SELECT * FROM workshop_turns WHERE workshop_id=? ORDER BY round, rowid",
            (ws["id"],))] if ws else []
        tl = pipeline.timeline(conn, project_id)
        budget = _budget_of(conn, project_id)
    can_manage = actor["role"] in {"ceo", "system"} and (
        actor["role"] == "system" or p["legal_entity_id"] == tenant.entity_of(actor))
    return {"project": dict(p), "allowed_actions": (["configure", "retry"] if can_manage else []),
            "stages": stages, "gates": gates, "deliverables": dels,
            "wbs": wbs, "workshop": dict(ws) if ws else None, "turns": turns,
            "timeline": tl, "budget": budget}


def _budget_of(conn, pid: str) -> dict:
    try:
        from ... import finance
        b = finance.overview(conn, pid)
    except Exception as exc:             # noqa: BLE001 - 预算表没建好也不该打不开项目
        import logging
        logging.getLogger("ui.api").warning("budget overview failed for %s: %s", pid, exc)
        b = {"project_id": pid, "pool": 0, "spent": 0, "remaining": 0, "stages": [],
             "state": "unknown"}
    total_tokens = conn.execute(
        """SELECT COALESCE(SUM(usage_tokens), 0) FROM task_runs
           WHERE task_id IN (SELECT id FROM tasks WHERE project_id=?)""",
        (pid,)).fetchone()[0]
    b["used_tokens"] = total_tokens
    b["cost_rmb"] = round(total_tokens * 0.00005, 2)
    return b


# ------------------------------------------- 预算池（v4.3）
class BudgetDecisionIn(BaseModel):
    approve: bool = True
    tokens: int | None = None


@router.get("/projects/{project_id}/budget")
def project_budget(project_id: str, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        return _budget_of(conn, project_id)


@router.get("/budgets/projects")
def budget_projects(limit: int = 30, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import finance
        return {"projects": finance.recent_projects(conn, limit=limit)}


@router.get("/budgets/approvals")
def budget_approvals(limit: int = 50, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import finance
        return {"approvals": finance.pending(conn, limit=limit)}


@router.post("/budgets/approvals/{approval_id}")
def budget_decide(approval_id: str, payload: BudgetDecisionIn,
                  oa_ui: str | None = Depends(_token)):
    who = _require(oa_ui)
    with get_conn() as conn:
        from ... import finance
        try:
            return finance.decide(conn, approval_id, approve=payload.approve,
                                  actor=who, tokens=payload.tokens)
        except KeyError:
            raise HTTPException(404, detail={"code": "ApprovalNotFound"}) from None
        except ValueError:
            raise HTTPException(409, detail={"code": "ApprovalAlreadyDecided"}) from None


# ------------------------------------------- 续跑（v5.3：stage_keys 精确过滤）
class ProjectResumeIn(BaseModel):
    stage_keys: list[str] | None = None


def _resume_target_keys(conn, project_id: str, stage_keys: list[str] | None) -> list[str]:
    """把 stage_keys 过滤成「本次真正要续跑」的阶段列表（按 seq 升序）。

    * stage_keys 为 None：完全沿用 v5.2 及以前的行为——返回所有非 done 阶段；
    * 传入 stage_keys：只保留命中的非 done 阶段；未知 key 报 400，跳阶段报 409。

    为什么要在路由里先挡「跳阶段」：`enter_stage` 会把阶段置 active，而
    `trg_stage_no_skip` 要求 seq-1 阶段必须 done，否则 RAISE(ABORT,'StageSkipped')。
    后台线程里 raise 会被吞掉，用户只看到「什么都没发生」，所以这里显式报错，
    而不是去动触发器。若前置阶段本次同批续跑且按 seq 顺序先执行完，则合法放行。
    """
    rows = conn.execute(
        "SELECT key,seq,state FROM project_stages WHERE project_id=? ORDER BY seq",
        (project_id,)).fetchall()
    if stage_keys is None:
        return [r["key"] for r in rows if r["state"] != "done"]

    known = {r["key"] for r in rows}
    asked: list[str] = []
    for key in stage_keys:
        if key not in asked:
            asked.append(key)
    if not asked:
        raise HTTPException(400, detail={"code": "StageKeysEmpty",
                                         "message": "stage_keys 不能是空列表"})
    unknown = [k for k in asked if k not in known]
    if unknown:
        raise HTTPException(400, detail={"code": "UnknownStageKey",
                                         "message": "项目里没有这些阶段：" + ", ".join(unknown),
                                         "unknown": unknown})

    by_seq = {r["seq"]: r for r in rows}
    target: list[str] = []
    will_done: set[str] = set()
    for row in rows:                                   # 天然按 seq 升序，保证触发器顺序
        if row["key"] not in asked or row["state"] == "done":
            continue
        prev = by_seq.get(row["seq"] - 1)
        if prev is not None and prev["state"] != "done" and prev["key"] not in will_done:
            raise HTTPException(409, detail={
                "code": "StageSkipNotAllowed",
                "message": (f"阶段 {row['key']} 的前置阶段 {prev['key']} 尚未完成，"
                            f"直接续跑会撞 StageSkipped 触发器；请先续跑 {prev['key']}"),
                "stage": row["key"], "blocked_by": prev["key"], "blocked_by_state": prev["state"]})
        target.append(row["key"])
        will_done.add(row["key"])
    return target


@router.post("/projects/{project_id}/resume")
def project_resume(project_id: str, payload: ProjectResumeIn | None = None,
                   stage_keys: list[str] | None = Query(None),
                   oa_ui: str | None = Depends(_token)):
    """预算批下来后继续跑：把未完成阶段复位，从断点接着推进。

    v5.3：可选 `stage_keys`（body 优先于同名 query）只续跑指定阶段；
    不传时行为与旧版完全一致——复位全部非 done 阶段。
    """
    who = _require(oa_ui)
    asked = (payload.stage_keys
             if payload is not None and payload.stage_keys is not None else stage_keys)
    with get_conn() as conn:
        from ... import finance, pipeline
        pipeline.ensure(conn)
        row = conn.execute("SELECT requirement,state,legal_entity_id FROM projects WHERE id=?",
                           (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        ov = finance.overview(conn, project_id)
        if ov.get("state") == "halted":
            raise HTTPException(409, detail={"code": "BudgetHalted",
                                             "message": "项目预算池已见底，不能再开工"})
        keys = _resume_target_keys(conn, project_id, asked)
        if not keys:
            return {"ok": True, "already_done": True}
        # 只复位本次要续跑的阶段：其余非 done 阶段原地保持，等它自己被点名。
        conn.execute(
            "UPDATE project_stages SET state='pending' WHERE project_id=?"
            " AND state<>'done' AND key IN (%s)" % ",".join("?" * len(keys)),
            (project_id, *keys))
        conn.execute("UPDATE projects SET state='active' WHERE id=?", (project_id,))
        finance.set_state(conn, project_id, "active")
        if asked is not None:
            write_audit(conn, legal_entity_id=(row["legal_entity_id"] or "LE-GROUP-HQ"),
                        verb="project", task_id=None, actor=who, layer="system",
                        action="project_resume_scoped", target=project_id,
                        payload={"stage_keys": list(asked), "resuming": keys},
                        response={"ok": True})
        conn.commit()
        requirement = row["requirement"] or ""

    owner_db = db_path()

    def _worker() -> None:
        # 同上：后台续跑必须绑定发起请求时的库，不能到执行时再读 DB_PATH。
        c2 = db_connect(owner_db)
        try:
            pipeline.run_project(c2, project_id, requirement, keys=keys)
        except Exception:            # noqa: BLE001 - 后台线程不能炸
            pass
        try:
            c2.commit()
        finally:
            c2.close()

    spawn_bg_thread(_worker, name="oa-resume", db=owner_db)
    if asked is None:                    # 旧调用方：返回结构保持完全一致
        return {"ok": True, "project_id": project_id, "resuming": keys}
    return {"ok": True, "project_id": project_id, "resuming": keys,
            "stage_keys": list(asked)}


# ------------------------------------------- 阶段详情 / 流程模板（v4.1）
class FlowTemplateIn(BaseModel):
    id: str
    name: str
    kind: str = "custom"
    description: str = ""
    nodes: list[dict]


@router.get("/flow-templates")
def flow_templates(oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        from ... import flows
        return {"templates": flows.templates(conn)}


@router.post("/flow-templates")
def create_flow_template(payload: FlowTemplateIn, oa_ui: str | None = Depends(_token)):
    """动态注册/定制企业专属交付流水线模板。"""
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:create")
        if actor["role"] not in ("ceo", "system", "admin"):
            raise HTTPException(403, detail={"code": "Forbidden"})
        from ... import flows
        flows.ensure(conn)
        if not payload.id.startswith("FLOW-"):
            raise HTTPException(400, detail={"code": "InvalidFlowId", "message": "flow id must start with FLOW-"})
        if not payload.nodes:
            raise HTTPException(400, detail={"code": "NodesRequired"})
        conn.execute(
            "INSERT OR REPLACE INTO flow_templates (id, name, kind, description, nodes, enabled)"
            " VALUES (?, ?, ?, ?, ?, 1)",
            (payload.id, payload.name, payload.kind, payload.description,
             json.dumps(payload.nodes, ensure_ascii=False))
        )
        conn.commit()
    return {"ok": True, "flow_id": payload.id, "nodes": len(payload.nodes)}


@router.delete("/flow-templates/{flow_id}")
def delete_flow_template(flow_id: str, oa_ui: str | None = Depends(_token)):
    """删除自定义流水线模板。内置模板（FLOW-SOFTWARE 等）不可删除。"""
    _require(oa_ui)
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:create")
        if actor["role"] not in ("ceo", "system", "admin"):
            raise HTTPException(403, detail={"code": "Forbidden"})
        from ... import flows
        flows.ensure(conn)
        row = conn.execute("SELECT id, name FROM flow_templates WHERE id=?", (flow_id,)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "FlowNotFound"})
        if flow_id in flows.PRESETS:
            raise HTTPException(409, detail={"code": "BuiltinFlowUndeletable",
                                             "message": "内置流程模板不可删除，只能停用自定义模板"})
        conn.execute("DELETE FROM flow_templates WHERE id=?", (flow_id,))
        write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="admin", actor=actor["id"],
                    layer="system", action="flow_template_deleted", target=flow_id,
                    payload={"name": row["name"]}, response={"deleted": True})
        conn.commit()
    return {"ok": True, "deleted": flow_id}


@router.get("/projects/{project_id}/stages/{stage_key}")
def project_stage(project_id: str, stage_key: str, oa_ui: str | None = Depends(_token)):
    """阶段详情：交付物 + 闸门 + 审计 + 历史 Run/Trace（点已完成阶段进来的页）。"""
    _require(oa_ui)
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
        _task_actor(conn, oa_ui, "task:read")
        from ... import flows
        flows.ensure(conn)
        try:
            return flows.stage_detail(conn, project_id, stage_key)
        except KeyError:
            raise HTTPException(404, detail={"code": "StageNotFound"}) from None


class AssignIn(BaseModel):
    employee_id: str


@router.get("/projects/{project_id}/stages/{stage_key}/files/{delivery_id}")
def project_stage_file(project_id: str, stage_key: str, delivery_id: str,
                       preview: int = 0,
                       oa_ui: str | None = Depends(_token)):
    """下载或在线预览阶段交付物（阶段详情页里的「下载」或观测面板里的「预览」）。"""
    import pathlib as _pathlib

    from fastapi.responses import FileResponse

    _require(oa_ui)
    with get_conn() as conn:
        row = conn.execute("SELECT file_path FROM stage_deliverables"
                           " WHERE id=? AND project_id=? AND stage_key=?",
                           (delivery_id, project_id, stage_key)).fetchone()
        if row is None:
            raise HTTPException(404, detail={"code": "FileNotFound"})
        path = _pathlib.Path(row["file_path"])
        if not path.is_file():
            raise HTTPException(404, detail={"code": "FileNotFound"})

    if preview:
        sfx = path.suffix.lower()
        media_type = "text/plain; charset=utf-8"
        if sfx in (".html", ".htm"):
            media_type = "text/html; charset=utf-8"
        elif sfx == ".json":
            media_type = "application/json; charset=utf-8"
        elif sfx == ".md":
            media_type = "text/markdown; charset=utf-8"
        elif sfx in (".py", ".js", ".ts", ".sh", ".sql", ".css", ".yaml", ".yml", ".ps1"):
            media_type = "text/plain; charset=utf-8"
        elif sfx in (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"):
            media_type = f"image/{sfx.lstrip('.')}"
        return FileResponse(path, media_type=media_type)

    return FileResponse(path, filename=path.name)


# ---------------------------------------------------- V4.4 出厂交付包与在线预览
@router.get("/projects/{project_id}/release/files")
def project_release_files(project_id: str, oa_ui: str | None = Depends(_token)):
    """获取最终出厂交付物目录文件列表。"""
    _require(oa_ui)
    with get_conn() as conn:
        p = conn.execute("SELECT id, name FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
    from ...engine import dsh_adapter
    rel_dir = dsh_adapter.workspace() / "projects" / project_id / "release"
    if not rel_dir.exists():
        return {"ok": True, "project_id": project_id, "files": [], "has_release": False}
    files = []
    for f in sorted(rel_dir.iterdir(), key=lambda x: x.name):
        if f.is_file():
            files.append({
                "name": f.name,
                "bytes": f.stat().st_size,
                "modified": int(f.stat().st_mtime),
                "ext": f.suffix.lower(),
                "is_previewable": f.suffix.lower() in (".html", ".htm", ".json", ".md", ".txt", ".py")
            })
    return {"ok": True, "project_id": project_id, "files": files, "has_release": len(files) > 0}


@router.get("/projects/{project_id}/release/zip")
def download_release_zip(project_id: str, oa_ui: str | None = Depends(_token)):
    """一键打包下载全部出厂交付物 (.zip)。"""
    _require(oa_ui)
    import io
    import zipfile
    from fastapi.responses import Response

    with get_conn() as conn:
        p = conn.execute("SELECT id, name FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
    from ...engine import dsh_adapter
    rel_dir = dsh_adapter.workspace() / "projects" / project_id / "release"
    if not rel_dir.exists():
        raise HTTPException(404, detail={"code": "ReleaseNotFound"})

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in rel_dir.iterdir():
            if f.is_file():
                zf.write(f, arcname=f.name)
    zip_buffer.seek(0)
    filename = f"{project_id}_release.zip"
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@router.get("/projects/{project_id}/release/preview/{filename}")
def preview_release_file(project_id: str, filename: str, oa_ui: str | None = Depends(_token)):
    """在线预览/查看出厂交付物（HTML 网页渲染、JSON 契约查看等）。"""
    _require(oa_ui)
    from fastapi.responses import FileResponse
    # 路径穿越防范
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, detail={"code": "InvalidFilename"})
    with get_conn() as conn:
        p = conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
        if p is None:
            raise HTTPException(404, detail={"code": "ProjectNotFound"})
    from ...engine import dsh_adapter
    file_path = dsh_adapter.workspace() / "projects" / project_id / "release" / filename
    if not file_path.is_file():
        raise HTTPException(404, detail={"code": "FileNotFound"})

    sfx = file_path.suffix.lower()
    media_type = "text/plain; charset=utf-8"
    if sfx in (".html", ".htm"):
        media_type = "text/html; charset=utf-8"
    elif sfx == ".json":
        media_type = "application/json; charset=utf-8"
    elif sfx == ".md":
        media_type = "text/markdown; charset=utf-8"
    elif sfx == ".css":
        media_type = "text/css; charset=utf-8"
    elif sfx == ".js":
        media_type = "application/javascript; charset=utf-8"

    return FileResponse(file_path, media_type=media_type)


@router.post("/projects/{project_id}/stages/{stage_key}/assign")
def project_stage_assign(project_id: str, stage_key: str, payload: AssignIn,
                         oa_ui: str | None = Depends(_token)):
    """PM 指定阶段执行人（同角色多人时可并行分工）。"""
    _require(oa_ui)
    from .. import dispatcher
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:write")
        try:
            return dispatcher.assign(conn, project_id, stage_key, payload.employee_id,
                                     actor=actor["id"])
        except KeyError as exc:
            raise HTTPException(404, detail={"code": str(exc).strip("'")}) from None
        except PermissionError as exc:
            raise HTTPException(403, detail={"code": str(exc)}) from None


# ------------------------------------------------------- Tool ACL（v1.5 战役二）
