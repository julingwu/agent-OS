"""v5.2 生成层：确定性兜底落地页必须自带真实 CTA，且能通过它自己的验收脚本。

物理事实（修复前）：_render_finops_html() 的模板从来没有 id="btn-calc" 的行动按钮，
而它自己生成的 test_check.py 第 723 行恰恰断言 check('id="btn-calc"' in raw)。
自检因此永远 FAIL -> 内核重写一次同样内容再跑 -> 还是 FAIL -> 交付物始终没有按钮
-> L0「含可点击的行动按钮」必然驳回（生产库 438 次同一失败）。
这是模板与自身验收规格互相矛盾的缺陷，不是模型能力问题。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from app.engine.runner_native import NativeKernel


@pytest.fixture(scope="module")
def kernel() -> NativeKernel:
    return NativeKernel.__new__(NativeKernel)


def test_landing_page_has_a_real_call_to_action(kernel):
    page = kernel._render_finops_html("秋季护肤落地页")
    # L0 判据认「可点击的行动按钮」：必须有 <button 与行动文案
    assert "<button" in page
    assert "立即" in page
    # 模板自己的验收脚本要求的具体 id 必须存在，否则自检永远不过
    assert 'id="btn-calc"' in page
    # 占位符必须被替换干净，不能把 __HEADING__ 漏到交付物里
    assert "__HEADING__" not in page


def test_heading_is_task_specific_and_escaped(kernel):
    page = kernel._render_finops_html("秋季护肤落地页")
    assert "秋季护肤落地页" in page
    assert "FinOps" in page and "ROI" in page, "自定义标题也不能丢掉自检要求的字样"
    # 不同任务不能产出同一份页面
    assert page != kernel._render_finops_html("企业级数字员工协作平台")
    # 标题注入 HTML 必须转义，不能把标题当标记解析
    escaped = kernel._render_finops_html("<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in escaped
    assert "&lt;script&gt;" in escaped


def test_default_heading_still_works(kernel):
    page = kernel._render_finops_html()
    assert "FinOps" in page and "ROI" in page
    assert 'id="btn-calc"' in page


@pytest.mark.parametrize("heading", [None, "秋季护肤落地页 · 敏感肌修护方案"])
def test_template_passes_its_own_acceptance_script(kernel, tmp_path, heading):
    """决定性断言：用模板自己生成的 test_check.py 真跑它自己的 index.html。"""
    (tmp_path / "index.html").write_text(kernel._render_finops_html(heading), encoding="utf-8")
    (tmp_path / "test_check.py").write_text(kernel._render_test_check_py(), encoding="utf-8")
    proc = subprocess.run([sys.executable, "test_check.py"], cwd=str(tmp_path),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    assert proc.returncode == 0, (proc.stdout or "")[-1500:] + (proc.stderr or "")[-800:]
    assert "ALL CHECKS PASSED" in proc.stdout
