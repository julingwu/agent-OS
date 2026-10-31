"""Tests for multi-provider routing isolation and rich model capabilities."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, llm, llm_config


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    from app import devices
    with db.get_conn() as conn:
        return devices.issue(conn, name="test-routing-device", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


def test_provider_routing_isolation():
    """测试 1：多提供商路由隔离验证。
    
    配置 DeepSeek (active) 与 SCNet 两个提供商，
    传入 model="SCNet-Max" 时验证 resolve_llm_config 返回 SCNet 的 Base URL 和 Key，
    而不是默认激活的 DeepSeek 的 Base URL / Key。
    """
    with db.get_conn() as conn:
        llm_config.ensure_tables(conn)
        providers = [
            {
                "id": "prv_deepseek",
                "name": "DeepSeek 官方",
                "kind": "deepseek",
                "preset_id": "deepseek",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-deepseek-active-key-111",
                "model": "deepseek-chat",
                "models": [
                    {"id": "deepseek-chat", "name": "deepseek-chat"},
                    {"id": "deepseek-reasoner", "name": "deepseek-reasoner"},
                ],
                "is_active": True,
            },
            {
                "id": "prv_scnet",
                "name": "SCNet 并行计算网格",
                "kind": "custom",
                "preset_id": "custom",
                "base_url": "https://api.scnet.cn/v1",
                "api_key": "sk-scnet-secret-key-999",
                "model": "SCNet-Max",
                "models": [
                    {"id": "SCNet-Max", "name": "SCNet-Max"},
                    {"id": "SCNet-Lite", "name": "SCNet-Lite"},
                ],
                "is_active": False,
            },
        ]
        now = llm_config._now()
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) VALUES ('llm_providers', ?, ?)",
            (json.dumps(providers), now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) VALUES ('active_provider_id', ?, ?)",
            (json.dumps("prv_deepseek"), now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) VALUES ('llm_base_url', ?, ?)",
            (json.dumps("https://api.deepseek.com"), now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) VALUES ('llm_api_key', ?, ?)",
            (json.dumps("sk-deepseek-active-key-111"), now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) VALUES ('model', ?, ?)",
            (json.dumps("deepseek-chat"), now),
        )
        conn.commit()
        llm_config.invalidate_cache()

        # 1. 不传 model 或传 None：走激活的 DeepSeek
        cfg_default = llm_config.resolve_llm_config(conn=conn)
        assert cfg_default["base_url"] == "https://api.deepseek.com"
        assert cfg_default["api_key"] == "sk-deepseek-active-key-111"
        assert cfg_default["model"] == "deepseek-chat"

        # 2. 传入 SCNet-Max：应精确路由到 SCNet 的 URL 和 Key
        cfg_scnet = llm_config.resolve_llm_config(model="SCNet-Max", conn=conn)
        assert cfg_scnet["base_url"] == "https://api.scnet.cn/v1"
        assert cfg_scnet["chat_url"] == "https://api.scnet.cn/v1/chat/completions"
        assert cfg_scnet["api_key"] == "sk-scnet-secret-key-999"
        assert cfg_scnet["model"] == "SCNet-Max"

        # 3. 传入 SCNet-Lite：同样路由到 SCNet
        cfg_scnet_lite = llm_config.resolve_llm_config(model="SCNet-Lite", conn=conn)
        assert cfg_scnet_lite["base_url"] == "https://api.scnet.cn/v1"
        assert cfg_scnet_lite["api_key"] == "sk-scnet-secret-key-999"

        # 4. 传入未知模型：回退到激活提供商 (DeepSeek)
        cfg_unknown = llm_config.resolve_llm_config(model="nonexistent-model", conn=conn)
        assert cfg_unknown["base_url"] == "https://api.deepseek.com"
        assert cfg_unknown["api_key"] == "sk-deepseek-active-key-111"
        assert cfg_unknown["model"] == "nonexistent-model"


def test_model_capabilities_inference():
    """测试 2：模型能力自动推断测试。
    
    验证 SCNet-Max、deepseek-flash、deepseek-chat、deepseek-reasoner、deepseek-v4-pro、o3-mini、qwen-turbo 的思考能力与上下文推断。
    """
    # 1. SCNet-Max
    scnet_caps = llm_config.infer_model_capabilities("SCNet-Max")
    assert scnet_caps["context_window"] == "256K"
    assert scnet_caps["max_tokens"] == "32K"
    assert scnet_caps["supports_reasoning"] is True
    assert scnet_caps["reasoning_levels"] == ["none", "low", "medium", "high"]

    # 2. deepseek-flash: context_window == "1M", max_tokens == "32K"
    ds_caps = llm_config.infer_model_capabilities("deepseek-flash")
    assert ds_caps["context_window"] == "1M"
    assert ds_caps["max_tokens"] == "32K"
    assert ds_caps["supports_reasoning"] is False
    assert ds_caps["reasoning_levels"] == []

    # 3. deepseek-chat: context_window == "64K", supports_reasoning is False
    ds_chat_caps = llm_config.infer_model_capabilities("deepseek-chat")
    assert ds_chat_caps["context_window"] == "64K"
    assert ds_chat_caps["supports_reasoning"] is False
    assert ds_chat_caps["reasoning_levels"] == []

    # 4. deepseek-reasoner: context_window == "64K", supports_reasoning is True
    ds_reasoner_caps = llm_config.infer_model_capabilities("deepseek-reasoner")
    assert ds_reasoner_caps["context_window"] == "64K"
    assert ds_reasoner_caps["supports_reasoning"] is True
    assert ds_reasoner_caps["reasoning_levels"] == ["none", "standard", "max"]

    # 5. deepseek-v4-pro: context_window == "1M"
    ds_v4_caps = llm_config.infer_model_capabilities("deepseek-v4-pro")
    assert ds_v4_caps["context_window"] == "1M"
    assert ds_v4_caps["max_tokens"] == "32K"

    # 6. o3-mini
    o3_caps = llm_config.infer_model_capabilities("o3-mini")
    assert o3_caps["context_window"] == "200K"
    assert o3_caps["max_tokens"] == "100K"
    assert o3_caps["supports_reasoning"] is True
    assert o3_caps["reasoning_levels"] == ["low", "medium", "high"]

    # 7. qwen-turbo
    qwen_caps = llm_config.infer_model_capabilities("qwen-turbo")
    assert qwen_caps["context_window"] == "128K"
    assert qwen_caps["max_tokens"] == "8K"
    assert qwen_caps["supports_reasoning"] is False
    assert qwen_caps["reasoning_levels"] == []

    # 额外边界覆盖
    # 1m -> "1M"
    m1_caps = llm_config.infer_model_capabilities("claude-1m-preview")
    assert m1_caps["context_window"] == "1M"

    # 64k -> "64K"
    k64_caps = llm_config.infer_model_capabilities("custom-64k")
    assert k64_caps["max_tokens"] == "64K"

    # 规整函数测试
    norm_str = llm_config.normalize_model_item("SCNet-Max")
    assert norm_str["id"] == "SCNet-Max"
    assert norm_str["name"] == "SCNet-Max"
    assert norm_str["context_window"] == "256K"
    assert norm_str["supports_reasoning"] is True
    assert norm_str["reasoning_levels"] == ["none", "low", "medium", "high"]

    norm_dict = llm_config.normalize_model_item({
        "id": "my-custom-model",
        "name": "Custom Model Label",
        "context_window": "512K",
    })
    assert norm_dict["id"] == "my-custom-model"
    assert norm_dict["name"] == "Custom Model Label"
    assert norm_dict["context_window"] == "512K"

    # 规整函数对 1000K, 1M 的标准化支持
    norm_1000k = llm_config.normalize_model_item({
        "id": "test-1000k",
        "context_window": "1000K",
    })
    assert norm_1000k["context_window"] == "1M"

    norm_1m = llm_config.normalize_model_item({
        "id": "test-1m",
        "context_window": "1M",
    })
    assert norm_1m["context_window"] == "1M"


def test_llm_url_and_key_dispatch(monkeypatch):
    """测试 3：llm._url 和 llm._key 正确根据传入的 model 派发对应提供商的配置。"""
    with db.get_conn() as conn:
        llm_config.ensure_tables(conn)
        providers = [
            {
                "id": "prv_deepseek",
                "name": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-deepseek-key",
                "model": "deepseek-chat",
                "models": ["deepseek-chat"],
                "is_active": True,
            },
            {
                "id": "prv_scnet",
                "name": "SCNet",
                "base_url": "https://api.scnet.cn/v1",
                "api_key": "sk-scnet-key",
                "model": "SCNet-Max",
                "models": ["SCNet-Max"],
                "is_active": False,
            },
        ]
        now = llm_config._now()
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) VALUES ('llm_providers', ?, ?)",
            (json.dumps(providers), now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) VALUES ('active_provider_id', ?, ?)",
            (json.dumps("prv_deepseek"), now),
        )
        conn.commit()
        llm_config.invalidate_cache()

    # 1. 默认/未传 model
    assert llm._url() == "https://api.deepseek.com/chat/completions"
    assert llm._key() == "sk-deepseek-key"

    # 2. 传 SCNet-Max
    assert llm._url(model="SCNet-Max") == "https://api.scnet.cn/v1/chat/completions"
    assert llm._key(model="SCNet-Max") == "sk-scnet-key"

    # 3. get_model_reasoning_levels 验证
    levels_scnet = llm.get_model_reasoning_levels("SCNet-Max")
    assert levels_scnet == ["none", "low", "medium", "high"]

    levels_ds_reasoner = llm.get_model_reasoning_levels("deepseek-reasoner")
    assert levels_ds_reasoner == ["none", "standard", "max"]

    levels_ds_chat = llm.get_model_reasoning_levels("deepseek-chat")
    assert levels_ds_chat == []


def test_chat_and_chat_stream_provider_dispatch(monkeypatch):
    """测试 3.1：chat() 与 chat_stream() 针对特定 model 发送请求时正确派发对应提供商的 Base URL 和 Key。"""
    with db.get_conn() as conn:
        llm_config.ensure_tables(conn)
        providers = [
            {
                "id": "prv_deepseek",
                "name": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-deepseek-key-111",
                "model": "deepseek-chat",
                "models": ["deepseek-chat"],
                "is_active": True,
            },
            {
                "id": "prv_scnet",
                "name": "SCNet",
                "base_url": "https://api.scnet.cn/v1",
                "api_key": "sk-scnet-key-999",
                "model": "SCNet-Max",
                "models": ["SCNet-Max"],
                "is_active": False,
            },
        ]
        now = llm_config._now()
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) VALUES ('llm_providers', ?, ?)",
            (json.dumps(providers), now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value_json, updated_at) VALUES ('active_provider_id', ?, ?)",
            (json.dumps("prv_deepseek"), now),
        )
        conn.commit()
        llm_config.invalidate_cache()

    monkeypatch.setattr(llm, "is_offline", lambda: False)
    monkeypatch.setattr(llm_config, "check_global_daily_token_gate", lambda *a: (True, 0, 1000000))

    captured = []

    def mock_post(self, url, json=None, headers=None, **kwargs):
        captured.append({"url": str(url), "headers": dict(headers or {}), "json": dict(json or {})})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        return resp

    monkeypatch.setattr("httpx.Client.post", mock_post)

    messages = [{"role": "user", "content": "hi"}]

    # A. 针对 SCNet-Max 调用 chat()
    captured.clear()
    res_scnet = llm.chat(messages, model="SCNet-Max")
    assert res_scnet is not None
    assert captured[0]["url"] == "https://api.scnet.cn/v1/chat/completions"
    assert captured[0]["headers"]["Authorization"] == "Bearer sk-scnet-key-999"

    # B. 针对默认/无 model 调用 chat()
    captured.clear()
    res_default = llm.chat(messages)
    assert res_default is not None
    assert captured[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert captured[0]["headers"]["Authorization"] == "Bearer sk-deepseek-key-111"


def test_fetch_models_returns_rich_items(client, device):
    """测试 4：POST /v1/settings/fetch-models 返回富元数据 items。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "SCNet-Max"},
            {"id": "deepseek-reasoner"},
            {"id": "qwen-turbo"},
        ]
    }

    with patch.object(httpx.Client, "get", return_value=mock_resp):
        res = client.post(
            "/v1/settings/fetch-models",
            headers=_auth(device),
            json={
                "base_url": "https://api.scnet.cn/v1",
                "api_key": "sk-test",
                "provider": "custom",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["models"] == ["SCNet-Max", "deepseek-reasoner", "qwen-turbo"]
        assert "items" in data
        assert len(data["items"]) == 3

        items_by_id = {it["id"]: it for it in data["items"]}
        
        # SCNet-Max
        assert items_by_id["SCNet-Max"]["supports_reasoning"] is True
        assert items_by_id["SCNet-Max"]["context_window"] == "256K"
        assert items_by_id["SCNet-Max"]["max_tokens"] == "32K"
        assert items_by_id["SCNet-Max"]["reasoning_levels"] == ["none", "low", "medium", "high"]

        # deepseek-reasoner
        assert items_by_id["deepseek-reasoner"]["supports_reasoning"] is True
        assert items_by_id["deepseek-reasoner"]["reasoning_levels"] == ["none", "standard", "max"]

        # qwen-turbo
        assert items_by_id["qwen-turbo"]["supports_reasoning"] is False
        assert items_by_id["qwen-turbo"]["reasoning_levels"] == []


def test_get_models_returns_rich_items(client, device):
    """测试 5：GET /v1/models 返回富元数据 items。"""
    # 先配置一个带有 SCNet-Max 的提供商
    save_res = client.post(
        "/v1/settings/providers",
        headers=_auth(device),
        json={
            "id": "prv_scnet_meta",
            "name": "SCNet",
            "kind": "custom",
            "preset_id": "custom",
            "base_url": "https://api.scnet.cn/v1",
            "api_key": "sk-scnet-test",
            "model": "SCNet-Max",
            "models": ["SCNet-Max", "qwen-turbo"],
        },
    )
    assert save_res.status_code == 200

    res = client.get("/v1/models", headers=_auth(device))
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert len(data["items"]) > 0

    items_by_id = {it["id"]: it for it in data["items"]}
    assert "SCNet-Max" in items_by_id
    assert "qwen-turbo" in items_by_id

    scnet_item = items_by_id["SCNet-Max"]
    assert scnet_item["name"] == "SCNet-Max"
    assert scnet_item["context_window"] == "256K"
    assert scnet_item["max_tokens"] == "32K"
    assert scnet_item["supports_reasoning"] is True
    assert scnet_item["reasoning_levels"] == ["none", "low", "medium", "high"]

    qwen_item = items_by_id["qwen-turbo"]
    assert qwen_item["supports_reasoning"] is False
    assert qwen_item["reasoning_levels"] == []


def test_models_list_prioritizes_configured_models_and_suppresses_presets(client, device):
    """测试 6：GET /v1/models 当激活提供商有显式配置的 models 时，优先返回且不注入硬编码预设。"""
    # 更新激活的 DeepSeek 提供商，用户只显式配置了 ["deepseek-chat"]，而不是包含 preset 的 flash / reasoner
    res_save = client.post(
        "/v1/settings/providers",
        headers=_auth(device),
        json={
            "id": "prv_deepseek",
            "name": "DeepSeek Custom",
            "kind": "deepseek",
            "preset_id": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-custom-ds-key",
            "model": "deepseek-chat",
            "models": ["deepseek-chat"],
            "is_active": True,
        },
    )
    assert res_save.status_code == 200

    res = client.get("/v1/models", headers=_auth(device))
    assert res.status_code == 200
    data = res.json()
    model_ids = [it["id"] for it in data["items"]]

    # 显式配置的 deepseek-chat 必须存在且排在首位
    assert model_ids[0] == "deepseek-chat"
    # 硬编码预设里的 deepseek-reasoner 和 deepseek-flash 不应该被注入进来
    assert "deepseek-reasoner" not in model_ids
    assert "deepseek-flash" not in model_ids
