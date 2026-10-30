"""Test suite for render_chart tool, doc preview generator, and artifact raw/preview routing."""
from __future__ import annotations

import io
import pathlib
import pytest
from fastapi.testclient import TestClient

from app import artifacts, db, devices
from app.agent import ext_tools, tools
from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    with db.get_conn() as conn:
        return devices.issue(conn, name="test-device-chart", kind="exe")


def _auth(d):
    return {"Authorization": f"Bearer {d['token']}"}


# ---------------------------------------------------------------- 1. render_chart Tests
def test_render_chart_standard_types():
    # Bar
    r_bar = ext_tools.render_chart(
        title="测试柱状图",
        chart_type="bar",
        data={
            "categories": ["一月", "二月", "三月"],
            "series": [{"name": "销售额", "values": [120, 180, 240]}],
        },
        theme="modern",
    )
    assert r_bar["ok"] is True
    assert "artifact_id" in r_bar
    assert r_bar["url"].startswith("/v1/artifacts/")
    assert "![测试柱状图]" in r_bar["markdown"]

    # Line
    r_line = ext_tools.render_chart(
        title="测试折线图",
        chart_type="line",
        data={
            "categories": ["2023", "2024", "2025"],
            "series": [{"name": "用户数", "values": [1000, 2500, 5000]}],
        },
        theme="financial",
    )
    assert r_line["ok"] is True

    # Pie
    r_pie = ext_tools.render_chart(
        title="测试饼图",
        chart_type="pie",
        data={
            "categories": ["A 业务", "B 业务", "C 业务"],
            "series": [{"values": [40, 35, 25]}],
        },
        theme="academic",
    )
    assert r_pie["ok"] is True

    # Scatter
    r_scatter = ext_tools.render_chart(
        title="测试散点图",
        chart_type="scatter",
        data={"x": [1, 2, 3, 4], "y": [10, 15, 13, 22]},
    )
    assert r_scatter["ok"] is True

    # Area
    r_area = ext_tools.render_chart(
        title="测试面积图",
        chart_type="area",
        data={
            "categories": ["Q1", "Q2", "Q3"],
            "series": [{"name": "访问量", "values": [100, 200, 350]}],
        },
    )
    assert r_area["ok"] is True

    # Radar
    r_radar = ext_tools.render_chart(
        title="测试雷达图",
        chart_type="radar",
        data={
            "categories": ["性能", "安全", "体验", "功能", "成本"],
            "series": [{"name": "产品评测", "values": [85, 90, 78, 92, 80]}],
        },
    )
    assert r_radar["ok"] is True


def test_render_chart_custom_code():
    code = """
fig, ax = plt.subplots(figsize=(6, 4))
ax.plot([1, 2, 3], [5, 2, 8], color='coral', marker='s')
ax.set_title('自定义测试曲线')
plt.savefig(output_path, dpi=300, bbox_inches='tight')
"""
    res = ext_tools.render_chart(
        title="自定义绘图产物",
        chart_type="custom_code",
        python_code=code,
        theme="modern",
    )
    assert res["ok"] is True
    assert res["artifact_id"].startswith("art_")


def test_render_chart_tool_dispatch():
    events = []
    def fake_emit(kind, payload):
        events.append((kind, payload))

    res = tools.dispatch(
        "render_chart",
        {
            "title": "调度测试图表",
            "chart_type": "bar",
            "data": {"categories": ["X", "Y"], "values": [10, 20]},
            "theme": "financial",
        },
        emit=fake_emit,
    )
    assert res["ok"] is True
    assert len(events) == 1
    assert events[0][0] == "artifact.attached"
    assert events[0][1]["artifact_id"] == res["artifact_id"]


# ---------------------------------------------------------------- 2. Non-Image Artifact Previews & Raw Routing
def test_doc_preview_generation():
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    preview_bytes = artifacts.generate_doc_preview("财务研报.pdf", "application/pdf", pdf_bytes)
    assert isinstance(preview_bytes, bytes)
    assert preview_bytes.startswith(b"\x89PNG")
    assert len(preview_bytes) > 5000


def test_publish_artifact_companion_preview(tmp_path, monkeypatch, client, device):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    monkeypatch.setenv("M0_WORKSPACE", str(ws))

    # Create a non-image file (HTML)
    html_file = ws / "report.html"
    html_file.write_text("<html><body><h1>年度总结报告</h1><p>2026年核心指标大幅提升。</p></body></html>", encoding="utf-8")

    # Publish
    events = []
    res = tools.dispatch("publish_artifact", {"path": "report.html", "name": "年度总结报告.html"}, emit=lambda k, p: events.append((k, p)))
    assert res["ok"] is True
    art_id = res["artifact"]["id"]

    # Companion preview must exist on disk
    preview_file = artifacts.root() / f"{art_id}.preview.png"
    assert preview_file.is_file()

    # GET /v1/artifacts/{id} -> returns preview PNG
    resp_preview = client.get(f"/v1/artifacts/{art_id}", headers=_auth(device))
    assert resp_preview.status_code == 200
    assert resp_preview.headers["content-type"] == "image/png"
    assert "x-artifact-original-kind" in resp_preview.headers
    assert resp_preview.content.startswith(b"\x89PNG")

    # GET /v1/artifacts/{id}?raw=1 -> returns raw HTML
    resp_raw = client.get(f"/v1/artifacts/{art_id}?raw=1", headers=_auth(device))
    assert resp_raw.status_code == 200
    assert "年度总结报告" in resp_raw.text

    # GET /v1/artifacts/{id}/raw -> returns raw HTML
    resp_raw_route = client.get(f"/v1/artifacts/{art_id}/raw", headers=_auth(device))
    assert resp_raw_route.status_code == 200
    assert "年度总结报告" in resp_raw_route.text


# ---------------------------------------------------------------- 3. local_* Tool Restrictions
def test_local_tools_restriction_prefix():
    local_tools = [t["function"] for t in tools.TOOL_SPECS if t["function"]["name"].startswith("local_")]
    assert len(local_tools) == 14, f"Expected 14 local tools, got {len(local_tools)}"

    prefix = "【仅限用户在消息中明确要求操作其个人电脑时使用，严禁在常规云端任务中主动调用或推荐】"
    for t in local_tools:
        desc = t["description"]
        assert desc.startswith(prefix), f"Tool {t['name']} description missing warning prefix: {desc}"
