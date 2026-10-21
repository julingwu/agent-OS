"""系统维护：找出并清理「只增不减」的历史残留。

第一性原则：系统里的数据分两类，处理方式必须不同——

1. **治理证据**（审批单、审计流水）：不能单条删，否则等于开了「无声抹除」的口子；
   只能按保留期整批清理，且清理动作本身要留痕。
2. **运行残留**（实时执行流事件、软删产物、孤儿 trace）：业务价值为零，
   只会在库里无限膨胀，可以随时按保留期清掉。

因此这里不提供"删单条"接口，只提供"按类别 + 保留天数"的批量清理，
且每次都必须先 dry_run 拿到将删数量，再决定是否真删。
"""
from __future__ import annotations

import pathlib
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import get_conn
from .api import _task_actor, _token

router = APIRouter(prefix="/ui", tags=["housekeeping"])

CATEGORIES = {
    "audit_logs": "审计流水",
    "run_stream": "实时执行流事件",
    "deleted_artifacts": "已删除产物的残留（记录 + 磁盘文件）",
    "orphan_traces": "找不到 Run 的孤儿追踪",
    "orphan_files": "工作区里没有任何数据库记录引用的孤儿文件",
    "wecom_outbox": "企业微信出站消息",
}

_DAYS = {"audit_logs": 90, "run_stream": 30, "deleted_artifacts": 0,
         "orphan_traces": 0, "orphan_files": 14, "wecom_outbox": 30}

_REF_TABLES = ("artifacts", "stage_deliverables", "project_artifacts")


def _norm(p) -> str:
    """归一化路径用于比较。故意不用 Path.resolve()：那会真的去访问磁盘，
    几千个文件时会把扫描拖到几秒；abspath+normcase 是纯字符串运算。"""
    import os
    return os.path.normcase(os.path.abspath(str(p)))


def _referenced_paths(conn) -> set[str]:
    """所有仍被数据库引用的磁盘路径。"""
    refs: set[str] = set()
    for table in _REF_TABLES:
        if not _table_exists(conn, table):
            continue
        try:
            rows = conn.execute(f"SELECT file_path FROM {table} WHERE file_path IS NOT NULL").fetchall()
        except sqlite3.OperationalError:
            continue
        for r in rows:
            refs.add(_norm(r["file_path"]))
    return refs


def _orphan_files(conn, ws_root: pathlib.Path, days: int) -> list[pathlib.Path]:
    """工作区里超过保留期、且没有任何表引用的文件。这是兜底清理：不依赖每个功能各自记得删。"""
    import os
    import time
    cutoff = time.time() - max(0, days) * 86400
    refs = _referenced_paths(conn)
    out: list[pathlib.Path] = []
    if not ws_root.exists():
        return out
    for dirpath, _dirnames, filenames in os.walk(ws_root):
        for name in filenames:
            if name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.stat(full).st_mtime > cutoff:
                    continue
            except OSError:
                continue
            if _norm(full) in refs:
                continue
            out.append(pathlib.Path(full))
    return out


def _table_exists(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def _count(conn, sql: str, args=()) -> int:
    try:
        return conn.execute(sql, args).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _snapshot(conn, days: dict, ws_root: pathlib.Path | None = None) -> dict:
    out = {}
    if _table_exists(conn, "ai_audit_logs"):
        out["audit_logs"] = _count(
            conn, "SELECT COUNT(*) FROM ai_audit_logs WHERE created_at < datetime('now', ?)",
            (f"-{days['audit_logs']} days",))
    if _table_exists(conn, "run_stream"):
        out["run_stream"] = _count(
            conn, "SELECT COUNT(*) FROM run_stream WHERE created_at < datetime('now', ?)",
            (f"-{days['run_stream']} days",))
    if _table_exists(conn, "wecom_outbox"):
        out["wecom_outbox"] = _count(
            conn, "SELECT COUNT(*) FROM wecom_outbox WHERE created_at < datetime('now', ?)",
            (f"-{days['wecom_outbox']} days",))
    if _table_exists(conn, "agent_traces"):
        out["orphan_traces"] = _count(
            conn, "SELECT COUNT(*) FROM agent_traces t WHERE NOT EXISTS"
                  " (SELECT 1 FROM task_runs r WHERE r.id=t.run_id)")
    rows = []
    if _table_exists(conn, "artifacts"):
        rows = conn.execute(
            "SELECT file_path FROM artifacts WHERE COALESCE(state,'active')='deleted'").fetchall()
    existing = [r["file_path"] for r in rows if r["file_path"] and pathlib.Path(r["file_path"]).is_file()]
    out["deleted_artifacts"] = len(rows)
    out["_deleted_artifact_files"] = existing
    if ws_root is not None:
        orphans = _orphan_files(conn, ws_root, days.get("orphan_files", 14))
        out["orphan_files"] = len(orphans)
        out["_orphan_files"] = orphans
    return out


class PurgeIn(BaseModel):
    categories: list[str] = Field(default_factory=list)
    dry_run: bool = True
    days: dict[str, int] | None = None


@router.get("/maintenance/stats")
def maintenance_stats(oa_ui: str | None = Depends(_token)):
    """各分类当前可清理的条数（只读，不改库）。"""
    from ..engine import dsh_adapter
    ws_root = pathlib.Path(dsh_adapter.workspace()).resolve()
    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:*")
        if actor["role"] not in {"ceo", "system"}:
            raise HTTPException(403, detail={"code": "Forbidden"})
        snap = _snapshot(conn, dict(_DAYS), ws_root)
    files = snap.pop("_deleted_artifact_files", [])
    orphans = snap.pop("_orphan_files", [])
    return {"categories": CATEGORIES, "counts": snap,
            "reclaimable_files": len(files) + len(orphans),
            "default_days": dict(_DAYS)}


@router.post("/maintenance/purge")
def maintenance_purge(payload: PurgeIn, oa_ui: str | None = Depends(_token)):
    """按分类批量清理。dry_run 默认 true；真删时会写入一条审计。"""
    unknown = [c for c in payload.categories if c not in CATEGORIES]
    if unknown:
        raise HTTPException(400, detail={"code": "UnknownCategory", "message": str(unknown)})
    if not payload.categories:
        raise HTTPException(400, detail={"code": "NoCategory"})

    days = dict(_DAYS)
    for k, v in (payload.days or {}).items():
        if k in days:
            days[k] = max(0, int(v))

    from ..engine import dsh_adapter
    ws_root = pathlib.Path(dsh_adapter.workspace()).resolve()
    repo = pathlib.Path(__file__).resolve().parents[2]

    with get_conn() as conn:
        actor = _task_actor(conn, oa_ui, "task:*")
        if actor["role"] not in {"ceo", "system"}:
            raise HTTPException(403, detail={"code": "Forbidden"})
        snap = _snapshot(conn, days, ws_root)
        files = snap.pop("_deleted_artifact_files", [])
        orphans = snap.pop("_orphan_files", [])
        planned = {c: snap.get(c, 0) for c in payload.categories}

        if payload.dry_run:
            return {"ok": True, "dry_run": True, "planned": planned,
                    "reclaimable_files": len(files) + len(orphans)}

        deleted = {}
        if "audit_logs" in payload.categories and _table_exists(conn, "ai_audit_logs"):
            cur = conn.execute("DELETE FROM ai_audit_logs WHERE created_at < datetime('now', ?)",
                               (f"-{days['audit_logs']} days",))
            deleted["audit_logs"] = cur.rowcount or 0
        if "run_stream" in payload.categories and _table_exists(conn, "run_stream"):
            cur = conn.execute("DELETE FROM run_stream WHERE created_at < datetime('now', ?)",
                               (f"-{days['run_stream']} days",))
            deleted["run_stream"] = cur.rowcount or 0
        if "wecom_outbox" in payload.categories and _table_exists(conn, "wecom_outbox"):
            cur = conn.execute("DELETE FROM wecom_outbox WHERE created_at < datetime('now', ?)",
                               (f"-{days['wecom_outbox']} days",))
            deleted["wecom_outbox"] = cur.rowcount or 0
        if "orphan_traces" in payload.categories and _table_exists(conn, "agent_traces"):
            ids = [r["trace_id"] for r in conn.execute(
                "SELECT trace_id FROM agent_traces t WHERE NOT EXISTS"
                " (SELECT 1 FROM task_runs r WHERE r.id=t.run_id)")]
            if ids and _table_exists(conn, "trace_spans"):
                q = ",".join("?" * len(ids))
                conn.execute(f"DELETE FROM trace_spans WHERE trace_id IN ({q})", ids)
            cur = conn.execute(
                "DELETE FROM agent_traces WHERE NOT EXISTS"
                " (SELECT 1 FROM task_runs r WHERE r.id=agent_traces.run_id)")
            deleted["orphan_traces"] = cur.rowcount or 0
        if "deleted_artifacts" in payload.categories and _table_exists(conn, "artifacts"):
            cur = conn.execute("DELETE FROM artifacts WHERE COALESCE(state,'active')='deleted'")
            deleted["deleted_artifacts"] = cur.rowcount or 0

        if deleted:
            from ..audit import write_audit
            write_audit(conn, legal_entity_id=actor["legal_entity_id"], verb="admin",
                        actor=actor["id"], layer="system", action="maintenance_purged",
                        target="housekeeping",
                        payload={"categories": payload.categories, "days": days},
                        response={"deleted": deleted})
        conn.commit()

    # 磁盘：只清理工作区 / data 内的残留；范围外的一律跳过并如实上报
    removed_files = 0
    skipped_files = 0

    def _unlink(target: pathlib.Path) -> None:
        nonlocal removed_files, skipped_files
        inside = False
        for root in (ws_root, (repo / "data").resolve()):
            try:
                target.resolve().relative_to(root)
                inside = True
                break
            except (ValueError, OSError):
                continue
        if not inside:
            skipped_files += 1
            return
        try:
            if target.is_file():
                target.unlink()
                removed_files += 1
        except OSError:
            skipped_files += 1

    if "deleted_artifacts" in payload.categories:
        for raw in files:
            _unlink(pathlib.Path(raw))
    if "orphan_files" in payload.categories:
        for target in orphans:
            _unlink(target)

    return {"ok": True, "dry_run": False, "deleted": deleted,
            "files_removed": removed_files, "files_skipped": skipped_files}
