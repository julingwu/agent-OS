"""远程云主机桌面（容器内 :0 屏幕）的采集与交互节点。
在 Windows 本地模式或未安装 ffmpeg 环境下自适应降级为原生占位画面。
"""

from __future__ import annotations

import io
import os
import re
import shutil
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

    if os.name == "nt":
        try:
            import ctypes
            w = ctypes.windll.user32.GetSystemMetrics(0)
            h = ctypes.windll.user32.GetSystemMetrics(1)
            if w > 0 and h > 0:
                with _geo_lock:
                    _geo_cache = (w, h)
                    return _geo_cache
        except Exception:
            pass
        return DEFAULT_GEOMETRY

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
    """抓一帧并压成 JPEG。在 Windows 或缺少 ffmpeg 时平滑输出占位帧，避免 WinError 2。"""
    w, h = geometry()
    target_w = max(320, min(int(max_w), w))
    target_h = max(200, int(h * target_w / w))

    # Windows 或未安装 ffmpeg 环境下的平滑兼容
    if os.name == "nt" or not shutil.which("ffmpeg"):
        try:
            from PIL import Image, ImageDraw
            im = Image.new("RGB", (target_w, target_h), (15, 23, 42))
            draw = ImageDraw.Draw(im)
            draw.rectangle([(8, 8), (target_w - 8, target_h - 8)], outline=(51, 65, 85), width=2)
            msg = "AgentOS Local Windows Workspace (Active)"
            draw.text((max(20, target_w // 2 - 140), max(20, target_h // 2 - 10)), msg, fill=(148, 163, 184))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=80)
            return buf.getvalue()
        except Exception as exc:
            raise CaptureUnavailable(f"local frame fallback failed: {exc}") from exc

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


# ---------------------------------------------------------------- 桌面控制
def desktop_active() -> bool:
    if os.name == "nt":
        return True
    try:
        out = subprocess.run(["systemctl", "is-active", "lightdm"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def _systemctl(action: str) -> None:
    if os.name == "nt":
        return
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
    if os.name == "nt":
        return
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
