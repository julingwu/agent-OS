"""Tests for multi-step browser interaction and visual snapshot execution engine."""
from __future__ import annotations

import os
import tempfile
import urllib.parse
from unittest.mock import MagicMock, patch

import pytest

from app.agent import browser_tools, tools, web_tools
from app.agent.browser_tools import browser_interact


# ---------------------------------------------------------------- 1. URL & Parameter Validation
def test_browser_interact_empty_url():
    """Verify empty or whitespace URL returns ok=False with error message without crashing."""
    res = browser_interact("")
    assert res["ok"] is False
    assert "URL 不能为空" in res["error"]
    assert res["actions_executed"] == []
    assert res["screenshot"] is None
    assert res["total_chars"] == 0

    res_none = browser_interact(None)
    assert res_none["ok"] is False
    assert "URL 不能为空" in res_none["error"]


def test_browser_interact_unsupported_protocol():
    """Verify unsupported URL scheme returns graceful error."""
    res = browser_interact("ftp://ftp.example.com/file.txt")
    assert res["ok"] is False
    assert "不支持" in res["error"]


def test_browser_interact_actions_robustness():
    """Verify invalid action types, missing keys, and non-dict action elements do not crash."""
    test_html = "<html><head><title>Robust Test</title></head><body><div class='box'>Content</div></body></html>"
    data_url = f"data:text/html,{urllib.parse.quote(test_html)}"

    res = browser_interact(
        data_url,
        actions=[
            "not_a_dict",
            {"action": "unknown_action_xyz"},
            {"action": "click"},  # missing selector
            {"action": "type", "text": "only text"},  # missing selector
            {"action": "extract"},  # missing selector
            {"action": "navigate"},  # missing url
            {"action": "extract", "selector": ".box"},
        ],
        wait_ms=100,
        screenshot=False,
    )
    assert res["ok"] is True
    actions = res["actions_executed"]
    assert len(actions) == 7

    assert actions[0]["status"] == "failed"
    assert "字典对象" in actions[0]["error"]

    assert actions[1]["status"] == "failed"
    assert "不支持的动作类型" in actions[1]["error"]

    assert actions[2]["status"] == "failed"
    assert "缺少必要参数 selector" in actions[2]["error"]

    assert actions[3]["status"] == "failed"
    assert "缺少必要参数 selector" in actions[3]["error"]

    assert actions[4]["status"] == "failed"
    assert "缺少必要参数 selector" in actions[4]["error"]

    assert actions[5]["status"] == "failed"
    assert "缺少必要参数 url" in actions[5]["error"]

    assert actions[6]["status"] == "ok"
    assert actions[6]["extracted"] == "Content"


# ---------------------------------------------------------------- 2. Graceful Degradation Mode
def test_browser_interact_degraded_mode_execution():
    """Verify system gracefully degrades to static DOM extraction when headless browser is missing."""
    test_html = """
    <html>
      <head><title>Degraded Page</title></head>
      <body>
        <div class="header">Static Header</div>
        <div class="desc">Static Description Paragraph</div>
        <button id="submit">Click Me</button>
      </body>
    </html>
    """
    data_url = f"data:text/html,{urllib.parse.quote(test_html)}"

    with patch("app.agent.browser_tools._find_browser_binary", return_value=None):
        res = browser_interact(
            data_url,
            actions=[
                {"action": "extract", "selector": ".header"},
                {"action": "click", "selector": "#submit"},
                {"action": "type", "selector": "input", "text": "test"},
                {"action": "scroll", "direction": "down", "amount": 500},
                {"action": "wait", "ms": 100},
            ],
            screenshot=True,
        )

    assert res["ok"] is True
    assert res["degraded"] is True
    assert res["title"] == "Degraded Page"

    # Verify actions in degraded mode
    actions = res["actions_executed"]
    assert len(actions) == 5
    assert actions[0]["action"] == "extract"
    assert actions[0]["status"] == "ok"
    assert actions[0]["extracted"] == "Static Header"

    # Dynamic actions skipped gracefully
    assert actions[1]["action"] == "click"
    assert actions[1]["status"] == "skipped"
    assert "降级模式" in actions[1]["detail"]

    assert actions[2]["action"] == "type"
    assert actions[2]["status"] == "skipped"

    assert actions[3]["action"] == "scroll"
    assert actions[3]["status"] == "skipped"

    assert actions[4]["action"] == "wait"
    assert actions[4]["status"] == "ok"

    # Content contains degradation notice and markdown formatting
    assert "降级运行提示" in res["content"]
    assert "Static Description Paragraph" in res["content"]

    # Screenshot artifact generated via fallback
    assert res["screenshot"] is not None
    assert res["screenshot"]["url"].startswith("/v1/artifacts/art_")
    assert res["screenshot"]["markdown"] in res["content"]


# ---------------------------------------------------------------- 3. Artifact Saving & Event Emission
def test_browser_interact_screenshot_artifact_and_emit():
    """Verify screenshot creation, artifact storage, emit callback, and Markdown syntax."""
    test_html = "<html><head><title>Snapshot Test</title></head><body><h1>Hello Visual</h1></body></html>"
    data_url = f"data:text/html,{urllib.parse.quote(test_html)}"

    mock_emit = MagicMock()
    res = browser_interact(data_url, wait_ms=100, screenshot=True, emit=mock_emit)

    assert res["ok"] is True
    shot = res["screenshot"]
    assert shot is not None
    assert "id" in shot
    assert "url" in shot
    assert shot["url"].startswith("/v1/artifacts/")
    assert shot["bytes"] > 0
    assert shot["markdown"] == f"![browser_snapshot]({shot['url']})"

    # Markdown reference must be present in content
    assert shot["markdown"] in res["content"]
    assert res["total_chars"] == len(res["content"])

    # Emit event verified
    mock_emit.assert_called_once()
    event_name, payload = mock_emit.call_args[0]
    assert event_name == "artifact.attached"
    assert payload["artifact_id"] == shot["id"]
    assert payload["url"] == shot["url"]
    assert payload["kind"] == "image/png"


def test_browser_interact_explicit_screenshot_action():
    """Verify explicit {"action": "screenshot"} captures and records intermediate snapshot."""
    test_html = "<html><head><title>Action Shot</title></head><body><h1>Action Shot Page</h1></body></html>"
    data_url = f"data:text/html,{urllib.parse.quote(test_html)}"

    res = browser_interact(
        data_url,
        actions=[
            {"action": "wait", "ms": 50},
            {"action": "screenshot"},
        ],
        wait_ms=100,
        screenshot=True,
    )
    assert res["ok"] is True
    shot_action = [a for a in res["actions_executed"] if a["action"] == "screenshot"]
    assert len(shot_action) == 1
    assert shot_action[0]["status"] == "ok"
    assert "artifact_id" in shot_action[0]
    assert "url" in shot_action[0]


# ---------------------------------------------------------------- 4. Live / Multi-Step Browser Execution
def test_browser_interact_full_workflow():
    """Test full multi-step interactive workflow (type, click, dynamic DOM update, extract, scroll)."""
    test_html = """
    <html>
      <head><title>Interactive App</title></head>
      <body>
        <input id="kw" type="text" />
        <button id="search-btn" onclick="document.getElementById('result').innerText = 'Query: ' + document.getElementById('kw').value">Search</button>
        <div id="result">Idle</div>
        <div class="summary">Summary of analysis</div>
        <div style="height: 1200px;">Spacer</div>
        <div id="bottom-marker">Bottom Reached</div>
      </body>
    </html>
    """
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(test_html)
        temp_path = f.name

    try:
        file_url = "file:///" + temp_path.replace("\\", "/")
        res = browser_interact(
            file_url,
            actions=[
                {"action": "type", "selector": "#kw", "text": "Deep Learning Models"},
                {"action": "click", "selector": "#search-btn"},
                {"action": "wait", "ms": 100},
                {"action": "extract", "selector": "#result"},
                {"action": "extract", "selector": ".summary"},
                {"action": "scroll", "direction": "down", "amount": 600},
            ],
            wait_ms=100,
            screenshot=True,
        )

        assert res["ok"] is True
        assert res["title"] == "Interactive App"

        executed = res["actions_executed"]
        assert len(executed) == 6

        # Check DOM interaction result
        if not res.get("degraded"):
            type_act = executed[0]
            assert type_act["status"] == "ok"

            click_act = executed[1]
            assert click_act["status"] == "ok"

            extract_result = executed[3]
            assert extract_result["status"] == "ok"
            assert "Query: Deep Learning Models" in extract_result["extracted"]

            extract_summary = executed[4]
            assert extract_summary["status"] == "ok"
            assert "Summary of analysis" in extract_summary["extracted"]

            scroll_act = executed[5]
            assert scroll_act["status"] == "ok"

        # Content must contain extracted content and snapshot
        assert "Deep Learning Models" in res["content"] or "Summary of analysis" in res["content"]
        assert res["screenshot"] is not None
        assert res["screenshot"]["markdown"] in res["content"]
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


# ---------------------------------------------------------------- 5. Export and Dispatch Integration
def test_browser_interact_web_tools_reexport():
    """Verify web_tools exports browser_interact with identical behavior."""
    assert hasattr(web_tools, "browser_interact")
    res = web_tools.browser_interact("")
    assert res["ok"] is False


def test_browser_interact_tools_dispatch():
    """Verify tools.dispatch can invoke browser_interact."""
    test_html = "<html><head><title>Dispatch Test</title></head><body><h1>Dispatched</h1></body></html>"
    data_url = f"data:text/html,{urllib.parse.quote(test_html)}"

    res = tools.dispatch(
        "browser_interact",
        {"url": data_url, "screenshot": False, "wait_ms": 100},
    )
    assert res["ok"] is True
    assert "Dispatched" in res["content"]



# ---------------------------------------------------------------- 6. Advanced Edge Cases & Modifiers
def test_browser_interact_max_chars_truncation():
    """Verify max_chars properly limits markdown content size."""
    long_p = "<p>" + ("Very long sentence for testing truncation. " * 50) + "</p>"
    test_html = f"<html><head><title>Truncate Test</title></head><body>{long_p}</body></html>"
    data_url = f"data:text/html,{urllib.parse.quote(test_html)}"

    res = browser_interact(data_url, wait_ms=50, screenshot=False, max_chars=300)
    assert res["ok"] is True
    assert "截断" in res["content"]
    assert res["total_chars"] <= 400


def test_browser_interact_no_screenshot():
    """Verify screenshot=False generates no artifact and no markdown image tag."""
    test_html = "<html><head><title>No Shot Test</title></head><body><p>Text only</p></body></html>"
    data_url = f"data:text/html,{urllib.parse.quote(test_html)}"

    res = browser_interact(data_url, wait_ms=50, screenshot=False)
    assert res["ok"] is True
    assert res["screenshot"] is None
    assert "![browser_snapshot]" not in res["content"]


def test_browser_interact_selector_not_found_handled_gracefully():
    """Verify attempting to click or extract non-existent selector records error without crashing."""
    test_html = "<html><head><title>NonExistent Test</title></head><body><p>Present</p></body></html>"
    data_url = f"data:text/html,{urllib.parse.quote(test_html)}"

    res = browser_interact(
        data_url,
        actions=[
            {"action": "click", "selector": "#non-existent-btn"},
            {"action": "extract", "selector": ".missing-class"},
        ],
        wait_ms=50,
        screenshot=False,
    )
    assert res["ok"] is True
    executed = res["actions_executed"]
    assert len(executed) == 2
    assert executed[0]["status"] == "failed"
    assert executed[1]["status"] == "failed"


def test_browser_interact_scroll_directions():
    """Verify scroll supports up, down, top, bottom directions without crashing."""
    test_html = "<html><head><title>Scroll Test</title></head><body style='height:3000px'><p>Long Page</p></body></html>"
    data_url = f"data:text/html,{urllib.parse.quote(test_html)}"

    res = browser_interact(
        data_url,
        actions=[
            {"action": "scroll", "direction": "down", "amount": 400},
            {"action": "scroll", "direction": "bottom"},
            {"action": "scroll", "direction": "up", "amount": 200},
            {"action": "scroll", "direction": "top"},
        ],
        wait_ms=50,
        screenshot=False,
    )
    assert res["ok"] is True
    executed = res["actions_executed"]
    assert len(executed) == 4
    for act in executed:
        if not res.get("degraded"):
            assert act["status"] == "ok"
        else:
            assert act["status"] == "skipped"


def test_browser_interact_single_dict_action():
    """Verify actions passed as a single dictionary instead of a list works properly."""
    test_html = "<html><head><title>Single Action</title></head><body><h1 id='title'>Hello Single</h1></body></html>"
    data_url = f"data:text/html,{urllib.parse.quote(test_html)}"

    res = browser_interact(
        data_url,
        actions={"action": "extract", "selector": "#title"},
        wait_ms=50,
        screenshot=False,
    )
    assert res["ok"] is True
    assert len(res["actions_executed"]) == 1
    assert res["actions_executed"][0]["extracted"] == "Hello Single"


def test_browser_interact_navigate_action():
    """Verify navigate action transitions to a new URL."""
    page1 = "<html><head><title>Page 1</title></head><body><p>First</p></body></html>"
    page2 = "<html><head><title>Page 2</title></head><body><p>Second</p></body></html>"
    url1 = f"data:text/html,{urllib.parse.quote(page1)}"
    url2 = f"data:text/html,{urllib.parse.quote(page2)}"

    res = browser_interact(
        url1,
        actions=[
            {"action": "navigate", "url": url2},
        ],
        wait_ms=50,
        screenshot=False,
    )
    assert res["ok"] is True
    assert len(res["actions_executed"]) == 1
    assert res["actions_executed"][0]["status"] == "ok"
    assert "Page 2" in res["title"] or "Second" in res["content"]
