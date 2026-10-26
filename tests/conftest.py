import os

import pytest

from app.db import active_bg_threads, init_db, wait_for_bg_threads


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
    # v5.4.2 §79 测试生命周期屏障：用例结束先排空它派生的后台流水线线程，再让
    # tmp_path 被回收。否则线程会活到下一个用例，读到别人的 DB_PATH / 已删除的
    # 临时库（sqlite 还会静默新建 0 表空库 → no such table: task_runs）。
    drained = wait_for_bg_threads(timeout=10.0)
    assert drained, ("仍有后台线程未退出（会跨用例污染下一个库）："
                     + repr([t.name for t in active_bg_threads()]))
    assert "data/oa.db" not in os.path.abspath(os.getenv("DB_PATH", "")).replace("\\", "/")
