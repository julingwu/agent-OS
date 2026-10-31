"""电脑（宿主机 `:0` 桌面）的采集层。

为什么不用 VNC 做主通道：5Mbps 带宽下 VNC 全时推流会卡；
预览用"按需截帧 JPEG"，只有在需要人接管时才升级到 VNC。

依赖：
  * Linux: ffmpeg / xdpyinfo（provision 脚本已装）
  * Windows / 无头环境: 原生 ctypes 分辨率探测 + PIL ImageGrab / AgentOS 平滑占位帧
显示归属：lightdm 自动登录的 oaagent 会话，Xauthority 在 /var/run/lightdm/root/:0。
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import threading
from typing import Any

from PIL import Image, ImageDraw, ImageGrab

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

    # Windows 原生分辨率探测
    if os.name == "nt":
        try:
            import ctypes
            w = ctypes.windll.user32.GetSystemMetrics(0)
            h = ctypes.windll.user32.GetSystemMetrics(1)
            if w > 0 and h > 0:
                with _geo_lock:
                    _geo_cache = (int(w), int(h))
                    return _geo_cache
        except Exception:
            pass

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


def _generate_placeholder_frame(w: int, h: int, target_w: int) -> bytes:
    """绘制 AgentOS 暗黑工作区平滑占位帧（15, 23, 42 科技蓝灰），绝不抛出 ffmpeg 异常。"""
    target_h = int(h * (target_w / w)) if w else 540
    img = Image.new("RGB", (target_w, target_h), color=(15, 23, 42))
    draw = ImageDraw.Draw(img)
    text = "AgentOS Virtual Screen (Headless / Standby)"
    draw.text((max(20, target_w // 2 - 140), max(20, target_h // 2 - 10)), text, fill=(100, 116, 139))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def grab_jpeg(max_w: int = 1280, quality: int = 5) -> bytes:
    """抓一帧并压成 JPEG。优先原生截图，异常安全降级，绝不抛出 ffmpeg 缺失异常。"""
    w, h = geometry()
    target_w = max(320, min(int(max_w), w))

    # 截帧优先级 1: Windows 环境优先尝试 PIL.ImageGrab.grab()
    if os.name == "nt":
        try:
            img = ImageGrab.grab()
            if img:
                gw, gh = img.size
                t_w = max(320, min(int(max_w), gw))
                if t_w < gw:
                    t_h = int(gh * (t_w / gw))
                    img = img.resize((t_w, t_h), Image.Resampling.LANCZOS)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                buf = io.BytesIO()
                pil_quality = 80 if quality <= 20 else int(quality)
                img.save(buf, format="JPEG", quality=pil_quality)
                return buf.getvalue()
        except Exception:
            pass

    # 截帧优先级 2: Linux 环境且 ffmpeg 与 DISPLAY 存在，走原 ffmpeg x11grab
    if os.name != "nt" and shutil.which("ffmpeg") and os.environ.get("DISPLAY"):
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
            if out.returncode == 0 and out.stdout:
                return out.stdout
        except (OSError, subprocess.SubprocessError):
            pass

    # 截帧优先级 3: 异常降级兜底，绘制科技蓝灰暗黑工作区平滑占位帧
    try:
        return _generate_placeholder_frame(w, h, target_w)
    except Exception as exc:
        raise CaptureUnavailable(f"screen capture failed: {exc}") from exc


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
