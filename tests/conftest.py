import os
import pathlib
import pytest
from app.db import init_db

@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path, monkeypatch):
    test_db = tmp_path / "test_oa.db"
    test_ws = tmp_path / "workspace"
    monkeypatch.setenv("DB_PATH", str(test_db))
    monkeypatch.setenv("M0_WORKSPACE", str(test_ws))
    monkeypatch.setenv("WECOM_MOCK", "1")
    monkeypatch.setenv("WECOM_AUTORUN", "0")
    monkeypatch.setenv("WECOM_WATCHER", "0")
    monkeypatch.setenv("WECOM_APPROVAL_POLLER", "0")
    monkeypatch.setenv("OCTO_RUNNER", "0")
    monkeypatch.setenv("OCTO_AUTORUN", "0")
    monkeypatch.setenv("UI_CRON", "0")
    init_db(str(test_db))
    assert "data/oa.db" not in os.path.abspath(os.getenv("DB_PATH", "")).replace("\\", "/")
    yield
    assert "data/oa.db" not in os.path.abspath(os.getenv("DB_PATH", "")).replace("\\", "/")
