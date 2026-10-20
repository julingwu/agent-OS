"""Both transports register shell-generated deliverables beside explicit writes."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time

import pytest

from app.api import verbs
from app.db import connect, init_db
from app.engine import dsh_adapter, runner_acp, task_generator
from app.models import SpawnRunIn


@pytest.fixture()
def isolated_run(tmp_path, monkeypatch):
    for key, value in {
        "DB_PATH": str(tmp_path / "runner.db"),
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": "",
        "UI_CRON": "0", "WECOM_WATCHER": "0", "WECOM_APPROVAL_POLLER": "0",
        "OCTO_RUNNER": "0", "RUNNER": "mock", "UI_RUNNER": "mock",
        "M0_SANDBOX": "none", "M0_SANDBOX_ACP": "0", "M0_HEARTBEAT_SECONDS": "0",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(dsh_adapter, "mount_shared", lambda ws: None)
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(dsh_adapter, "_log_path", lambda rid: logs / (rid + ".jsonl"))
    server = tmp_path / "server"
    server.mkdir()
    (server / "README.md").write_text("server-only README", encoding="utf-8")
    monkeypatch.chdir(server)
    init_db()
    conn = connect()
    task = task_generator.generate(conn, "写一个 Python 计算器")
    run = verbs.do_spawn_run(conn, SpawnRunIn(task_id=task["task_id"], run_seq=1))
    conn.commit()
    yield conn, task["task_id"], run["run_id"], server
    conn.close()


def _create_outputs(ws):
    outputs = {
        "script.py": "print('task script')\n",
        "data/sales.csv": "department,amount\n研发,3000\n",
        "summary.json": '{"研发": 3000}',
        "index.html": "<!doctype html><html><body>任务报表</body></html>",
        "README.md": "Run python script.py to produce the task report.",
    }
    for name, content in {
        **outputs,
        "old.txt": "a previous run",
        "old-recent.txt": "also predates this turn",
        ".dsh/session.json": "private runtime state",
        "node_modules/pkg/README.md": "dependency readme",
        "__pycache__/script.pyc": "bytecode",
        "generated.pyc": "bytecode",
        "shared/resource.md": "shared resource",
        "package-lock.json": "dependency lock",
        "AppData/Local/Microsoft/PowerShell/telemetry.uuid": "runtime telemetry id",
        "AppData/Local/Microsoft/PowerShell/StartupProfileData-NonInteractive": "runtime startup profile",
    }.items():
        path = ws / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    old = time.time() - 60
    os.utime(ws / "old.txt", (old, old))
    recent = time.time() - 0.5
    os.utime(ws / "old-recent.txt", (recent, recent))
    (ws.parent / "outside.txt").write_text("outside the task", encoding="utf-8")
    return outputs


@pytest.mark.parametrize("transport", ["acp", "headless"])
@pytest.mark.parametrize("success", [True, False])
def test_runners_register_all_fresh_deliverables_but_only_known_partial_files(
        isolated_run, monkeypatch, transport, success):
    conn, tid, rid, server = isolated_run
    expected = {}

    class FakeAcpClient:
        pid = 12345

        def __init__(self, cmd, *, cwd, env, default_timeout):
            self.cwd, self.log, self.stderr = pathlib.Path(cwd), [], []

        def start(self):
            pass

        def new_session(self):
            return {"sessionId": "sess-shell-files"}

        def prompt(self, sid, prompt, *, on_update, timeout):
            expected.update(_create_outputs(self.cwd))
            for i, target in enumerate(("script.py", "./script.py")):
                on_update({"sessionUpdate": "tool_call", "toolCallId": str(i),
                           "title": "write", "rawInput": {"file_path": target}})
            return {"result": {"stopReason": "end_turn" if success else "refusal"}, "updates": 2}

        def close(self, *, session_id):
            pass

    class FakeHeadlessProcess:
        pid = 12345

        def __init__(self, cmd, *, cwd, **kwargs):
            self.cwd = pathlib.Path(cwd)
            self.returncode = 0 if success else 1

        def communicate(self, prompt=None, timeout=None):
            expected.update(_create_outputs(self.cwd))
            events = [{"type": "tool_call", "tool": "write", "input": {"file_path": name}}
                      for name in ("script.py", "./script.py")]
            return "\n".join(json.dumps(ev) for ev in events), ""

    if transport == "acp":
        monkeypatch.setattr(runner_acp, "AcpClient", FakeAcpClient)
    else:
        monkeypatch.setattr(dsh_adapter.subprocess, "Popen", FakeHeadlessProcess)
    result = dsh_adapter.execute_run(conn, rid, runner="mock_acp" if transport == "acp" else "mock")
    assert result["exit_code"] == (0 if success else 1), result
    ws = dsh_adapter.task_workspace(tid, "system").resolve()
    registered = {pathlib.Path(row["file_path"]): row["content"] for row in conn.execute(
        "SELECT file_path,content FROM artifacts WHERE run_id=?", (rid,))}
    wanted = expected if success else {"script.py": expected["script.py"]}
    assert registered == {ws / name: content for name, content in wanted.items()}
    assert len(result["artifacts"]) == len(wanted), "aliases and scan results must be deduplicated"
    assert (server / "README.md").read_text(encoding="utf-8") == "server-only README"


def test_explicit_candidates_cannot_escape_or_collect_runtime_noise(isolated_run):
    _, tid, _, _ = isolated_run
    ws = dsh_adapter.task_workspace(tid, "system").resolve()
    _create_outputs(ws)
    files = dsh_adapter.collect_artifact_files([
        "script.py", "./script.py", str(ws / "script.py"),
        "../outside.txt", str(ws.parent / "outside.txt"),
        ".dsh/session.json", "shared/resource.md", "generated.pyc",
        "node_modules/pkg/README.md", "__pycache__/script.pyc",
    ], since=time.time(), task_id=tid, actor="system", success=False)
    assert files == [str(ws / "script.py")]


def test_scanning_does_not_follow_junction_or_symlink_resources(isolated_run):
    _, tid, _, server = isolated_run
    ws = dsh_adapter.task_workspace(tid, "system").resolve()
    linked = ws / "borrowed"
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(server)],
                              capture_output=True, check=False)
        if made.returncode:
            pytest.skip("junction creation unavailable")
    else:
        linked.symlink_to(server, target_is_directory=True)
    since = time.time() - 10
    (ws / "report.json").write_text("{}", encoding="utf-8")
    files = dsh_adapter.collect_artifact_files([str(linked / "README.md")], since=since,
                                             task_id=tid, actor="system", success=True)
    assert files == [str(ws / "report.json")]


def test_scan_prunes_dependency_trees_and_bounds_deliverable_count(isolated_run, monkeypatch):
    _, tid, _, _ = isolated_run
    ws = dsh_adapter.task_workspace(tid, "system").resolve()
    dependency = ws / "node_modules" / "pkg"
    dependency.mkdir(parents=True)
    for i in range(30):
        (dependency / f"noise-{i}.txt").write_text("dependency", encoding="utf-8")
    for i in range(4):
        (ws / f"report-{i}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dsh_adapter, "SCAN_MAX_ENTRIES", 8)
    monkeypatch.setattr(dsh_adapter, "SCAN_MAX_FILES", 3)
    files = dsh_adapter._scan_workspace(time.time() - 10, tid, "system")
    assert len(files) == 3
    assert all(pathlib.Path(path).name.startswith("report-") for path in files)


@pytest.mark.parametrize("success", [True, False])
def test_runtime_profile_exclusion_is_scoped_and_preserves_legitimate_side_outputs(isolated_run, success):
    _, tid, _, _ = isolated_run
    ws = dsh_adapter.task_workspace(tid, "system").resolve()
    runtime = {
        "AppData/Local/Microsoft/PowerShell/telemetry.uuid",
        "AppData/Local/Microsoft/PowerShell/StartupProfileData-NonInteractive",
        "AppData/Local/Microsoft/PowerShell/cache/analysis.bin",
    }
    legitimate = {
        "report.json",
        "reports/telemetry.uuid",
        "reports/StartupProfileData-NonInteractive",
        "AppData/Local/Business/report.json",
        "AppData/Local/Microsoft/PowerShellReports/summary.csv",
        "exports/AppData/Local/Microsoft/PowerShell/telemetry.uuid",
    }
    for name in runtime | legitimate:
        path = ws / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test output", encoding="utf-8")
    files = dsh_adapter.collect_artifact_files(sorted(runtime | legitimate), since=time.time() - 10,
                                             task_id=tid, actor="system", success=success)
    assert set(files) == {str(ws / name) for name in legitimate}


def test_scan_prunes_powershell_runtime_profile_before_descending(isolated_run, monkeypatch):
    _, tid, _, _ = isolated_run
    ws = dsh_adapter.task_workspace(tid, "system").resolve()
    runtime = ws / "AppData" / "Local" / "Microsoft" / "PowerShell"
    runtime.mkdir(parents=True)
    (runtime / "StartupProfileData-NonInteractive").write_text("runtime", encoding="utf-8")
    report = ws / "report.json"
    report.write_text("{}", encoding="utf-8")
    scanned = []
    original_scandir = os.scandir

    def observe(directory):
        scanned.append(pathlib.Path(directory))
        return original_scandir(directory)

    monkeypatch.setattr(dsh_adapter.os, "scandir", observe)
    assert dsh_adapter._scan_workspace(time.time() - 10, tid, "system") == [str(report)]
    assert runtime not in scanned
