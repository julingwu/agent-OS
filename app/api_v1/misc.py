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
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, llm_config
from ..secretbox import decrypt, encrypt
from .stream import require_device

router = APIRouter(prefix="/v1", tags=["misc"])

DEFAULT_SETTINGS = {
    "theme": "cyber",
    "computer_fps": 2,
    "daily_tokens": 0,
    "model": "deepseek-flash",
    "view_only": True,
    "local_enabled": False,
    "local_approval": True,
    "memory_enabled": True,
    "local_allow_dirs": [],
    "llm_provider": "deepseek",
    "llm_base_url": "",
    "llm_api_key": "",
    "custom_models": [],
    "apply_model_globally": True,
    "search_api_key": "",
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
def _format_settings(stored: dict, conn=None) -> dict:
    merged = {**DEFAULT_SETTINGS, **stored}
    raw_key = str(merged.get("llm_api_key") or "").strip()
    masked = (raw_key[:3] + "..." + raw_key[-4:]) if len(raw_key) > 7 else ("***" if raw_key else "")
    has_key = bool(raw_key)
    # Never leak raw key to client
    merged["llm_api_key"] = ""
    merged["llm_api_key_masked"] = masked
    merged["llm_has_key"] = has_key
    raw_search = str(merged.get("search_api_key") or "").strip()
    masked_search = (raw_search[:3] + "..." + raw_search[-4:]) if len(raw_search) > 7 else ("***" if raw_search else "")
    has_search = bool(raw_search)
    merged["search_api_key"] = ""
    merged["search_api_key_masked"] = masked_search
    merged["search_has_key"] = has_search
    try:
        merged["usage_today_tokens"] = llm_config.get_today_token_usage(conn)
    except Exception:
        merged["usage_today_tokens"] = 0
    return merged


@router.get("/settings")
def get_settings(device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        llm_config.migrate_unlimited_default_if_needed(conn)
        rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
        stored = {r["key"]: json.loads(r["value_json"]) for r in rows}
        return {"values": _format_settings(stored, conn)}


class SettingsPatch(BaseModel):
    values: dict
    apply_model_globally: bool | None = None


@router.put("/settings")
def put_settings(body: SettingsPatch, device: dict = Depends(require_device)) -> dict:
    allowed = set(DEFAULT_SETTINGS)
    unknown = [k for k in body.values if k not in allowed]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown settings: {unknown}")

    apply_globally = body.apply_model_globally
    if apply_globally is None:
        apply_globally = bool(body.values.get("apply_model_globally", True))

    now = _now()
    with db.get_conn() as conn:
        llm_config.migrate_unlimited_default_if_needed(conn)
        for key, value in body.values.items():
            if key == "search_api_key":
                s_val = str(value or "").strip()
                if not s_val or "..." in s_val:
                    continue
            if key == "llm_api_key":
                s_val = str(value or "").strip()
                if not s_val or "..." in s_val:
                    continue
            conn.execute(
                "INSERT INTO app_settings (key, value_json, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,"
                " updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), now),
            )

        new_model = body.values.get("model")
        if new_model:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('default_model', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (new_model,),
            )
            if apply_globally:
                conn.execute("UPDATE bots SET model = NULL WHERE deleted_at IS NULL")

        new_base = body.values.get("llm_base_url")
        if new_base:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('default_api_base', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (new_base,),
            )

        llm_config.invalidate_cache()

        rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
        stored = {r["key"]: json.loads(r["value_json"]) for r in rows}
        return {"values": _format_settings(stored, conn)}


class TestLlmIn(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


@router.post("/settings/test-llm")
def test_llm_connection(body: TestLlmIn, device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        cfg = llm_config.resolve_llm_config(conn=conn)

    provider = body.provider or cfg.get("provider") or "deepseek"
    base_url = (body.base_url or "").strip()
    if not base_url:
        base_url = llm_config.PROVIDER_PRESETS.get(provider, {}).get("base_url") or cfg.get("base_url") or ""

    chat_url = llm_config.normalize_chat_completions_url(base_url)

    key = (body.api_key or "").strip()
    if not key or "..." in key:
        key = cfg.get("api_key") or ""

    if not key:
        return {"ok": False, "error": "API Key 不能为空"}

    model = (body.model or "").strip() or cfg.get("model") or "deepseek-chat"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 10,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    t0 = time.time()
    try:
        with httpx.Client(timeout=12.0, trust_env=False) as client:
            res = client.post(chat_url, json=payload, headers=headers)
        latency_ms = int((time.time() - t0) * 1000)
        if res.status_code == 200:
            return {"ok": True, "latency_ms": latency_ms, "model": model, "provider": provider}
        else:
            detail = res.text[:200]
            return {"ok": False, "error": f"HTTP {res.status_code}: {detail}", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = int((time.time() - t0) * 1000)
        return {"ok": False, "error": f"连接失败: {exc}", "latency_ms": latency_ms}


# ---------------------------------------------------------------- 模型
def available_models() -> list[str]:
    raw = os.environ.get("AVAILABLE_MODELS", "deepseek-flash,deepseek-v4-pro")
    return [m.strip() for m in raw.split(",") if m.strip()]


@router.get("/models")
def list_models(device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        cfg = llm_config.resolve_llm_config(conn=conn)
        row = conn.execute("SELECT value_json FROM app_settings WHERE key='model'").fetchone()
        custom_row = conn.execute("SELECT value_json FROM app_settings WHERE key='custom_models'").fetchone()
        custom_models = json.loads(custom_row["value_json"]) if custom_row else []

    default = json.loads(row["value_json"]) if row else cfg.get("model") or DEFAULT_SETTINGS["model"]

    models_seen = set()
    items = []

    # 1. available_models (preserves test_pairing_v1 expectations)
    for m in available_models():
        if m not in models_seen:
            models_seen.add(m)
            items.append({"id": m, "label": m})

    # 2. Provider presets
    provider = cfg.get("provider", "deepseek")
    preset_models = llm_config.PROVIDER_MODELS.get(provider, [])
    for m in preset_models:
        if m not in models_seen:
            models_seen.add(m)
            items.append({"id": m, "label": m})

    # 3. Custom models configured by user
    if isinstance(custom_models, list):
        for m in custom_models:
            m_str = str(m).strip()
            if m_str and m_str not in models_seen:
                models_seen.add(m_str)
                items.append({"id": m_str, "label": m_str})

    # 4. Currently configured default
    if default not in models_seen:
        models_seen.add(default)
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
