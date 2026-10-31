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
from typing import Any

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
    "llm_providers": [],
    "active_provider_id": "",
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
    if "llm_providers" in merged and isinstance(merged["llm_providers"], list):
        safe_providers = []
        for p in merged["llm_providers"]:
            if isinstance(p, dict):
                p_copy = dict(p)
                p_raw_k = str(p_copy.get("api_key") or "")
                p_copy["api_key"] = llm_config.mask_key(p_raw_k)
                p_copy["api_key_masked"] = llm_config.mask_key(p_raw_k)
                p_copy["has_key"] = bool(p_raw_k.strip())
                safe_providers.append(p_copy)
            else:
                safe_providers.append(p)
        merged["llm_providers"] = safe_providers
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
                if not s_val or "..." in s_val or "****" in s_val:
                    continue
            if key == "llm_api_key":
                s_val = str(value or "").strip()
                if not s_val or "..." in s_val or "****" in s_val:
                    continue
            if key == "llm_providers" and isinstance(value, list):
                curr_row = conn.execute("SELECT value_json FROM app_settings WHERE key='llm_providers'").fetchone()
                existing_map = {}
                if curr_row:
                    try:
                        for ep in json.loads(curr_row["value_json"]):
                            if isinstance(ep, dict) and ep.get("id"):
                                existing_map[ep["id"]] = ep.get("api_key", "")
                    except Exception:
                        pass
                sanitized = []
                for p in value:
                    if isinstance(p, dict):
                        p_entry = dict(p)
                        in_k = str(p_entry.get("api_key") or "").strip()
                        if not in_k or "****" in in_k or "..." in in_k:
                            if p_entry.get("id") in existing_map:
                                p_entry["api_key"] = existing_map[p_entry["id"]]
                        sanitized.append(p_entry)
                    else:
                        sanitized.append(p)
                value = sanitized
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


# ---------------------------------------------------------------- 多提供商与远程模型探测 (DSH)
def _lookup_real_api_key(provider: str | None = None, base_url: str | None = None) -> str:
    """Find real unmasked API key from configured providers or app_settings."""
    try:
        with db.get_conn() as conn:
            row = conn.execute("SELECT value_json FROM app_settings WHERE key='llm_providers'").fetchone()
            if row:
                try:
                    providers = json.loads(row["value_json"])
                    for p in providers:
                        if not isinstance(p, dict):
                            continue
                        p_key = str(p.get("api_key") or "").strip()
                        if not p_key or "****" in p_key or "..." in p_key:
                            continue
                        if provider and (p.get("id") == provider or p.get("preset_id") == provider or p.get("kind") == provider):
                            return p_key
                        if base_url and p.get("base_url") and p.get("base_url").rstrip("/") == base_url.rstrip("/"):
                            return p_key
                except Exception:
                    pass
            row_key = conn.execute("SELECT value_json FROM app_settings WHERE key='llm_api_key'").fetchone()
            if row_key:
                try:
                    k = str(json.loads(row_key["value_json"]) or "").strip()
                    if k and "****" not in k and "..." not in k:
                        return k
                except Exception:
                    pass
    except Exception:
        pass
    return ""


def _sync_active_provider_dual_write(conn, provider: dict) -> None:
    """Dual-write active provider config to app_settings and settings for 100% backward compatibility."""
    now = _now()
    p_id = str(provider.get("id") or "")
    p_kind = str(provider.get("preset_id") or provider.get("kind") or "deepseek")
    base_url = str(provider.get("base_url") or "").strip()
    api_key = str(provider.get("api_key") or "").strip()
    model = str(provider.get("model") or "").strip()

    # 1. Update app_settings
    conn.execute(
        "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('active_provider_id', ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
        (json.dumps(p_id, ensure_ascii=False), now),
    )
    conn.execute(
        "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('llm_provider', ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
        (json.dumps(p_kind, ensure_ascii=False), now),
    )
    conn.execute(
        "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('llm_base_url', ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
        (json.dumps(base_url, ensure_ascii=False), now),
    )
    if api_key and "****" not in api_key and "..." not in api_key:
        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('llm_api_key', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (json.dumps(api_key, ensure_ascii=False), now),
        )
    if model:
        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('model', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (json.dumps(model, ensure_ascii=False), now),
        )

    # 2. Update legacy settings table
    if model:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('default_model', ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (model,),
        )
    if base_url:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('default_api_base', ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (base_url,),
        )


def _get_or_init_providers(conn) -> tuple[list[dict], str]:
    """Retrieve all providers from app_settings, initializing from existing settings if empty."""
    llm_config.migrate_unlimited_default_if_needed(conn)
    row = conn.execute("SELECT value_json FROM app_settings WHERE key='llm_providers'").fetchone()
    active_row = conn.execute("SELECT value_json FROM app_settings WHERE key='active_provider_id'").fetchone()

    providers: list[dict] = []
    if row:
        try:
            providers = json.loads(row["value_json"])
        except Exception:
            providers = []

    active_provider_id = ""
    if active_row:
        try:
            active_provider_id = json.loads(active_row["value_json"])
        except Exception:
            active_provider_id = ""

    if not providers:
        rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
        stored = {r["key"]: json.loads(r["value_json"]) for r in rows}
        preset_id = stored.get("llm_provider") or "deepseek"
        preset_info = llm_config.PROVIDER_PRESETS.get(preset_id, {})
        base_url = stored.get("llm_base_url") or preset_info.get("base_url", "https://api.deepseek.com")
        api_key = stored.get("llm_api_key") or ""
        model = stored.get("model") or (preset_info.get("models", ["deepseek-chat"])[0] if preset_info.get("models") else "deepseek-chat")
        models = list(preset_info.get("models", []))
        if model and model not in models:
            models.append(model)
        name = preset_info.get("label") or preset_id.title()
        now = _now()
        initial_id = "prv_default"
        initial_provider = {
            "id": initial_id,
            "name": name,
            "kind": preset_id,
            "preset_id": preset_id,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "models": [llm_config.normalize_model_item(m) for m in models],
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        providers = [initial_provider]
        active_provider_id = initial_id
        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('llm_providers', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (json.dumps(providers, ensure_ascii=False), now),
        )
        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('active_provider_id', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (json.dumps(active_provider_id, ensure_ascii=False), now),
        )
        conn.commit()
        llm_config.invalidate_cache()

    return providers, active_provider_id


def normalize_base_url_for_models(raw_url: str) -> str:
    """Normalize any Base URL for model fetching by stripping chat endpoints."""
    if not raw_url or not raw_url.strip():
        return ""
    url = raw_url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[:-len("/chat/completions")].rstrip("/")
    elif url.endswith("/chat"):
        url = url[:-len("/chat")].rstrip("/")
    return url


class FetchModelsIn(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    provider: str | None = None


@router.post("/settings/fetch-models")
def fetch_models(body: FetchModelsIn, device: dict = Depends(require_device)) -> dict:
    provider = (body.provider or "").strip().lower()
    raw_base = (body.base_url or "").strip()
    if not raw_base and provider in llm_config.PROVIDER_PRESETS:
        raw_base = llm_config.PROVIDER_PRESETS[provider].get("base_url", "")

    base_url = normalize_base_url_for_models(raw_base)
    if not base_url:
        return {"ok": False, "error": "base_url 不能为空", "models": [], "items": []}

    api_key = (body.api_key or "").strip()
    if not api_key or "****" in api_key or "..." in api_key:
        api_key = _lookup_real_api_key(provider, base_url)

    is_ollama = (provider == "ollama") or (":11434" in base_url)
    if is_ollama:
        root_url = base_url[:-3].rstrip("/") if base_url.endswith("/v1") else base_url
        endpoint = f"{root_url}/api/tags"
    else:
        endpoint = f"{base_url}/models"

    headers = {"Accept": "application/json"}
    is_anthropic = (provider == "anthropic") or ("anthropic.com" in base_url)
    if is_anthropic:
        if api_key:
            headers["x-api-key"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"
        headers["anthropic-version"] = "2023-06-01"
    else:
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    t0 = time.time()
    try:
        with httpx.Client(timeout=10.0, trust_env=False) as client:
            res = client.get(endpoint, headers=headers)
        latency_ms = int((time.time() - t0) * 1000)

        if res.status_code != 200:
            if is_ollama and res.status_code in (404, 405):
                try:
                    alt_endpoint = f"{base_url}/models"
                    with httpx.Client(timeout=10.0, trust_env=False) as client:
                        alt_res = client.get(alt_endpoint, headers=headers)
                    if alt_res.status_code == 200:
                        res = alt_res
                except Exception:
                    pass

        if res.status_code != 200:
            return {
                "ok": False,
                "error": f"HTTP {res.status_code}: {res.text[:200]}",
                "models": [],
                "items": [],
                "latency_ms": latency_ms,
            }

        data = res.json()
        model_ids: list[str] = []
        if isinstance(data, dict):
            if "data" in data and isinstance(data["data"], list):
                for item in data["data"]:
                    if isinstance(item, dict) and "id" in item:
                        model_ids.append(str(item["id"]).strip())
                    elif isinstance(item, str):
                        model_ids.append(item.strip())
            elif "models" in data and isinstance(data["models"], list):
                for item in data["models"]:
                    if isinstance(item, dict):
                        m_id = item.get("name") or item.get("model") or item.get("id")
                        if m_id:
                            model_ids.append(str(m_id).strip())
                    elif isinstance(item, str):
                        model_ids.append(item.strip())
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "id" in item:
                    model_ids.append(str(item["id"]).strip())
                elif isinstance(item, str):
                    model_ids.append(item.strip())

        cleaned_models = sorted(list({m for m in model_ids if m}))
        items = [llm_config.normalize_model_item(m) for m in cleaned_models]
        for it in items:
            it["label"] = it["name"]
        return {
            "ok": True,
            "models": cleaned_models,
            "items": items,
            "count": len(cleaned_models),
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "ok": False,
            "error": str(exc),
            "models": [],
            "items": [],
            "latency_ms": latency_ms,
        }


class ProviderSaveIn(BaseModel):
    id: str | None = None
    name: str | None = None
    kind: str | None = None
    preset_id: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    models: list[Any] | None = None
    is_active: bool | None = None


@router.get("/settings/providers")
def list_providers(device: dict = Depends(require_device)) -> dict:
    with db.get_conn() as conn:
        providers, active_id = _get_or_init_providers(conn)

    masked_providers = []
    for p in providers:
        p_copy = dict(p)
        raw_k = str(p_copy.get("api_key") or "")
        p_copy["api_key"] = llm_config.mask_key(raw_k)
        p_copy["api_key_masked"] = llm_config.mask_key(raw_k)
        p_copy["has_key"] = bool(raw_k.strip())
        masked_providers.append(p_copy)

    return {
        "providers": masked_providers,
        "items": masked_providers,
        "active_provider_id": active_id,
    }


@router.post("/settings/providers")
def save_provider(body: ProviderSaveIn, device: dict = Depends(require_device)) -> dict:
    now = _now()
    with db.get_conn() as conn:
        providers, active_id = _get_or_init_providers(conn)

        target_provider = None
        if body.id:
            for p in providers:
                if p.get("id") == body.id:
                    target_provider = p
                    break

        if target_provider is not None:
            # Update existing
            if body.name is not None:
                target_provider["name"] = body.name
            if body.kind is not None:
                target_provider["kind"] = body.kind
            if body.preset_id is not None:
                target_provider["preset_id"] = body.preset_id
            if body.base_url is not None:
                target_provider["base_url"] = body.base_url.strip()
            if body.model is not None:
                target_provider["model"] = body.model.strip()
            if body.models is not None:
                target_provider["models"] = [llm_config.normalize_model_item(m) for m in body.models]

            in_key = (body.api_key or "").strip()
            if in_key and "****" not in in_key and "..." not in in_key:
                target_provider["api_key"] = in_key

            target_provider["updated_at"] = now
            if body.is_active is not None:
                target_provider["is_active"] = bool(body.is_active)
        else:
            # Create new provider
            new_id = body.id or f"prv_{secrets.token_hex(6)}"
            preset = body.preset_id or body.kind or "custom"
            preset_cfg = llm_config.PROVIDER_PRESETS.get(preset, {})
            name = body.name or preset_cfg.get("label") or preset.title()
            base_url = (body.base_url if body.base_url is not None else preset_cfg.get("base_url", "")).strip()
            in_key = (body.api_key or "").strip()
            key_val = in_key if ("****" not in in_key and "..." not in in_key) else ""

            raw_models = list(body.models) if body.models is not None else list(preset_cfg.get("models", []))
            models = [llm_config.normalize_model_item(m) for m in raw_models]
            model = (body.model or (models[0]["id"] if models else "deepseek-chat")).strip()
            model_ids = {m["id"] for m in models}
            if model and model not in model_ids:
                models.insert(0, llm_config.normalize_model_item(model))

            target_provider = {
                "id": new_id,
                "name": name,
                "kind": body.kind or preset,
                "preset_id": body.preset_id or preset,
                "base_url": base_url,
                "api_key": key_val,
                "model": model,
                "models": models,
                "is_active": bool(body.is_active) if body.is_active is not None else False,
                "created_at": now,
                "updated_at": now,
            }
            providers.append(target_provider)

        # Activation logic
        should_activate = False
        if body.is_active is True:
            should_activate = True
        elif not any(p.get("is_active") for p in providers):
            should_activate = True
        elif target_provider.get("is_active"):
            should_activate = True

        if should_activate:
            for p in providers:
                p["is_active"] = (p.get("id") == target_provider["id"])
            active_id = target_provider["id"]
            _sync_active_provider_dual_write(conn, target_provider)

        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('llm_providers', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (json.dumps(providers, ensure_ascii=False), now),
        )
        conn.commit()
        llm_config.invalidate_cache()

    p_copy = dict(target_provider)
    raw_k = str(p_copy.get("api_key") or "")
    p_copy["api_key"] = llm_config.mask_key(raw_k)
    p_copy["api_key_masked"] = llm_config.mask_key(raw_k)
    p_copy["has_key"] = bool(raw_k.strip())

    return {
        "ok": True,
        "provider": p_copy,
        "active_provider_id": active_id,
    }


@router.delete("/settings/providers/{provider_id}")
def delete_provider(provider_id: str, device: dict = Depends(require_device)) -> dict:
    now = _now()
    with db.get_conn() as conn:
        providers, active_id = _get_or_init_providers(conn)
        idx_to_del = -1
        for idx, p in enumerate(providers):
            if p.get("id") == provider_id:
                idx_to_del = idx
                break

        if idx_to_del == -1:
            raise HTTPException(status_code=404, detail="Provider not found")

        providers.pop(idx_to_del)

        if active_id == provider_id:
            if providers:
                providers[0]["is_active"] = True
                active_id = providers[0]["id"]
                _sync_active_provider_dual_write(conn, providers[0])
            else:
                active_id = ""
                conn.execute(
                    "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('active_provider_id', ?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (json.dumps("", ensure_ascii=False), now),
                )

        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('llm_providers', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (json.dumps(providers, ensure_ascii=False), now),
        )
        conn.commit()
        llm_config.invalidate_cache()

    return {"ok": True, "deleted": provider_id, "active_provider_id": active_id}


@router.post("/settings/providers/{provider_id}/activate")
def activate_provider(provider_id: str, device: dict = Depends(require_device)) -> dict:
    now = _now()
    with db.get_conn() as conn:
        providers, _ = _get_or_init_providers(conn)
        target = None
        for p in providers:
            if p.get("id") == provider_id:
                target = p
                break

        if target is None:
            raise HTTPException(status_code=404, detail="Provider not found")

        for p in providers:
            p["is_active"] = (p.get("id") == provider_id)

        _sync_active_provider_dual_write(conn, target)

        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('llm_providers', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (json.dumps(providers, ensure_ascii=False), now),
        )
        conn.commit()
        llm_config.invalidate_cache()

    p_copy = dict(target)
    raw_k = str(p_copy.get("api_key") or "")
    p_copy["api_key"] = llm_config.mask_key(raw_k)
    p_copy["api_key_masked"] = llm_config.mask_key(raw_k)
    p_copy["has_key"] = bool(raw_k.strip())

    return {
        "ok": True,
        "active_provider_id": provider_id,
        "provider": p_copy,
    }


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
    if not key or "..." in key or "****" in key:
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
        prv_row = conn.execute("SELECT value_json FROM app_settings WHERE key='llm_providers'").fetchone()
        providers = json.loads(prv_row["value_json"]) if prv_row else []
        active_row = conn.execute("SELECT value_json FROM app_settings WHERE key='active_provider_id'").fetchone()
        active_provider_id = json.loads(active_row["value_json"]) if active_row else ""

    default = json.loads(row["value_json"]) if row else cfg.get("model") or DEFAULT_SETTINGS["model"]

    # 识别当前激活的提供商及其显式配置的模型列表
    active_p = None
    if isinstance(providers, list):
        for p in providers:
            if isinstance(p, dict):
                if active_provider_id and p.get("id") == active_provider_id:
                    active_p = p
                    break
                elif p.get("is_active"):
                    active_p = p
                    break
        if not active_p and providers and isinstance(providers[0], dict):
            active_p = providers[0]

    has_active_configured_models = bool(
        active_p
        and isinstance(active_p.get("models"), list)
        and len(active_p.get("models")) > 0
    )

    models_seen = set()
    raw_list: list[str | dict] = []

    # 1. 优先使用激活提供商中用户显式配置的模型列表
    if has_active_configured_models:
        p_models = active_p.get("models")
        if isinstance(p_models, list):
            for m in p_models:
                if isinstance(m, dict):
                    m_id = str(m.get("id") or m.get("name") or "").strip()
                    if m_id and m_id not in models_seen:
                        models_seen.add(m_id)
                        raw_list.append(m)
                else:
                    m_str = str(m).strip()
                    if m_str and m_str not in models_seen:
                        models_seen.add(m_str)
                        raw_list.append(m_str)
        p_model = str(active_p.get("model") or "").strip()
        if p_model and p_model not in models_seen:
            models_seen.add(p_model)
            raw_list.append(p_model)
    else:
        # 未显式配置模型列表时，才注入基础 available_models 与该 provider 预设
        for m in available_models():
            m_str = str(m).strip()
            if m_str and m_str not in models_seen:
                models_seen.add(m_str)
                raw_list.append(m_str)

        provider = cfg.get("provider", "deepseek")
        preset_models = llm_config.PROVIDER_MODELS.get(provider, [])
        for m in preset_models:
            m_str = str(m).strip()
            if m_str and m_str not in models_seen:
                models_seen.add(m_str)
                raw_list.append(m_str)

    # 2. 加入其余已配置提供商的模型（供聚合或跨提供商调用）
    if isinstance(providers, list):
        for p in providers:
            if not isinstance(p, dict):
                continue
            if p is active_p or (isinstance(p, dict) and active_p and p.get("id") == active_p.get("id")):
                continue
            # 排除非激活的默认占位提供商以及同预设的不活动提供商
            if p.get("id") == "prv_default" and not p.get("is_active"):
                continue
            if active_p and (p.get("preset_id") == active_p.get("preset_id") or p.get("kind") == active_p.get("kind")):
                continue
            other_models = p.get("models")
            if isinstance(other_models, list):
                for m in other_models:
                    if isinstance(m, dict):
                        m_id = str(m.get("id") or m.get("name") or "").strip()
                        if m_id and m_id not in models_seen:
                            models_seen.add(m_id)
                            raw_list.append(m)
                    else:
                        m_str = str(m).strip()
                        if m_str and m_str not in models_seen:
                            models_seen.add(m_str)
                            raw_list.append(m_str)
            p_model = str(p.get("model") or "").strip()
            if p_model and p_model not in models_seen:
                models_seen.add(p_model)
                raw_list.append(p_model)

    # 4. Custom models configured by user
    if isinstance(custom_models, list):
        for m in custom_models:
            if isinstance(m, dict):
                m_id = str(m.get("id") or m.get("name") or "").strip()
                if m_id and m_id not in models_seen:
                    models_seen.add(m_id)
                    raw_list.append(m)
            else:
                m_str = str(m).strip()
                if m_str and m_str not in models_seen:
                    models_seen.add(m_str)
                    raw_list.append(m_str)

    # 5. Currently configured default
    if default and default not in models_seen:
        models_seen.add(default)
        raw_list.insert(0, default)

    items = []
    for item in raw_list:
        norm = llm_config.normalize_model_item(item)
        norm["label"] = norm["name"]
        items.append(norm)

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
