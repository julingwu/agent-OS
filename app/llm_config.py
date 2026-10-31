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


# ---------------------------------------------------------------- Model Capabilities & Normalization
def infer_model_capabilities(model_id: str) -> dict[str, Any]:
    """推断模型的上下文窗口、最大输出与推理档位。

    - 上下文大小推断：包含 1m -> "1000K", 256k/deepseek/scnet -> "256K", 128k -> "128K", 32k -> "32K", 默认 "128K"
    - 最大输出推断：scnet/deepseek/r1/o1 -> "32K", 64k -> "64K", 默认 "8K"
    - 思考能力识别：包含 scnet, reasoner, r1, o1, o3, o4, thinking, qwq, deepseek 等 -> supports_reasoning = True
      如果是 o1/o3/o4 则 reasoning_levels = ["low", "medium", "high"]；
      如果是 claude-3-7 / claude thinking 则 reasoning_levels = ["none", "low", "medium", "high", "max"]；
      其余具备思考能力者为 ["none", "standard", "max"]；
      其他普通模型 -> supports_reasoning = False, reasoning_levels = []
    """
    name = (model_id or "").strip().lower()

    # 1. 上下文大小推断
    if "1m" in name:
        context_window = "1000K"
    elif "256k" in name or "deepseek" in name or "scnet" in name:
        context_window = "256K"
    elif "128k" in name:
        context_window = "128K"
    elif "32k" in name:
        context_window = "32K"
    else:
        context_window = "128K"

    # 2. 最大输出推断
    if "64k" in name:
        max_tokens = "64K"
    elif any(k in name for k in ("scnet", "deepseek", "r1", "o1")):
        max_tokens = "32K"
    else:
        max_tokens = "8K"

    # 3. 思考能力识别
    is_o_series = bool(
        re.search(r"\b(o1|o3|o4)\b", name)
        or any(name.startswith(p) for p in ("o1-", "o3-", "o4-", "o1", "o3", "o4"))
    )
    is_claude_thinking = (
        "claude-3-7" in name or "claude-3.7" in name or ("claude" in name and "thinking" in name)
    )

    if is_o_series:
        supports_reasoning = True
        reasoning_levels = ["low", "medium", "high"]
    elif is_claude_thinking:
        supports_reasoning = True
        reasoning_levels = ["none", "low", "medium", "high", "max"]
    elif any(k in name for k in ("scnet", "reasoner", "r1", "thinking", "qwq", "deepseek", "qwen-max")):
        supports_reasoning = True
        reasoning_levels = ["none", "standard", "max"]
    else:
        supports_reasoning = False
        reasoning_levels = []

    return {
        "context_window": context_window,
        "max_tokens": max_tokens,
        "supports_reasoning": supports_reasoning,
        "reasoning_levels": reasoning_levels,
    }


def normalize_model_item(item: str | dict[str, Any]) -> dict[str, Any]:
    """统一将模型规整为标准富元数据结构：
    {
        "id": str,
        "name": str,
        "context_window": str,
        "max_tokens": str,
        "supports_reasoning": bool,
        "reasoning_levels": list[str]
    }
    """
    if isinstance(item, str):
        m_id = item.strip()
        caps = infer_model_capabilities(m_id)
        return {
            "id": m_id,
            "name": m_id,
            "context_window": caps["context_window"],
            "max_tokens": caps["max_tokens"],
            "supports_reasoning": caps["supports_reasoning"],
            "reasoning_levels": list(caps["reasoning_levels"]),
        }
    elif isinstance(item, dict):
        m_id = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
        m_name = str(item.get("name") or item.get("label") or m_id).strip() or m_id
        caps = infer_model_capabilities(m_id)

        cw = str(item.get("context_window") or caps["context_window"])
        mt = str(item.get("max_tokens") or caps["max_tokens"])

        sr = item.get("supports_reasoning")
        if sr is None:
            sr = caps["supports_reasoning"]
        else:
            sr = bool(sr)

        rl = item.get("reasoning_levels")
        if rl is None or not isinstance(rl, list):
            rl = list(caps["reasoning_levels"])
        else:
            rl = list(rl)

        return {
            "id": m_id,
            "name": m_name,
            "context_window": cw,
            "max_tokens": mt,
            "supports_reasoning": sr,
            "reasoning_levels": rl,
        }
    else:
        m_id = str(item).strip()
        caps = infer_model_capabilities(m_id)
        return {
            "id": m_id,
            "name": m_id,
            "context_window": caps["context_window"],
            "max_tokens": caps["max_tokens"],
            "supports_reasoning": caps["supports_reasoning"],
            "reasoning_levels": list(caps["reasoning_levels"]),
        }


# ---------------------------------------------------------------- Core Resolver
def resolve_llm_config(model: str | None = None, conn=None) -> dict[str, Any]:
    """Resolve active LLM configuration for any caller.

    Resolution Priority:
      * When `model` is passed:
        - Search all providers in `llm_providers` for a matching model (in `models` list or `model` attribute).
        - If matched, directly route to that provider's `base_url` and `api_key`!
        - If not matched in any provider, fallback to active provider or global settings.
      * When `model` is not passed:
        - Use active provider or global settings.
    """
    settings = _get_app_settings(conn)
    providers = settings.get("llm_providers")

    # 1. Check if model matches a specific provider
    chosen_p = None
    target_model = (model or "").strip()
    if target_model and isinstance(providers, list):
        for p in providers:
            if not isinstance(p, dict):
                continue
            # Check provider default model
            p_model = str(p.get("model") or "").strip()
            if p_model == target_model or (p_model and p_model.lower() == target_model.lower()):
                chosen_p = p
                break
            # Check provider models list
            p_models = p.get("models")
            if isinstance(p_models, list):
                found = False
                for item in p_models:
                    if isinstance(item, str):
                        m_str = item.strip()
                        if m_str == target_model or (m_str and m_str.lower() == target_model.lower()):
                            found = True
                            break
                    elif isinstance(item, dict):
                        m_id = str(item.get("id") or item.get("name") or "").strip()
                        if m_id == target_model or (m_id and m_id.lower() == target_model.lower()):
                            found = True
                            break
                if found:
                    chosen_p = p
                    break

    if chosen_p is not None:
        provider_key = str(chosen_p.get("api_key") or "").strip()
        provider_base = str(chosen_p.get("base_url") or "").strip()
        provider_name = str(
            chosen_p.get("preset_id") or chosen_p.get("kind") or chosen_p.get("name") or "custom"
        ).strip()

        api_key = provider_key or os.environ.get("LLM_API_KEY", "").strip()
        base_url = provider_base or "https://api.deepseek.com"
        chat_url = normalize_chat_completions_url(base_url)

        return {
            "api_key": api_key,
            "chat_url": chat_url,
            "base_url": base_url,
            "model": target_model,
            "provider": provider_name,
            "has_key": bool(api_key),
        }

    # 2. Fallback to active provider
    active_p = None
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

    # API Key
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

    # Chat URL
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

    # Model
    setting_model = str(settings.get("model") or "").strip()
    if not setting_model and active_p:
        setting_model = str(active_p.get("model") or "").strip()

    resolved_model = (
        target_model
        or setting_model
        or os.environ.get("LLM_MODEL", "deepseek-chat").strip()
    )

    provider = str(
        settings.get("llm_provider")
        or (active_p.get("preset_id") or active_p.get("kind") if active_p else "")
        or "deepseek"
    ).strip()

    base_url = (
        stored_base
        or os.environ.get("MODEL_BASE_URL")
        or os.environ.get("LLM_API_BASE")
        or "https://api.deepseek.com"
    )

    return {
        "api_key": api_key,
        "chat_url": chat_url,
        "base_url": base_url,
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
