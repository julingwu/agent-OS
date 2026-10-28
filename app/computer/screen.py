"""电脑（宿主机 `:0` 桌面）的采集层。

为什么不用 VNC 做主通道：5Mbps 带宽下 VNC 全时推流会卡；
预览用"按需截帧 JPEG"，只有在需要人接管时才升级到 VNC。

依赖：ffmpeg / xdpyinfo（provision 脚本已装）。
显示归属：lightdm 自动登录的 oaagent 会话，Xauthority 在 /var/run/lightdm/root/:0。
"""

from __future__ import annotations

import os
import re
import subprocess
import threading

DISPLAY = os.environ.get("COMPUTER_DISPLAY", ":0")
XAUTHORITY = os.environ.get("COMPUTER_XAUTHORITY", "/var/run/lightdm/root/:0")
DEFAULT_GEOMETRY = (1600, 900)

_geo_lock = threading.Lock()
_geo_cache: tuple[int, int] | None = None


class CaptureUnavailable(RuntimeError):
    """x11grab 取不到画面（桌面没起 / 权限不对 / ffmpeg 缺失）。"""


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    env["XAUTHORITY"] = XAUTHORITY
    return env


def parse_geometry(stdout: str) -> tuple[int, int] | None:
    m = re.search(r"dimensions:\s+(\d+)x(\d+)", stdout or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def geometry(*, refresh: bool = False) -> tuple[int, int]:
    """当前桌面分辨率（缓存；失败回退 1600x900）。"""
    global _geo_cache
    with _geo_lock:
        if _geo_cache and not refresh:
            return _geo_cache
    try:
        out = subprocess.run(
            ["xdpyinfo", "-display", DISPLAY],
            env=_env(), capture_output=True, text=True, timeout=5,
        )
        parsed = parse_geometry(out.stdout)
    except (OSError, subprocess.SubprocessError):
        parsed = None
    with _geo_lock:
        _geo_cache = parsed or DEFAULT_GEOMETRY
        return _geo_cache


def grab_jpeg(max_w: int = 1280, quality: int = 5) -> bytes:
    """抓一帧并压成 JPEG。失败抛 CaptureUnavailable（由上层降级）。"""
    w, h = geometry()
    target_w = max(320, min(int(max_w), w))
    scale = f"scale={target_w}:-2:flags=fast_bilinear"
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-f", "x11grab", "-video_size", f"{w}x{h}", "-i", DISPLAY,
        "-frames:v", "1", "-vf", scale,
        "-q:v", str(max(2, min(int(quality), 20))),
        "-f", "mjpeg", "pipe:1",
    ]
    try:
        out = subprocess.run(cmd, env=_env(), capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CaptureUnavailable(str(exc)) from exc
    if out.returncode != 0 or not out.stdout:
        detail = (out.stderr or b"").decode("utf-8", "replace")[:400]
        raise CaptureUnavailable(detail or "ffmpeg produced no frame")
    return out.stdout


def probe() -> dict:
    """电脑状态（给 /v1/computer 用），不抛异常。"""
    w, h = geometry()
    try:
        jpg = grab_jpeg(max_w=480, quality=8)
        ok, size = True, len(jpg)
    except CaptureUnavailable as exc:
        ok, size = False, 0
        err = str(exc)[:200]
    else:
        err = ""
    return {
        "display": DISPLAY,
        "geometry": {"w": w, "h": h},
        "capture_ok": ok,
        "frame_bytes": size,
        "error": err,
        "awake": desktop_active(),
    }


# ---------------------------------------------------------------- 桌面控制（接管）
def desktop_active() -> bool:
    try:
        out = subprocess.run(["systemctl", "is-active", "lightdm"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def _systemctl(action: str) -> None:
    try:
        out = subprocess.run(["systemctl", action, "lightdm"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CaptureUnavailable(str(exc)) from exc
    if out.returncode != 0:
        raise CaptureUnavailable((out.stderr or out.stdout or "").strip()[:300])


def wake() -> None:
    """唤醒桌面（启动 lightdm，`:0` 重新可用）。"""
    _systemctl("start")


def sleep() -> None:
    """休眠桌面（停 lightdm，保留文件与会话数据）。"""
    _systemctl("stop")


def xdo(*args: str) -> None:
    """在 `:0` 上执行 xdotool 动作（真实接管）。"""
    try:
        out = subprocess.run(["xdotool", *args], env=_env(),
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CaptureUnavailable(f"xdotool failed: {exc}") from exc
    if out.returncode != 0:
        raise CaptureUnavailable((out.stderr or "").strip()[:300] or "xdotool error")


def input_click(x: int, y: int, button: int = 1) -> None:
    xdo("mousemove", str(int(x)), str(int(y)), "click", str(int(button)))


def input_move(x: int, y: int) -> None:
    xdo("mousemove", str(int(x)), str(int(y)))


def input_type(text: str) -> None:
    xdo("type", "--delay", "12", text)


def input_key(keys: str) -> None:
    xdo("key", keys)


def input_drag(x1: int, y1: int, x2: int, y2: int) -> None:
    xdo("mousemove", str(int(x1)), str(int(y1)), "mousedown", "1")
    xdo("mousemove", str(int(x2)), str(int(y2)))
    xdo("mouseup", "1")
