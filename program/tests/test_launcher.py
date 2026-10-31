"""One-click entry: ownership, reuse, restart, and browser ordering."""
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
import os
import sys

import launcher
import supervisor


@pytest.mark.skipif(sys.platform != "win32", reason="Windows inventory")
def test_real_read_only_inventory_is_parseable():
    result = launcher.windows_inventory()
    assert any(int(p["ProcessId"]) == os.getpid() for p in result["processes"])
    assert isinstance(result["listeners"], list)


def test_root_has_one_cmd_entry():
    assert [p.name for p in launcher.PROJECT.glob("*.cmd")] == ["Start.cmd"]


def process(pid, parent, command, executable=None):
    return {"ProcessId": pid, "ParentProcessId": parent, "CommandLine": command,
            "ExecutablePath": executable or str(launcher.PROJECT / ".venv/Scripts/python.exe")}


def test_ownership_does_not_include_neighbor_or_unrelated_python():
    own = str(launcher.PROGRAM / "supervisor.py")
    inventory = {"processes": [
        process(10, 1, f'python "{own}"'),
        process(11, 10, "python -m uvicorn app.main:app", r"C:\Python\python.exe"),
        process(20, 1, 'python "D:/Agent-OS-copy/program/supervisor.py"', r"C:\Python\python.exe"),
        process(30, 1, "python unrelated.py"),
        process(40, 1, f'python "{launcher.PROGRAM / "launcher.py"}"'),
    ]}
    assert launcher.owned_processes(inventory) == {10, 11}


def test_legacy_venv_uvicorn_and_children_are_owned():
    inventory = {"processes": [process(10, 1, "python -m uvicorn app.main:app --port 8077"),
                                process(11, 10, "python backend", r"C:\Python\python.exe")]}
    assert launcher.owned_processes(inventory) == {10, 11}


@pytest.fixture
def entry(monkeypatch, tmp_path):
    monkeypatch.chdir(launcher.PROGRAM)
    monkeypatch.setattr(launcher, "settings", lambda: (tmp_path, [8077, 8081, 8082]))

    @contextmanager
    def lock(_):
        yield True

    monkeypatch.setattr(launcher, "launch_lock", lock)
    monkeypatch.setattr(launcher, "windows_inventory", Mock(return_value={
        "processes": [process(10, 1, f'python "{launcher.PROGRAM / "supervisor.py"}"')],
        "listeners": [{"LocalPort": 8077, "OwningProcess": 10}]}))
    monkeypatch.setattr(launcher, "stop_owned", Mock())
    monkeypatch.setattr(launcher, "ports_open", lambda _: False)
    monkeypatch.setattr(launcher, "start_child", Mock())
    monkeypatch.setattr(launcher, "ensure_daemon", Mock())
    monkeypatch.setattr(launcher.webbrowser, "open", Mock(return_value=True))
    state = {"active": "green", "standby": "red", "red": {"ready": True}, "running_revision": "new", "maintenance": False,
             "green": {"runtime": {"workload_busy": False}}}
    monkeypatch.setattr(launcher, "probe", Mock(return_value=state))
    monkeypatch.setattr(launcher, "wait_ready", Mock(return_value=state))
    monkeypatch.setattr(supervisor, "fingerprint", lambda: "new")
    return state


def test_healthy_repeat_opens_browser_without_duplicate_service(entry):
    assert launcher.main([]) == 0
    launcher.start_child.assert_not_called()
    launcher.stop_owned.assert_not_called()
    launcher.ensure_daemon.assert_called_once()
    launcher.webbrowser.open.assert_called_once_with("http://127.0.0.1:8077/ui/")


def test_changed_idle_service_is_restarted_before_browser(entry):
    entry["running_revision"] = "old"
    launcher.windows_inventory.side_effect = [launcher.windows_inventory.return_value,
                                             {"processes": [], "listeners": []}]
    order = []
    launcher.wait_ready.side_effect = lambda _: order.append("ready") or entry
    launcher.ensure_daemon.side_effect = lambda _: order.append("device")
    launcher.webbrowser.open.side_effect = lambda _: order.append("browser") or True
    assert launcher.main([]) == 0
    launcher.stop_owned.assert_called_once()
    launcher.start_child.assert_called_once_with("supervisor.py", "gateway.log")
    assert order == ["ready", "device", "browser"]


def test_busy_old_service_is_kept(entry):
    entry["running_revision"] = "old"
    entry["green"]["runtime"]["workload_busy"] = True
    assert launcher.main([]) == 0
    launcher.stop_owned.assert_not_called()
    launcher.start_child.assert_not_called()


def test_other_software_occupying_port_is_not_killed(entry):
    launcher.windows_inventory.return_value["listeners"][0]["OwningProcess"] = 999
    with pytest.raises(RuntimeError, match="其他程序"):
        launcher.main([])
    launcher.stop_owned.assert_not_called()
    launcher.start_child.assert_not_called()
    launcher.webbrowser.open.assert_not_called()


def test_start_failure_does_not_open_browser(entry):
    launcher.windows_inventory.return_value = {"processes": [], "listeners": []}
    launcher.probe.return_value = None
    launcher.wait_ready.side_effect = RuntimeError("not ready")
    with pytest.raises(RuntimeError):
        launcher.main([])
    launcher.webbrowser.open.assert_not_called()


def test_owner_zero_requires_correlated_project_and_backend(entry):
    launcher.windows_inventory.return_value["listeners"][0]["OwningProcess"] = 0
    entry["project_root"] = str(launcher.PROJECT)
    entry["green"]["runtime"]["pid"] = 10
    assert launcher.main(["--no-browser"]) == 0
    launcher.stop_owned.assert_not_called()


def test_owner_zero_without_identity_is_not_killed(entry):
    launcher.windows_inventory.return_value["listeners"][0]["OwningProcess"] = 0
    with pytest.raises(RuntimeError):
        launcher.main([])
    launcher.stop_owned.assert_not_called()
