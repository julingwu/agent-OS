"""The one-click local entry. Own only this checkout's process trees."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import webbrowser

import httpx

PROGRAM = Path(__file__).resolve().parent
PROJECT = PROGRAM.parent
sys.path.insert(0, str(PROGRAM))


def settings():
    from app.config import load_env
    load_env(PROGRAM / ".env")
    # Keep user data and credentials outside the implementation tree.
    os.environ.setdefault("DB_PATH", str(PROJECT / "data" / "oa.db"))
    os.environ.setdefault("M0_WORKSPACE", str(PROJECT / "workspace"))
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    data = Path(os.environ.get("AGENTOS_LAUNCHER_DATA") or PROJECT / "data")
    data.mkdir(parents=True, exist_ok=True)
    ports = [int(os.environ.get(name, default)) for name, default in
             (("AGENTOS_GATEWAY_PORT", "8077"), ("AGENTOS_GREEN_PORT", "8081"), ("AGENTOS_RED_PORT", "8082"))]
    return data, ports


def windows_inventory():
    # Command lines are inspected only in memory; never print them (tokens may
    # be present in existing device launch arguments).
    script = """$ErrorActionPreference='Stop'
$p=@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine,ExecutablePath)
$l=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object LocalPort,OwningProcess)
@{processes=$p;listeners=$l} | ConvertTo-Json -Depth 4 -Compress
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                            capture_output=True, encoding="utf-8", errors="replace", timeout=25,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("无法读取本机进程状态，启动已停止。")
    return json.loads(result.stdout.lstrip("\ufeff"))


def owned_processes(inventory):
    """Recognize exact project scripts, or legacy uvicorn under this venv."""
    processes = {int(p["ProcessId"]): p for p in inventory["processes"]}
    root = str(PROJECT).replace("\\", "/").lower()
    owned = set()
    for pid, proc in processes.items():
        command = (proc.get("CommandLine") or "").replace("\\", "/").lower()
        executable = (proc.get("ExecutablePath") or "").replace("\\", "/").lower()
        is_python = Path(executable).name in ("python.exe", "pythonw.exe")
        if not is_python:
            continue
        # Explicit full script paths establish ownership, even after a move.
        scripts = (root + "/program/supervisor.py", root + "/supervisor.py",
                   root + "/program/agentos-daemon.py", root + "/agentos-daemon.py")
        if any(re.search(re.escape(script) + r'(?=["\s]|$)', command) for script in scripts):
            owned.add(pid)
        elif executable.startswith(root + "/.venv/") and (
                re.search(r"(?:^|\s)(?:supervisor\.py|agentos-daemon\.py)(?:\s|$)", command)
                or ("uvicorn" in command and "app.main:app" in command)):
            owned.add(pid)
    while True:
        children = {pid for pid, p in processes.items() if int(p["ParentProcessId"]) in owned}
        expanded = owned | children
        if expanded == owned:
            return owned
        owned = expanded


def service_pids(inventory, ports):
    return {int(p["OwningProcess"]) for p in inventory["listeners"] if int(p["LocalPort"]) in ports}


def ports_open(ports):
    for port in ports:
        with socket.socket() as sock:
            sock.settimeout(.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
    return False


def own_status(state, owned):
    if not state:
        return False
    project = str(PROJECT).replace("\\", "/").lower().rstrip("/")
    reported = str(state.get("project_root", "")).replace("\\", "/").lower().rstrip("/")
    pid = state.get(state.get("active"), {}).get("runtime", {}).get("pid")
    return reported == project and pid in owned


def stop_owned(inventory, owned):
    processes = {int(p["ProcessId"]): p for p in inventory["processes"]}
    # Kill each root once. Parent identities come from the same inventory.
    roots = [pid for pid in owned if int(processes[pid]["ParentProcessId"]) not in owned]
    for pid in roots:
        # Recheck identity immediately before taskkill to avoid stale PID reuse.
        current = windows_inventory()
        if pid not in owned_processes(current):
            continue
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)


@contextmanager
def launch_lock(path):
    import msvcrt
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def probe(base):
    try:
        with httpx.Client(timeout=4, trust_env=False) as client:
            response = client.get(base + "/v1/supervisor/status")
            response.raise_for_status()
            state = response.json()
            if state.get("mode") != "drain-before-switch":
                return None
            if not state.get(state.get("active"), {}).get("ready"):
                return None
            return state
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def start_child(script, log_name, arguments=()):
    logs = PROJECT / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / log_name).open("ab") as log:
        return subprocess.Popen([sys.executable, "-u", "-X", "utf8", str(PROGRAM / script), *arguments],
                                cwd=PROGRAM, env=os.environ.copy(), stdout=log, stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)


def wait_ready(base, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = probe(base)
        if state and state.get(state.get("standby"), {}).get("ready"):
            return state
        time.sleep(.5)
    raise RuntimeError("服务启动未通过检查。请查看 logs/gateway.log 和 logs/backend-*.log。")


def daemon_online(base):
    try:
        with httpx.Client(timeout=3, trust_env=False, headers={"Authorization": "Bearer local"}) as client:
            response = client.get(base + "/v1/local/devices")
            response.raise_for_status()
            return response.json().get("count", 0) > 0
    except (httpx.HTTPError, ValueError):
        return False


def ensure_daemon(base):
    inventory = windows_inventory()
    owned = owned_processes(inventory)
    daemons = {int(p["ProcessId"]) for p in inventory["processes"]
               if int(p["ProcessId"]) in owned and "agentos-daemon.py" in (p.get("CommandLine") or "")}
    parents = {int(p["ProcessId"]): int(p["ParentProcessId"]) for p in inventory["processes"]}
    roots = {pid for pid in daemons if parents[pid] not in daemons}
    if len(roots) <= 1 and daemon_online(base):
        print("本机执行程序已连接。")
        return
    if daemons:
        stop_owned(inventory, daemons)
    print("正在连接本机执行程序…")
    child = start_child("agentos-daemon.py", "daemon.log", ("--server", base, "--token", "local"))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if daemon_online(base):
            return
        if child.poll() is not None:
            break
        time.sleep(.4)
    raise RuntimeError("网页服务已就绪，但本机执行程序连接失败。请查看 logs/daemon.log；再次点击 Start 可重试。")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--stop", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise RuntimeError("此启动入口用于 Windows。")
    os.chdir(PROGRAM)
    data, ports = settings()
    base = f"http://127.0.0.1:{ports[0]}"
    with launch_lock(data / "launcher.lock") as acquired:
        if not acquired:
            print("另一个 Start 正在启动服务，请稍候…")
            wait_ready(base)
            if not args.no_browser and not args.stop:
                webbrowser.open(base + "/ui/")
            return 0
        inventory = windows_inventory()
        owned = owned_processes(inventory)
        listeners = service_pids(inventory, ports)
        current = probe(base)
        # Some Windows network providers report an owner of 0 or omit a
        # listener. Correlate the application's project identity and live
        # backend PID instead of treating an unknown owner as another app.
        unknown = listeners - owned
        if unknown and not (unknown == {0} and own_status(current, owned)):
            raise RuntimeError("服务端口被其他程序占用；已保留该程序，请关闭占用后重试。")
        if args.stop:
            stop_owned(inventory, owned)
            print("本项目后台程序已停止。")
            return 0
        from supervisor import fingerprint
        revision = fingerprint()
        if current and ((current.get("running_revision") == revision and
                        current.get(current.get("standby"), {}).get("ready")) or
                        current.get(current["active"], {}).get("runtime", {}).get("workload_busy") or
                        current.get("maintenance") or (current.get("release") and
                        current["release"]["phase"] not in ("completed", "failed", "rolled_back"))):
            print("后台服务正常，使用现有实例。")
            if current.get("running_revision") != revision:
                print("当前任务或发布尚未结束。已保留正在运行的服务，结束后再次点击 Start 即可加载更新。")
        else:
            if owned:
                print("正在结束本项目的旧后台程序…")
                stop_owned(inventory, owned)
                deadline = time.monotonic() + 12
                while ports_open(ports):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("旧服务尚未释放端口，请稍后再次点击 Start。")
                    time.sleep(.3)
            print("正在启动网页服务…")
            start_child("supervisor.py", "gateway.log")
            current = wait_ready(base)
        ensure_daemon(base)
        print("已就绪：" + base + "/ui/")
        if not args.no_browser:
            if not webbrowser.open(base + "/ui/"):
                print("浏览器未自动打开，请访问上面的地址。")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("启动失败：" + str(exc))
        raise SystemExit(1)
