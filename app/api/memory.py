"""Memory endpoints (F2): reads are scoped by entity and pack, not filtered later."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import memory, tenant
from ..db import get_conn
from ..identity import require
from ..models import MemoryIn

router = APIRouter(tags=["memory"])


def _employee(conn, ident: dict):
    employee_id = ident.get("employee_id")
    if not employee_id:
        raise HTTPException(400, detail={"code": "NoEmployeeBound",
                                         "message": "这个身份没有绑定 AI 员工，无法读写记忆"})
    emp = conn.execute("SELECT * FROM ai_employees WHERE id = ?", (employee_id,)).fetchone()
    if emp is None:
        raise HTTPException(404, detail={"code": "EmployeeNotFound", "id": employee_id})
    if emp["legal_entity_id"] != tenant.entity_of(ident):
        raise HTTPException(403, detail={"code": "EmployeeEntityMismatch",
                                         "employee": employee_id})
    return emp


@router.post("/memory")
def write_memory(req: MemoryIn, _ident: dict = Depends(require("memory:write"))):
    with get_conn() as conn:
        emp = _employee(conn, _ident)
        pack = tenant.pack_of(conn, _ident)
        try:
            return memory.write(conn, employee_id=emp["id"],
                                employee_entity=emp["legal_entity_id"],
                                employee_pack=pack, scope=req.scope, content=req.content)
        except memory.ScopeError as exc:
            raise HTTPException(400, detail={"code": "BadScope", "message": str(exc)})


@router.get("/memory")
def list_memory(scope: str | None = Query(default=None),
                _ident: dict = Depends(require("memory:read"))):
    with get_conn() as conn:
        entity = tenant.entity_of(_ident)
        pack = tenant.pack_of(conn, _ident)
        entries = memory.read(conn, entity=entity, pack=pack, scope=scope)
        return {"entity": entity, "pack": pack, "count": len(entries), "entries": entries}


@router.get("/memory/{memory_id}")
def get_memory(memory_id: str, _ident: dict = Depends(require("memory:read"))):
    with get_conn() as conn:
        entity = tenant.entity_of(_ident)
        pack = tenant.pack_of(conn, _ident)
        row = memory.get(conn, memory_id, entity=entity, pack=pack)
        if row is None:
            raise HTTPException(404, detail={"code": "MemoryNotFound", "id": memory_id})
        return row
