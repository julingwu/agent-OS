import json
import pathlib
import shutil
import uuid
import pytest

from app import db, events as ev
from app.agent import tools, loop

_TMP_ROOT = pathlib.Path(__file__).resolve().parents[1] / ".tmp-tests"


@pytest.fixture()
def conn(monkeypatch):
    root = _TMP_ROOT / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DB_PATH", str(root / "oa.db"))
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    monkeypatch.setenv("M0_WORKSPACE", str(workspace))
    monkeypatch.setenv("M0_SANDBOX", "none")

    # Update tools.WORKSPACE to point to the test fixture workspace
    old_workspace = tools.WORKSPACE
    tools.WORKSPACE = workspace

    db.init_db()
    c = db.connect()
    try:
        yield c
    finally:
        c.close()
        tools.WORKSPACE = old_workspace
        shutil.rmtree(root, ignore_errors=True)


def test_sanitize_tool_args_large_payload():
    """测试超大参数（如 20KB 文本传给 fs_write）经过 _sanitize_tool_args_for_event 后事件体积安全且保留前缀。"""
    large_content = "X" * 20000
    raw_args = {
        "path": "report.md",
        "content": large_content,
        "metadata": {
            "nested_code": "print('hello')\n" * 500
        }
    }
    sanitized = loop._sanitize_tool_args_for_event(raw_args)

    assert sanitized["path"] == "report.md"
    assert len(sanitized["content"]) < 1400
    assert sanitized["content"].startswith("X" * 100)
    assert "完整内容共 20000 字符，已送入工具安全执行" in sanitized["content"]
    assert "已送入工具安全执行" in sanitized["metadata"]["nested_code"]

    # 验证原始 args 未被污染
    assert len(raw_args["content"]) == 20000


def test_sanitize_tool_result():
    """测试超大工具输出经过 _sanitize_tool_result_for_event 后截断。"""
    large_result = {
        "ok": True,
        "stdout": "A" * 5000,
        "nested": ["B" * 3000]
    }
    sanitized = loop._sanitize_tool_result_for_event(large_result)
    assert len(sanitized["stdout"]) < 2200
    assert "已截断显示" in sanitized["stdout"]
    assert "已截断显示" in sanitized["nested"][0]


def test_events_emit_auto_truncates_oversize_payload(conn):
    """测试 events.emit 遇到超大 payload 时自动截断而不抛异常。"""
    large_text = "重要宏观策略分析" * 5000  # 约 120KB
    payload = {
        "text": large_text,
        "source": "analyst",
    }
    # MAX_PAYLOAD_BYTES 是 32KB，120KB 会触发截断
    event = ev.emit(
        conn,
        bot_id="bot_macro",
        session_id="ses_1",
        kind="message.bot",
        payload=payload,
    )
    conn.commit()

    assert event.id > 0
    assert event.payload.get("_payload_truncated") is True
    assert len(event.payload["text"]) <= 1100
    assert "...[已截断显示]" in event.payload["text"]


def test_events_emit_fallback_on_extreme_payload(conn):
    """测试无法通过字段截断缩减的超大 payload 降级为 minimal 字典。"""
    # 构造大量短字段，总大小超过 32KB
    huge_dict = {f"k_{i:04d}": "val" * 30 for i in range(500)}
    huge_dict["call_id"] = "call_123"
    huge_dict["tool"] = "fs_write"

    event = ev.emit(
        conn,
        bot_id="bot_test",
        session_id="ses_1",
        kind="tool.call",
        payload=huge_dict,
    )
    conn.commit()

    assert event.id > 0
    assert event.payload.get("_warning") == "payload too large"
    assert "keys" in event.payload
    # 保证 tool.call 必需字段依然存在
    assert event.payload.get("tool") == "fs_write"
    assert event.payload.get("call_id") == "call_123"


def test_path_aliases_tolerance(conn):
    """测试 fs_write / fs_read / fs_edit / doc_read 对 file, filename, filepath 等别名的容错支持。"""
    # 1. fs_write with 'file'
    res = tools.dispatch("fs_write", {"file": "alias_file.txt", "content": "content from file"})
    assert res.get("ok") is True

    # fs_read with 'file'
    r1 = tools.dispatch("fs_read", {"file": "alias_file.txt"})
    assert r1.get("ok") is True
    assert "content from file" in r1.get("content", "")

    # 2. fs_write with 'filename'
    res = tools.dispatch("fs_write", {"filename": "alias_filename.txt", "content": "content from filename"})
    assert res.get("ok") is True

    # fs_read with 'filename'
    r2 = tools.dispatch("fs_read", {"filename": "alias_filename.txt"})
    assert r2.get("ok") is True
    assert "content from filename" in r2.get("content", "")

    # 3. fs_write with 'filepath'
    res = tools.dispatch("fs_write", {"filepath": "alias_filepath.txt", "content": "content from filepath"})
    assert res.get("ok") is True

    # fs_read with 'filepath'
    r3 = tools.dispatch("fs_read", {"filepath": "alias_filepath.txt"})
    assert r3.get("ok") is True
    assert "content from filepath" in r3.get("content", "")

    # 4. fs_edit with 'filepath'
    r_edit = tools.dispatch("fs_edit", {"filepath": "alias_filepath.txt", "old_text": "content", "new_text": "updated"})
    assert r_edit.get("ok") is True
    r_read_after = tools.dispatch("fs_read", {"path": "alias_filepath.txt"})
    assert "updated from filepath" in r_read_after.get("content", "")

    # 5. doc_read with 'file'
    r_doc = tools.dispatch("doc_read", {"file": "alias_file.txt"})
    assert r_doc.get("ok") is True
    assert "content from file" in r_doc.get("content", "")


def test_path_empty_error_messages(conn):
    """测试 path 为空时返回友好的报错信息。"""
    res_w = tools.dispatch("fs_write", {"content": "no path"})
    assert res_w.get("ok") is False
    assert "path (或 file/filename) 不能为空" in res_w.get("error", "")

    res_r = tools.dispatch("fs_read", {})
    assert res_r.get("ok") is False
    assert "path (或 file/filename) 不能为空" in res_r.get("error", "")

    res_e = tools.dispatch("fs_edit", {"old_text": "a", "new_text": "b"})
    assert res_e.get("ok") is False
    assert "path (或 file/filename) 不能为空" in res_e.get("error", "")

    res_d = tools.dispatch("doc_read", {})
    assert res_d.get("ok") is False
    assert "path (或 file/filename) 不能为空" in res_d.get("error", "")


def test_emit_catches_exception_safely(conn, monkeypatch):
    """测试 _emit 遇到异常时静默降级不崩溃。"""
    def broken_emit(*args, **kwargs):
        raise RuntimeError("Simulated database disk I/O failure")

    monkeypatch.setattr(ev, "emit", broken_emit)

    # 此时 _emit 不应该向上抛出异常，而是返回 None
    ret = loop._emit(conn, bot_id="bot_test", session_id="ses_1", kind="turn.started", payload={"input_event_id": 1})
    assert ret is None
