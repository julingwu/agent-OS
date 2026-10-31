"""Global LLM Config & Daily Token Ledger Hub.

Single Source of Truth for:
  * Global Model, Base URL, API Key, Provider Presets
  * Global Daily Token Usage Ledger & Unlimited (0) Default Gate
  * Safe resolution across FastAPI, Agent Loop, Octo Intent, Native Kernel, and Pytest.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Tuple

log = logging.getLogger("app.llm_config")

# ---------------------------------------------------------------- Provider Presets
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "label": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner", "deepseek-flash"],
    },
    "siliconflow": {
        "label": "硅基流动 (SiliconFlow)",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": [
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen2.5-72B-Instruct",
        ],
    },
    "moonshot": {
        "label": "Moonshot (月之暗面/Kimi)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    },
    "zhipu": {
        "label": "智谱 AI (GLM)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-flash", "glm-4-long"],
    },
    "dashscope": {
        "label": "阿里云百炼 (通义千问)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo"],
    },
    "openai": {
        "label": "OpenAI 官方",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-3-7-sonnet", "claude-3-5-sonnet"],
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            "deepseek/deepseek-r1",
            "anthropic/claude-3.7-sonnet",
            "openai/gpt-4o",
        ],
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b"],
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "models": ["abab6.5s-chat", "MiniMax-Text-01"],
    },
    "ollama": {
        "label": "Ollama (本地)",
        "base_url": "http://localhost:11434/v1",
        "models": ["llama3.2", "qwen2.5"],
    },
    "ark": {
        "label": "火山引擎方舟 (豆包/DeepSeek)",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-1-5-pro-32k-250115", "deepseek-r1-250120"],
    },
    "custom": {
        "label": "自定义 OpenAI 兼容接口",
        "base_url": "https://gateway.example.com/v1",
        "models": [],
    },
}

PROVIDER_MODELS: dict[str, list[str]] = {k: list(v.get("models", [])) for k, v in PROVIDER_PRESETS.items()}

# ---------------------------------------------------------------- URL Normalizer
def normalize_chat_completions_url(raw_url: str) -> str:
    """Normalize any user/provider Base URL into an exact /chat/completions endpoint."""
    if not raw_url or not raw_url.strip():
        return ""
    url = raw_url.strip().rstrip("/")
    # If already fully specified
    if url.endswith("/chat/completions"):
        return url
    # Handle mock test servers or endpoints ending with /chat
    if url.endswith("/chat"):
        return url

    # Standard OpenAI compatible versions
    version_suffixes = (
        "/v1",
        "/v1beta/openai",
        "/v3",
        "/v4",
        "/compatible-mode/v1",
    )
    for sfx in version_suffixes:
        if url.endswith(sfx):
            return f"{url}/chat/completions"

    # Specific well-known domains
    if "api.deepseek.com" in url and not url.endswith(("/v1", "/v1/chat/completions")):
        return f"{url}/chat/completions"

    # Generic domain without version path -> append /v1/chat/completions
    return f"{url}/v1/chat/completions"


# ---------------------------------------------------------------- Invalidation & Cache
_cache_version: int = 0
_cached_db_key: str = ""
_cached_settings: dict[str, Any] = {}


def invalidate_cache() -> None:
    """Invalidate in-memory LLM settings cache."""
    global _cache_version, _cached_settings, _cached_db_key
    _cache_version += 1
    _cached_settings = {}
    _cached_db_key = ""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def today_key() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def mask_key(key: str | None) -> str:
    if not key or not str(key).strip():
        return ""
    k = str(key).strip()
    if len(k) <= 8:
        return "sk-****"
    return f"{k[:3]}****{k[-4:]}"


# ---------------------------------------------------------------- DB Helpers
def ensure_tables(conn) -> None:
    """Ensure global_token_usage and app_settings tables exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS global_token_usage (
            day TEXT PRIMARY KEY,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def migrate_unlimited_default_if_needed(conn) -> bool:
    """Migrate legacy default 500,000 daily_tokens to 0 (Unlimited) exactly once."""
    try:
        ensure_tables(conn)
        row = conn.execute(
            "SELECT value_json FROM app_settings WHERE key='_migrated_unlimited_default_v551'"
        ).fetchone()
        if row is not None:
            return False

        migrated = False
        dt_row = conn.execute(
            "SELECT value_json FROM app_settings WHERE key='daily_tokens'"
        ).fetchone()
        if dt_row is not None:
            try:
                val = json.loads(dt_row["value_json"])
                if val == 500000:
                    now = _now()
                    conn.execute(
                        "UPDATE app_settings SET value_json=?, updated_at=? WHERE key='daily_tokens'",
                        (json.dumps(0), now),
                    )
                    migrated = True
            except Exception:
                pass

        now = _now()
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?)",
            ("_migrated_unlimited_default_v551", json.dumps(True), now),
        )
        conn.commit()
        invalidate_cache()
        return migrated
    except Exception as exc:
        log.warning("migrate_unlimited_default error: %s", exc)
        return False

init_token_ledger = ensure_tables


def _get_app_settings(conn=None) -> dict[str, Any]:
    global _cached_db_key, _cached_settings
    from . import db

    if conn is None:
        try:
            with db.get_conn() as c:
                return _get_app_settings(c)
        except Exception:
            return {}

    db_id = getattr(conn, "_db_path", str(id(conn)))
    if _cached_db_key == db_id and _cached_settings:
        return dict(_cached_settings)

    try:
        ensure_tables(conn)
        migrate_unlimited_default_if_needed(conn)
        rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
        settings = {}
        for r in rows:
            try:
                settings[r["key"]] = json.loads(r["value_json"])
            except Exception:
                pass
        _cached_db_key = db_id
        _cached_settings = settings
        return dict(settings)
    except Exception:
        return {}


# ---------------------------------------------------------------- Core Resolver
def resolve_llm_config(model: str | None = None, conn=None) -> dict[str, Any]:
    """Resolve active LLM configuration for any caller.

    Resolution Priority (100% Pytest Safe):
      * api_key: app_settings['llm_api_key'] -> DEEPSEEK_API_KEY -> OPENAI_API_KEY -> LLM_API_KEY
      * chat_url: normalized app_settings['llm_base_url'] (if non-empty) -> LLM_URL -> normalized MODEL_BASE_URL / LLM_API_BASE
      * model: explicit arg -> app_settings['model'] -> LLM_MODEL -> "deepseek-chat"
    """
    settings = _get_app_settings(conn)

    # Active provider fallback
    active_p = None
    providers = settings.get("llm_providers")
    if isinstance(providers, list):
        active_id = settings.get("active_provider_id")
        for p in providers:
            if isinstance(p, dict):
                if active_id and p.get("id") == active_id:
                    active_p = p
                    break
                elif p.get("is_active"):
                    active_p = p
                    break
        if not active_p and providers and isinstance(providers[0], dict):
            active_p = providers[0]

    # 1. API Key
    stored_key = str(settings.get("llm_api_key") or "").strip()
    if not stored_key and active_p:
        stored_key = str(active_p.get("api_key") or "").strip()

    api_key = (
        stored_key
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or ""
    ).strip()

    # 2. Chat URL
    stored_base = str(settings.get("llm_base_url") or "").strip()
    if not stored_base and active_p:
        stored_base = str(active_p.get("base_url") or "").strip()

    if stored_base:
        chat_url = normalize_chat_completions_url(stored_base)
    elif os.environ.get("LLM_URL"):
        chat_url = os.environ["LLM_URL"].strip()
    else:
        env_base = os.environ.get("MODEL_BASE_URL") or os.environ.get("LLM_API_BASE")
        if env_base:
            chat_url = normalize_chat_completions_url(env_base)
        else:
            chat_url = "https://api.deepseek.com/chat/completions"

    # 3. Model
    setting_model = str(settings.get("model") or "").strip()
    if not setting_model and active_p:
        setting_model = str(active_p.get("model") or "").strip()

    resolved_model = (
        (model or "").strip()
        or setting_model
        or os.environ.get("LLM_MODEL", "deepseek-chat").strip()
    )

    provider = str(
        settings.get("llm_provider")
        or (active_p.get("preset_id") or active_p.get("kind") if active_p else "")
        or "deepseek"
    ).strip()

    return {
        "api_key": api_key,
        "chat_url": chat_url,
        "base_url": stored_base or os.environ.get("MODEL_BASE_URL") or os.environ.get("LLM_API_BASE") or "https://api.deepseek.com",
        "model": resolved_model,
        "provider": provider,
        "has_key": bool(api_key),
    }


# ---------------------------------------------------------------- Token Ledger & Gate
def record_token_usage(prompt_tokens: int, completion_tokens: int, conn=None) -> int:
    """Record turn/call tokens into global_token_usage and return today's total."""
    p_tok = max(0, int(prompt_tokens or 0))
    c_tok = max(0, int(completion_tokens or 0))
    tot = p_tok + c_tok
    if tot <= 0:
        return get_today_token_usage(conn)

    from . import db

    if conn is None:
        with db.get_conn() as c:
            return record_token_usage(p_tok, c_tok, c)

    ensure_tables(conn)
    day = today_key()
    now = _now()
    conn.execute(
        """
        INSERT INTO global_token_usage (day, prompt_tokens, completion_tokens, total_tokens, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(day) DO UPDATE SET
            prompt_tokens = prompt_tokens + excluded.prompt_tokens,
            completion_tokens = completion_tokens + excluded.completion_tokens,
            total_tokens = total_tokens + excluded.total_tokens,
            updated_at = excluded.updated_at
        """,
        (day, p_tok, c_tok, tot, now),
    )
    conn.commit()

    row = conn.execute(
        "SELECT total_tokens FROM global_token_usage WHERE day=?", (day,)
    ).fetchone()
    return int(row["total_tokens"] if row else 0)


def get_today_token_usage(conn=None) -> int:
    """Read today's total tokens used across the entire system."""
    from . import db

    if conn is None:
        try:
            with db.get_conn() as c:
                return get_today_token_usage(c)
        except Exception:
            return 0

    ensure_tables(conn)
    row = conn.execute(
        "SELECT total_tokens FROM global_token_usage WHERE day=?", (today_key(),)
    ).fetchone()
    return int(row["total_tokens"]) if row else 0


def check_global_daily_token_gate(conn=None) -> Tuple[bool, int, int]:
    """Check if global daily token limit is exceeded.

    Returns:
      (allowed, used_today, daily_limit)
      daily_limit <= 0 means Unlimited (always allowed).
    """
    settings = _get_app_settings(conn)
    limit = int(settings.get("daily_tokens") if "daily_tokens" in settings else 0)
    used_today = get_today_token_usage(conn)

    if limit <= 0:
        return True, used_today, 0

    allowed = used_today < limit
    return allowed, used_today, limit
