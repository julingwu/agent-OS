"""M14：Low 完整性标签必须静默完成（不得依赖 UAC 提权）。

真实事故：仓库目录 ACL 属 Administrators，`icacls /setintegritylevel` 返回 5，
旧代码回落到 `Start-Process -Verb RunAs` → 每次启动都弹 UAC。现在工作区放在
用户自己的 %LOCALAPPDATA% 下，静默可写；RunAs 路径必须彻底不存在。
"""
from __future__ import annotations

import pathlib

import pytest

from app.engine import lowil


def test_workspace_defaults_under_localappdata(tmp_path, monkeypatch):
    from app.engine import dsh_adapter
    monkeypatch.delenv("M0_WORKSPACE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    ws = dsh_adapter.workspace()
    local = (tmp_path / "Local").resolve()
    assert local in ws.resolve().parents or ws.resolve() == local, ws


def test_label_is_applied_and_inherited(tmp_path):
    root = tmp_path / "workspace"
    ok, how = lowil.apply_low(root)
    assert ok, how
    child = root / "AGENT" / "T-1"
    child.mkdir(parents=True)
    # 子目录靠 (OI)(CI) 继承，不需要再打一次
    assert lowil.is_low(child) is True


def test_no_elevation_fallback_remains():
    """不允许出现「用 powershell 提权跑 icacls」这种代码路径。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("tools/sandbox_win.py", "app/engine/lowil.py",
                "app/engine/dsh_adapter.py", "app/engine/sandbox_env.py"):
        text = (root / rel).read_text(encoding="utf-8")
        offenders = [line.strip() for line in text.splitlines()
                     if "RunAs" in line and ("powershell" in line.lower()
                                             or "subprocess" in line)]
        assert not offenders, f"{rel} 仍有提权兜底: {offenders}"
