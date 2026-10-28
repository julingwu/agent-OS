"""应用连接 / 设置 / 保险库（v1 的最小实现）。

诚实边界：
  * 应用连接目前是"登记 + 开关"，真正 OAuth 接入在后续版本；
  * 设置是服务端 key-value，前端据此渲染；
  * 保险库只存密文，列表只回掩码，模型永远拿不到明文。

注意：legacy 已有一张 settings(key,value) 表，本模块用 app_settings 避让。
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..secretbox import decrypt, encrypt
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["misc"])

DEFAULT_SETTINGS = {
    "theme": "cyber",
    "computer_fps": 2,
    "daily_tokens": 500000,
    "model": "deepseek-flash",
    "view_only": True,
    "local_enabled": False,
    "local_approval": True,
    "memory_enabled": True,
    "local_allow_dirs": [],
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


# ---------------------------------------------------------------- 应用
@router.get("/apps")
def list_apps(device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM apps ORDER BY kind, name").fetchall()
    return {"items": [
        {"id": r["id"], "name": r["name"], "description": r["description"],
         "kind": r["kind"], "connected": bool(r["connected"]),
         "connected_at": r["connected_at"]}
        for r in rows
    ]}


class AppToggle(BaseModel):
    connected: bool = True


@router.post("/apps/{app_id}/connect")
def connect_app(app_id: str, body: AppToggle,
                device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="app not found")
        conn.execute("UPDATE apps SET connected=?, connected_at=? WHERE id=?",
                     (1 if body.connected else 0, _now() if body.connected else None, app_id))
        row = conn.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()
    return {"id": row["id"], "connected": bool(row["connected"])}


# ---------------------------------------------------------------- 设置
@router.get("/settings")
def get_settings(device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
    stored = {r["key"]: json.loads(r["value_json"]) for r in rows}
    return {"values": {**DEFAULT_SETTINGS, **stored}}


class SettingsPatch(BaseModel):
    values: dict


@router.put("/settings")
def put_settings(body: SettingsPatch, device: dict = Depends(require_device)) -> dict:
    allowed = set(DEFAULT_SETTINGS)
    unknown = [k for k in body.values if k not in allowed]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown settings: {unknown}")
    now = _now()
    with db.get_conn() as conn:
        for key, value in body.values.items():
            conn.execute(
                "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,"
                " updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), now),
            )
        rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
    return {"values": {**DEFAULT_SETTINGS, **{r["key"]: json.loads(r["value_json"]) for r in rows}}}


# ---------------------------------------------------------------- 模型
def available_models() -> list[str]:
    raw = os.environ.get("AVAILABLE_MODELS", "deepseek-flash,deepseek-v4-pro")
    return [m.strip() for m in raw.split(",") if m.strip()]


@router.get("/models")
def list_models(device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        row = conn.execute("SELECT value_json FROM app_settings WHERE key='model'").fetchone()
    default = json.loads(row["value_json"]) if row else DEFAULT_SETTINGS["model"]
    items = [{"id": m, "label": m} for m in available_models()]
    if default not in [i["id"] for i in items]:
        items.insert(0, {"id": default, "label": default})
    return {"default": default, "items": items}


# ---------------------------------------------------------------- 外部接入目录
@router.get("/integrations")
def list_integrations(device: dict = Depends(require_device)) -> dict:
    """外部接入目录：连接应用页/MCP 管理后续单独做。"""
    return {"items": []}


# ---------------------------------------------------------------- 保险库
def _mask(kind: str, fields: dict) -> dict:
    if kind == "login":
        user = str(fields.get("username") or "")
        return {"username": (user[:2] + "***") if user else "",
                "has_password": bool(fields.get("password"))}
    if kind == "card":
        num = str(fields.get("number") or "").replace(" ", "")
        return {"last4": num[-4:], "holder": fields.get("holder", ""),
                "has_cvv": bool(fields.get("cvv"))}
    if kind == "contact":
        phone = str(fields.get("phone") or "")
        return {"phone": (phone[:3] + "****" + phone[-2:]) if len(phone) >= 6 else "***",
                "email": fields.get("email", "")}
    return {}


def _masked_row(row) -> dict:
    fields = json.loads(decrypt(row["fields_enc"])) if row["fields_enc"] else {}
    return {"id": row["id"], "kind": row["kind"], "title": row["title"],
            "masked": _mask(row["kind"], fields), "created_at": row["created_at"]}


@router.get("/vault")
def list_vault(device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM vault_items ORDER BY created_at DESC").fetchall()
    return {"items": [_masked_row(r) for r in rows]}


class VaultIn(BaseModel):
    kind: str = Field(pattern="^(login|card|contact)$")
    title: str = Field(min_length=1, max_length=80)
    fields: dict


@router.post("/vault", status_code=201)
def add_vault(body: VaultIn, device: dict = Depends(require_device)) -> dict:
    item_id = "vlt_" + secrets.token_hex(8)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO vault_items (id, kind, title, fields_enc, created_at)"
            " VALUES (?,?,?,?,?)",
            (item_id, body.kind, body.title,
             encrypt(json.dumps(body.fields, ensure_ascii=False)), _now()),
        )
        row = conn.execute("SELECT * FROM vault_items WHERE id=?", (item_id,)).fetchone()
    return _masked_row(row)


@router.post("/vault/{item_id}/reveal")
def reveal_vault(item_id: str, device: dict = Depends(require_device)) -> dict:
    """人用接口：返回一次明文。模型侧没有对应工具，不会进对话上下文。"""
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM vault_items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="vault item not found")
    return {"id": row["id"], "kind": row["kind"], "title": row["title"],
            "fields": json.loads(decrypt(row["fields_enc"]))}


@router.delete("/vault/{item_id}")
def delete_vault(item_id: str, device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM vault_items WHERE id=?", (item_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="vault item not found")
    return {"deleted": item_id}
