"""AgentOS Windows Native Lightweight Daemon (Python Implementation).

替代笨重的桌面 exe 客户端，零黑盒的本地执行守护进程。
支持安全执行本地命令 (local_exec)、文件读写 (local_read/local_write)、
目录扫描 (local_list)、系统快照 (local_info) 以及屏幕截图 (local_screenshot)。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import platform
import subprocess
import sys
import time
from typing import Any

import websockets

DEFAULT_SERVER = "http://127.0.0.1:8077"
DEFAULT_TOKEN = "daemon_windows_local_token_sec99"


def get_system_snapshot() -> dict[str, Any]:
    return {
        "hostname": platform.node(),
        "username": os.getlogin() if hasattr(os, "getlogin") else os.environ.get("USERNAME", ""),
        "platform": "Windows",
        "os_name": platform.system(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "processor": platform.processor(),
        "cores": os.cpu_count() or 1,
        "python_version": sys.version.split()[0],
        "cwd": os.getcwd(),
    }


def capture_screenshot() -> dict[str, Any]:
    try:
        from PIL import ImageGrab
        im = ImageGrab.grab()
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        raw_bytes = buf.getvalue()
        return {
            "ok": True,
            "bytes": len(raw_bytes),
            "data_b64": base64.b64encode(raw_bytes).decode("ascii"),
            "width": im.width,
            "height": im.height,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Screenshot failed: {exc}"}


def execute_tool(tool: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
    """执行具体工具，返回 (ok, result, optional_artifact)"""
    if tool == "local_info":
        return True, get_system_snapshot(), None

    if tool == "local_exec":
        cmd = str(args.get("cmd") or "").strip()
        cwd = args.get("cwd") or None
        timeout = int(args.get("timeout_s") or 60)
        if not cmd:
            return False, {"error": "cmd is required"}, None

        print(f"[EXEC] > {cmd}")
        t0 = time.monotonic()
        try:
            p = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            return (
                p.returncode == 0,
                {
                    "stdout": p.stdout,
                    "stderr": p.stderr,
                    "exit_code": p.returncode,
                    "duration_ms": duration_ms,
                },
                None,
            )
        except subprocess.TimeoutExpired:
            return False, {"error": f"Command timed out after {timeout}s"}, None
        except Exception as exc:  # noqa: BLE001
            return False, {"error": str(exc)}, None

    if tool == "local_list":
        path = str(args.get("path") or ".")
        if not os.path.exists(path):
            return False, {"error": f"Path not found: {path}"}, None
        items = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    stat = entry.stat()
                    items.append({
                        "name": entry.name,
                        "type": "dir" if entry.is_dir() else "file",
                        "bytes": stat.st_size if entry.is_file() else 0,
                        "modified": stat.st_mtime,
                    })
            return True, {"items": items[:200]}, None
        except Exception as exc:  # noqa: BLE001
            return False, {"error": str(exc)}, None

    if tool == "local_read":
        path = str(args.get("path") or "")
        max_bytes = int(args.get("max_bytes") or 200_000)
        if not os.path.exists(path):
            return False, {"error": f"File not found: {path}"}, None
        try:
            with open(path, "rb") as f:
                data = f.read(max_bytes)
            content = data.decode("utf-8", errors="replace")
            return True, {"path": path, "content": content, "bytes": len(data)}, None
        except Exception as exc:  # noqa: BLE001
            return False, {"error": str(exc)}, None

    if tool == "local_write":
        path = str(args.get("path") or "")
        content = str(args.get("content") or "")
        if not path:
            return False, {"error": "path is required"}, None
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True, {"path": path, "bytes": len(content.encode("utf-8"))}, None
        except Exception as exc:  # noqa: BLE001
            return False, {"error": str(exc)}, None

    if tool == "local_screenshot":
        shot = capture_screenshot()
        if not shot.get("ok"):
            return False, {"error": shot.get("error")}, None
        artifact = {
            "name": f"screen-{time.strftime('%Y%m%d-%H%M%S')}.jpg",
            "kind": "image/jpeg",
            "bytes": shot["bytes"],
            "data_b64": shot["data_b64"],
        }
        return True, {"width": shot["width"], "height": shot["height"], "bytes": shot["bytes"]}, artifact

    return False, {"error": f"Unknown tool: {tool}"}, None


async def run_daemon(server_url: str, token: str) -> None:
    clean_url = server_url.rstrip("/")
    if clean_url.startswith("https://"):
        ws_base = "wss://" + clean_url[8:]
    elif clean_url.startswith("http://"):
        ws_base = "ws://" + clean_url[7:]
    else:
        ws_base = "ws://" + clean_url
    ws_url = f"{ws_base}/v1/device?token={token}"

    print(f"[*] Target Server: {server_url}")
    print(f"[*] Connecting to: {ws_url} ...")

    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                print("[OK] Connected to AgentOS Server successfully!")

                # 1. 注册声明本机能力
                hello = {
                    "t": "hello",
                    "platform": "Windows",
                    "caps": ["exec", "fs", "info", "screenshot"],
                    "version": "1.0.0-daemon",
                }
                await ws.send(json.dumps(hello))
                print("[OK] Capabilities registered: exec, fs, info, screenshot")

                # 2. 消息接收与执行
                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                    except Exception:
                        continue

                    mtype = msg.get("t")
                    if mtype in ("hello_ack", "pong"):
                        continue

                    if mtype == "tool_call":
                        call_id = msg.get("call_id") or ""
                        tool = msg.get("tool") or ""
                        tool_args = msg.get("args") or {}
                        print(f"[TASK] Received tool_call: {tool} (id: {call_id})")

                        ok, result, artifact = execute_tool(tool, tool_args)

                        # 如果有文件产物（如截屏），先推 artifact
                        if artifact:
                            art_msg = {
                                "t": "artifact",
                                "call_id": call_id,
                                "name": artifact["name"],
                                "kind": artifact["kind"],
                                "bytes": artifact["bytes"],
                                "data_b64": artifact["data_b64"],
                            }
                            await ws.send(json.dumps(art_msg))

                        # 推送执行结果
                        res_msg = {
                            "t": "tool_result",
                            "call_id": call_id,
                            "ok": ok,
                            "result": result if ok else None,
                            "error": result.get("error") if not ok else None,
                        }
                        await ws.send(json.dumps(res_msg))
                        print(f"[DONE] Replied tool_call: {tool} (ok: {ok})")

        except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as exc:
            print(f"[WARN] Connection lost ({type(exc).__name__}: {exc}), reconnecting in 5s...")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(5)
        except Exception as exc:
            print(f"[ERROR] Unexpected exception ({type(exc).__name__}: {exc}), reconnecting in 5s...")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentOS Windows Native Daemon")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="AgentOS Server URL")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Device token")
    args = parser.parse_args()

    # 配置日志记录到文件和控制台
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "daemon.log")

    class Tee:
        def __init__(self, filename, stream):
            self.file = open(filename, "a", encoding="utf-8", buffering=1)
            self.stream = stream
        def write(self, data):
            if self.stream:
                try: self.stream.write(data)
                except Exception: pass
            self.file.write(data)
        def flush(self):
            if self.stream:
                try: self.stream.flush()
                except Exception: pass
            self.file.flush()

    sys.stdout = Tee(log_file, sys.stdout)
    sys.stderr = Tee(log_file, sys.stderr)

    print("=" * 60)
    print("      AgentOS Windows Lightweight Daemon (Python)       ")
    print(f"[*] Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    try:
        asyncio.run(run_daemon(args.server, args.token))
    except KeyboardInterrupt:
        print("\n[INFO] Daemon stopped by user.")


if __name__ == "__main__":
    main()
