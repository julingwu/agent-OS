"""Unit and integration tests for Global LLM Configuration and Global Token Ledger."""

from __future__ import annotations

import json
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
        return devices.issue(conn, name="test-global-llm", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


def test_url_normalizer():
    assert llm_config.normalize_chat_completions_url("https://api.deepseek.com") == "https://api.deepseek.com/chat/completions"
    assert llm_config.normalize_chat_completions_url("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
    assert llm_config.normalize_chat_completions_url("https://api.openai.com/v1/") == "https://api.openai.com/v1/chat/completions"
    assert llm_config.normalize_chat_completions_url("https://api.openai.com/v1/chat/completions") == "https://api.openai.com/v1/chat/completions"
    assert llm_config.normalize_chat_completions_url("http://127.0.0.1:8999/v1/chat/completions") == "http://127.0.0.1:8999/v1/chat/completions"


def test_token_ledger_and_unlimited_gate():
    with db.get_conn() as conn:
        llm_config.init_token_ledger(conn)
        # 1. Default should be unlimited (0)
        allowed, used, limit = llm_config.check_global_daily_token_gate(conn)
        assert allowed is True
        assert limit == 0

        # 2. Record tokens
        llm_config.record_token_usage(120, 80, conn=conn)
        today_used = llm_config.get_today_token_usage(conn)
        assert today_used >= 200

        # 3. Configure a custom daily limit of 500
        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('daily_tokens', '500', CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json"
        )
        llm_config.invalidate_cache()

        # Under limit: allowed
        allowed, used, limit = llm_config.check_global_daily_token_gate(conn)
        assert allowed is True
        assert limit == 500

        # Over limit: record 400 more tokens -> total 600 > 500
        llm_config.record_token_usage(200, 200, conn=conn)
        allowed, used, limit = llm_config.check_global_daily_token_gate(conn)
        assert allowed is False
        assert used >= 600
        assert limit == 500


def test_migrate_unlimited_default():
    with db.get_conn() as conn:
        # Simulate legacy DB with 500,000
        conn.execute(
            "INSERT INTO app_settings (key, value_json, updated_at) VALUES ('daily_tokens', '500000', CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json"
        )
        # Clear migration flag if present
        conn.execute("DELETE FROM app_settings WHERE key='daily_tokens_migrated_to_unlimited'")
        llm_config.invalidate_cache()

        migrated = llm_config.migrate_unlimited_default_if_needed(conn)
        assert migrated is True

        row = conn.execute("SELECT value_json FROM app_settings WHERE key='daily_tokens'").fetchone()
        assert json.loads(row["value_json"]) == 0

        # Second call does nothing
        assert llm_config.migrate_unlimited_default_if_needed(conn) is False


def test_put_settings_and_masking(client, device):
    # 1. Save settings with key and custom URL
    res = client.put(
        "/v1/settings",
        headers=_auth(device),
        json={
            "values": {
                "llm_provider": "openai",
                "llm_base_url": "https://api.openai.com/v1",
                "llm_api_key": "sk-1234567890abcdef",
                "model": "gpt-4o",
            }
        },
    )
    assert res.status_code == 200
    values = res.json()["values"]
    assert values["llm_provider"] == "openai"
    assert values["model"] == "gpt-4o"
    assert values["llm_api_key"] == ""  # Never returned raw
    assert values["llm_has_key"] is True
    assert values["llm_api_key_masked"].startswith("sk-")
    assert "1234567890abcdef" not in values["llm_api_key_masked"]

    # 2. GET /v1/settings confirms masking
    get_res = client.get("/v1/settings", headers=_auth(device))
    assert get_res.status_code == 200
    g_vals = get_res.json()["values"]
    assert g_vals["llm_api_key"] == ""
    assert g_vals["llm_has_key"] is True

    # 3. PUT /v1/settings with empty llm_api_key does not overwrite
    res2 = client.put(
        "/v1/settings",
        headers=_auth(device),
        json={"values": {"computer_fps": 3, "llm_api_key": ""}},
    )
    assert res2.status_code == 200
    assert res2.json()["values"]["llm_has_key"] is True

    # Verify backend resolves this key
    with db.get_conn() as conn:
        cfg = llm_config.resolve_llm_config(conn=conn)
        assert cfg["api_key"] == "sk-1234567890abcdef"
        assert cfg["model"] == "gpt-4o"


def test_apply_model_globally_and_patch_bot(client, device):
    # 1. Create a bot with an explicit override model
    b_res = client.post(
        "/v1/bots",
        headers=_auth(device),
        json={"name": "OverrideBot", "model": "override-model-v1"},
    )
    assert b_res.status_code in (200, 201)
    bot_id = b_res.json()["id"]
    assert b_res.json()["model"] == "override-model-v1"

    # 2. Update global settings with apply_model_globally=True
    put_res = client.put(
        "/v1/settings",
        headers=_auth(device),
        json={
            "values": {"model": "global-unified-model"},
            "apply_model_globally": True,
        },
    )
    assert put_res.status_code == 200

    # 3. Check that bot's model has been reset to None (tracking global)
    bot_check = client.get(f"/v1/bots/{bot_id}", headers=_auth(device)).json()
    assert bot_check["model"] is None

    # 4. Set bot model again
    client.patch(
        f"/v1/bots/{bot_id}",
        headers=_auth(device),
        json={"model": "another-custom-model"},
    )
    bot_check2 = client.get(f"/v1/bots/{bot_id}", headers=_auth(device)).json()
    assert bot_check2["model"] == "another-custom-model"

    # 5. Reset bot model via PATCH with null
    patch_res = client.patch(
        f"/v1/bots/{bot_id}",
        headers=_auth(device),
        json={"model": None},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["model"] is None

    bot_check3 = client.get(f"/v1/bots/{bot_id}", headers=_auth(device)).json()
    assert bot_check3["model"] is None


def test_create_bot_template_leaves_model_none(client, device):
    # Template bot creation must NOT pin template model
    with db.get_conn() as conn:
        from app.ui import roles
        roles.ensure(conn)

    res = client.post(
        "/v1/bots",
        headers=_auth(device),
        json={"name": "TemplateBot", "role_id": "chief"},
    )
    assert res.status_code in (200, 201)
    # Must follow global default
    assert res.json()["model"] is None


def test_test_llm_endpoint(client, device, monkeypatch):
    # Test connection endpoint
    import httpx

    class MockResponse:
        status_code = 200
        text = '{"choices": [{"message": {"content": "pong"}}]}'
        def json(self):
            return {"choices": [{"message": {"content": "pong"}}]}

    orig_post = httpx.Client.post

    def mock_post(self, url, *args, **kwargs):
        if "deepseek" in str(url) or "completions" in str(url):
            return MockResponse()
        return orig_post(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    res = client.post(
        "/v1/settings/test-llm",
        headers=_auth(device),
        json={
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-test",
            "model": "deepseek-chat",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "latency_ms" in data
