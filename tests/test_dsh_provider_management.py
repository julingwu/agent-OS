"""Unit and integration tests for DSH multi-provider management and remote model probing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, llm_config


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    from app import devices
    with db.get_conn() as conn:
        return devices.issue(conn, name="test-dsh-providers", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


def test_provider_presets_catalog():
    """Verify built-in PROVIDER_PRESETS catalog contains all required presets and model lists."""
    presets = llm_config.PROVIDER_PRESETS
    required = [
        "deepseek",
        "siliconflow",
        "moonshot",
        "zhipu",
        "dashscope",
        "openai",
        "anthropic",
        "openrouter",
        "groq",
        "minimax",
        "ollama",
        "custom",
    ]
    for key in required:
        assert key in presets, f"Missing preset: {key}"

    assert presets["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert "deepseek-chat" in presets["deepseek"]["models"]
    assert "deepseek-reasoner" in presets["deepseek"]["models"]
    assert "deepseek-flash" in presets["deepseek"]["models"]

    assert presets["siliconflow"]["base_url"] == "https://api.siliconflow.cn/v1"
    assert "deepseek-ai/DeepSeek-V3" in presets["siliconflow"]["models"]
    assert "deepseek-ai/DeepSeek-R1" in presets["siliconflow"]["models"]
    assert "Qwen/Qwen2.5-72B-Instruct" in presets["siliconflow"]["models"]

    assert presets["moonshot"]["base_url"] == "https://api.moonshot.cn/v1"
    assert "moonshot-v1-8k" in presets["moonshot"]["models"]
    assert "moonshot-v1-32k" in presets["moonshot"]["models"]
    assert "moonshot-v1-128k" in presets["moonshot"]["models"]

    assert presets["zhipu"]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert "glm-4-plus" in presets["zhipu"]["models"]
    assert "glm-4-flash" in presets["zhipu"]["models"]
    assert "glm-4-long" in presets["zhipu"]["models"]

    assert presets["dashscope"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert "qwen-plus" in presets["dashscope"]["models"]
    assert "qwen-max" in presets["dashscope"]["models"]
    assert "qwen-turbo" in presets["dashscope"]["models"]

    assert presets["openai"]["base_url"] == "https://api.openai.com/v1"
    assert "gpt-4o" in presets["openai"]["models"]
    assert "gpt-4o-mini" in presets["openai"]["models"]
    assert "o1" in presets["openai"]["models"]
    assert "o3-mini" in presets["openai"]["models"]

    assert presets["anthropic"]["base_url"] == "https://api.anthropic.com/v1"
    assert "claude-3-7-sonnet" in presets["anthropic"]["models"]
    assert "claude-3-5-sonnet" in presets["anthropic"]["models"]

    assert presets["openrouter"]["base_url"] == "https://openrouter.ai/api/v1"
    assert "deepseek/deepseek-r1" in presets["openrouter"]["models"]
    assert "anthropic/claude-3.7-sonnet" in presets["openrouter"]["models"]
    assert "openai/gpt-4o" in presets["openrouter"]["models"]

    assert presets["groq"]["base_url"] == "https://api.groq.com/openai/v1"
    assert "llama-3.3-70b-versatile" in presets["groq"]["models"]
    assert "deepseek-r1-distill-llama-70b" in presets["groq"]["models"]

    assert presets["minimax"]["base_url"] == "https://api.minimax.chat/v1"
    assert "abab6.5s-chat" in presets["minimax"]["models"]
    assert "MiniMax-Text-01" in presets["minimax"]["models"]

    assert presets["ollama"]["base_url"] == "http://localhost:11434/v1"
    assert "llama3.2" in presets["ollama"]["models"]
    assert "qwen2.5" in presets["ollama"]["models"]

    assert presets["custom"]["base_url"] == "https://gateway.example.com/v1"
    assert presets["custom"]["models"] == []


def test_fetch_models_openai_format(client, device):
    """Test remote model fetching with standard OpenAI format and URL normalization."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "gpt-4o", "object": "model"},
            {"id": "gpt-4o-mini", "object": "model"},
            {"id": "o1", "object": "model"},
        ]
    }

    with patch.object(httpx.Client, "get", return_value=mock_resp) as mock_get:
        # Base URL has /chat/completions attached; should normalize to base and probe /models
        res = client.post(
            "/v1/settings/fetch-models",
            headers=_auth(device),
            json={
                "base_url": "https://api.openai.com/v1/chat/completions",
                "api_key": "sk-test12345678",
                "provider": "openai",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["count"] == 3
        assert data["models"] == ["gpt-4o", "gpt-4o-mini", "o1"]
        assert "latency_ms" in data

        # Check call arguments
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.openai.com/v1/models"
        called_headers = mock_get.call_args[1]["headers"]
        assert called_headers["Authorization"] == "Bearer sk-test12345678"


def test_fetch_models_ollama_format(client, device):
    """Test remote model fetching for Ollama calling /api/tags."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "models": [
            {"name": "llama3.2:latest", "model": "llama3.2:latest"},
            {"name": "qwen2.5:latest", "model": "qwen2.5:latest"},
        ]
    }

    with patch.object(httpx.Client, "get", return_value=mock_resp) as mock_get:
        res = client.post(
            "/v1/settings/fetch-models",
            headers=_auth(device),
            json={
                "base_url": "http://localhost:11434/v1",
                "provider": "ollama",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["count"] == 2
        assert data["models"] == ["llama3.2:latest", "qwen2.5:latest"]

        called_url = mock_get.call_args[0][0]
        assert called_url == "http://localhost:11434/api/tags"


def test_fetch_models_anthropic_headers(client, device):
    """Test Anthropic header injection (x-api-key & anthropic-version)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "claude-3-7-sonnet-20250219"},
            {"id": "claude-3-5-sonnet-20241022"},
        ]
    }

    with patch.object(httpx.Client, "get", return_value=mock_resp) as mock_get:
        res = client.post(
            "/v1/settings/fetch-models",
            headers=_auth(device),
            json={
                "base_url": "https://api.anthropic.com/v1",
                "api_key": "sk-ant-test-9999",
                "provider": "anthropic",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["count"] == 2
        assert "claude-3-7-sonnet-20250219" in data["models"]

        called_headers = mock_get.call_args[1]["headers"]
        assert called_headers["x-api-key"] == "sk-ant-test-9999"
        assert called_headers["anthropic-version"] == "2023-06-01"


def test_fetch_models_error_tolerance(client, device):
    """Test that connection failures or HTTP errors never throw unhandled 500."""
    # 1. Connection error simulation
    with patch.object(httpx.Client, "get", side_effect=httpx.ConnectError("Failed to connect")):
        res = client.post(
            "/v1/settings/fetch-models",
            headers=_auth(device),
            json={
                "base_url": "http://127.0.0.1:54321",
                "api_key": "dummy",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is False
        assert "Failed to connect" in data["error"]
        assert data["models"] == []

    # 2. HTTP 401 Unauthorized simulation
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.text = "Incorrect API key provided"
    with patch.object(httpx.Client, "get", return_value=mock_401):
        res = client.post(
            "/v1/settings/fetch-models",
            headers=_auth(device),
            json={
                "base_url": "https://api.openai.com/v1",
                "api_key": "bad-key",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is False
        assert "HTTP 401" in data["error"]
        assert data["models"] == []

    # 3. Missing base URL and empty provider
    res = client.post(
        "/v1/settings/fetch-models",
        headers=_auth(device),
        json={"base_url": "", "api_key": ""},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert "base_url 不能为空" in data["error"]


def test_provider_management_crud_and_dual_write(client, device):
    """Test provider CRUD lifecycle, password masking, activation and dual-write backward compatibility."""
    # 1. GET providers when DB is fresh -> auto fallback to default provider
    with db.get_conn() as conn:
        conn.execute("DELETE FROM app_settings WHERE key IN ('llm_providers', 'active_provider_id')")
        llm_config.invalidate_cache()

    res = client.get("/v1/settings/providers", headers=_auth(device))
    assert res.status_code == 200
    data = res.json()
    assert "providers" in data
    assert len(data["providers"]) >= 1
    active_id = data["active_provider_id"]
    assert active_id

    # 2. Add a new SiliconFlow provider
    new_provider_payload = {
        "id": "prv_siliconflow_test",
        "name": "My SiliconFlow",
        "kind": "siliconflow",
        "preset_id": "siliconflow",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": "sk-sf-real-key-123456789",
        "model": "deepseek-ai/DeepSeek-V3",
        "models": [
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen2.5-72B-Instruct",
        ],
        "is_active": False,
    }
    save_res = client.post(
        "/v1/settings/providers",
        headers=_auth(device),
        json=new_provider_payload,
    )
    assert save_res.status_code == 200
    saved = save_res.json()
    assert saved["ok"] is True
    assert saved["provider"]["id"] == "prv_siliconflow_test"
    # Ensure api_key is masked in response
    assert saved["provider"]["api_key"].startswith("sk-")
    assert "****" in saved["provider"]["api_key"]
    assert "sk-sf-real-key-123456789" not in saved["provider"]["api_key"]

    # 3. Update provider without changing API key (send masked key)
    update_res = client.post(
        "/v1/settings/providers",
        headers=_auth(device),
        json={
            "id": "prv_siliconflow_test",
            "name": "My SiliconFlow Updated",
            "api_key": saved["provider"]["api_key"],  # masked key
            "model": "deepseek-ai/DeepSeek-R1",
        },
    )
    assert update_res.status_code == 200
    assert update_res.json()["provider"]["name"] == "My SiliconFlow Updated"
    assert update_res.json()["provider"]["model"] == "deepseek-ai/DeepSeek-R1"

    # Verify real unmasked key is retained in DB
    with db.get_conn() as conn:
        row = conn.execute("SELECT value_json FROM app_settings WHERE key='llm_providers'").fetchone()
        prvs = json.loads(row["value_json"])
        sf_prv = next(p for p in prvs if p["id"] == "prv_siliconflow_test")
        assert sf_prv["api_key"] == "sk-sf-real-key-123456789"

    # 4. Activate the SiliconFlow provider
    act_res = client.post(
        "/v1/settings/providers/prv_siliconflow_test/activate",
        headers=_auth(device),
    )
    assert act_res.status_code == 200
    assert act_res.json()["ok"] is True
    assert act_res.json()["active_provider_id"] == "prv_siliconflow_test"

    # 5. Verify Dual-Write Consistency & resolve_llm_config()
    with db.get_conn() as conn:
        # Check app_settings table
        rows = dict(conn.execute("SELECT key, value_json FROM app_settings").fetchall())
        assert json.loads(rows["llm_provider"]) == "siliconflow"
        assert json.loads(rows["llm_base_url"]) == "https://api.siliconflow.cn/v1"
        assert json.loads(rows["llm_api_key"]) == "sk-sf-real-key-123456789"
        assert json.loads(rows["model"]) == "deepseek-ai/DeepSeek-R1"

        # Check legacy settings table
        leg_rows = dict(conn.execute("SELECT key, value FROM settings").fetchall())
        assert leg_rows["default_model"] == "deepseek-ai/DeepSeek-R1"
        assert leg_rows["default_api_base"] == "https://api.siliconflow.cn/v1"

        # Check resolve_llm_config()
        cfg = llm_config.resolve_llm_config(conn=conn)
        assert cfg["api_key"] == "sk-sf-real-key-123456789"
        assert cfg["model"] == "deepseek-ai/DeepSeek-R1"
        assert cfg["base_url"] == "https://api.siliconflow.cn/v1"
        assert cfg["chat_url"] == "https://api.siliconflow.cn/v1/chat/completions"
        assert cfg["provider"] == "siliconflow"
        assert cfg["has_key"] is True

    # 6. Verify GET /v1/models aggregates provider models
    m_res = client.get("/v1/models", headers=_auth(device))
    assert m_res.status_code == 200
    model_ids = [item["id"] for item in m_res.json()["items"]]
    assert "deepseek-ai/DeepSeek-V3" in model_ids
    assert "deepseek-ai/DeepSeek-R1" in model_ids
    assert "Qwen/Qwen2.5-72B-Instruct" in model_ids

    # 7. Delete provider
    del_res = client.delete(
        "/v1/settings/providers/prv_siliconflow_test",
        headers=_auth(device),
    )
    assert del_res.status_code == 200
    assert del_res.json()["ok"] is True
    assert del_res.json()["deleted"] == "prv_siliconflow_test"

    # Deleting non-existent provider returns 404
    del_404 = client.delete(
        "/v1/settings/providers/non_existent_id",
        headers=_auth(device),
    )
    assert del_404.status_code == 404
