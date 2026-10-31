"""The UI test helper must never merge into a repository or a used directory."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools/serve_ui_test.py"
spec = importlib.util.spec_from_file_location("serve_ui_test_helper", SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.mark.parametrize("suffix", ["", "workspace", "data", "app", "tools", "scripts", "web/dist", "untracked/new"])
def test_rejects_every_repo_subdirectory_before_copying(tmp_path, monkeypatch, suffix):
    repo = tmp_path / "repo"
    repo.mkdir()
    sentinel = repo / "sentinel.txt"
    sentinel.write_text("untouched")
    copied = []
    monkeypatch.setattr(helper.shutil, "copytree", lambda *args, **kwargs: copied.append(args))
    with pytest.raises(SystemExit) as stopped:
        helper._data_directory(argparse.ArgumentParser(), repo, repo / suffix)
    assert stopped.value.code == 2
    assert sentinel.read_text() == "untouched" and not copied


def test_rejects_nonempty_external_directory_without_touching_sentinel(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "used"
    target.mkdir()
    sentinel = target / "app.py"
    sentinel.write_text("unrelated existing code")
    result = subprocess.run([sys.executable, str(SCRIPT), "--data-dir", str(target), "--port", "8080"],
                            capture_output=True, text=True)
    assert result.returncode == 2 and "new or empty" in result.stderr
    assert sentinel.read_text() == "unrelated existing code"
    assert list(target.iterdir()) == [sentinel]


@pytest.mark.parametrize("target_kind", ["new", "empty", "default"])
def test_accepts_only_fresh_external_directories(tmp_path, monkeypatch, target_kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "fresh"
    if target_kind != "new":
        target.mkdir()
    monkeypatch.setattr(helper.tempfile, "mkdtemp", lambda **kwargs: str(target))
    actual = helper._data_directory(argparse.ArgumentParser(), repo, None if target_kind == "default" else target)
    assert actual == target
    assert not actual.exists() or not list(actual.iterdir())


@pytest.mark.parametrize("port", ["0", "-1", "65536", "999999", "8077", "not-a-port"])
def test_cli_rejects_invalid_ports_without_creating_target(tmp_path, port):
    target = tmp_path / "not-created"
    result = subprocess.run([sys.executable, str(SCRIPT), "--data-dir", str(target), "--port", port],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert not target.exists()


def test_cli_rejects_repo_workspace_without_copying():
    repo = SCRIPT.parents[1]
    result = subprocess.run([sys.executable, str(SCRIPT), "--data-dir", str(repo / "workspace"), "--port", "8080"],
                            capture_output=True, text=True)
    assert result.returncode == 2 and "outside the source repository" in result.stderr
