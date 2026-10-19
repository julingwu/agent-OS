"""Run a child process at Low integrity so it can only write inside its own dir.

How the containment actually works (Windows mandatory integrity control):

  1. The task directory is labelled Low (`icacls <dir> /setintegritylevel Low`).
     Low-integrity processes may write there.
  2. This launcher lowers *its own* token to Low integrity before spawning.
     Lowering is always permitted; the child inherits the token, so the whole
     subtree runs at Low.
  3. Default policy is "no write up": a Low process cannot write to Medium
     objects — which is everything else on the user's disk. Reads are still
     allowed (read down), so tools keep working.

Why not a restricted token or a Job Object: a Job Object does not restrict
paths at all, and a restricted token needs the same user SID tricks without
adding write protection. Integrity levels are the mechanism Chrome uses for its
renderers, and they need no admin rights.

Usage:
    python tools/sandbox_win.py --cwd <task_dir> -- <command> [args...]
    python tools/sandbox_win.py --selftest
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import pathlib
import shutil
import subprocess
import sys

LOW_IL_SID = "S-1-16-4096"          # SECURITY_MANDATORY_LOW_RID
TokenIntegrityLevel = 25
TOKEN_ADJUST_DEFAULT = 0x0080
TOKEN_QUERY = 0x0008

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

# ctypes truncates pseudo-handles to 32 bits unless the signatures are declared.
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.GetCurrentProcess.argtypes = []
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi32.OpenProcessToken.restype = wt.BOOL
advapi32.ConvertStringSidToSidW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
advapi32.ConvertStringSidToSidW.restype = wt.BOOL
advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
advapi32.GetLengthSid.restype = wt.DWORD
advapi32.SetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
advapi32.SetTokenInformation.restype = wt.BOOL


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD)]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


def _check(ok: int, what: str) -> None:
    if not ok:
        err = ctypes.get_last_error()
        raise OSError(err, f"{what} failed (winerror {err}): {ctypes.FormatError(err)}")


def lower_own_integrity(sid_string: str = LOW_IL_SID) -> None:
    """Set this process's integrity level down to Low."""
    token = wt.HANDLE()
    _check(advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     TOKEN_ADJUST_DEFAULT | TOKEN_QUERY,
                                     ctypes.byref(token)), "OpenProcessToken")
    try:
        psid = ctypes.c_void_p()
        _check(advapi32.ConvertStringSidToSidW(ctypes.c_wchar_p(sid_string),
                                               ctypes.byref(psid)),
               "ConvertStringSidToSid")
        try:
            label = TOKEN_MANDATORY_LABEL()
            label.Label.Sid = psid
            label.Label.Attributes = 0x20      # SE_GROUP_INTEGRITY
            size_of_sid = advapi32.GetLengthSid(psid)
            _check(advapi32.SetTokenInformation(
                token, TokenIntegrityLevel, ctypes.byref(label),
                ctypes.sizeof(TOKEN_MANDATORY_LABEL) + int(size_of_sid)),
                "SetTokenInformation(TokenIntegrityLevel)")
        finally:
            kernel32.LocalFree(psid)
    finally:
        kernel32.CloseHandle(token)


def _is_low(path: pathlib.Path) -> bool:
    proc = subprocess.run(["icacls", str(path)], capture_output=True)
    text = (proc.stdout or b"").decode("mbcs", errors="replace")
    return "Low Mandatory Level" in text or "低强制级别" in text or "S-1-16-4096" in text


def set_dir_low(path: pathlib.Path) -> None:
    """Label a directory Low (inheritable) — **never** raises a UAC prompt.

    顺序：
      1. 已经是 Low 就返回（子目录靠 (OI)(CI) 继承，无需再打）。
      2. Win32 原生 API（SetNamedSecurityInfo + LABEL_SECURITY_INFORMATION）。
      3. icacls 静默调用（用户自己是 owner 的目录不会触发提权）。
    都能不成功时**大声失败**，绝不退到 `Start-Process -Verb RunAs` ——
    那条路会弹 UAC，董事长已经投诉过。
    """
    path.mkdir(parents=True, exist_ok=True)
    if _is_low(path):
        return

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    try:
        from app.engine import lowil
        if lowil.apply_low(path)[0]:
            return
    except Exception:                      # noqa: BLE001  原生路径不可用时继续
        pass

    if _is_low(path):
        return
    raise RuntimeError(
        f"cannot label {path} as Low without elevation; "
        "move the workspace under %LOCALAPPDATA% (owner is the current user)")

def sandbox_env(cwd: pathlib.Path) -> dict[str, str]:
    """Point every scratch location into the task dir; nothing else is writable."""
    env = dict(os.environ)
    for name in ("TEMP", "TMP", "TMPDIR"):
        env[name] = str(cwd / ".tmp")
    env["DSH_HOME"] = str(cwd / ".dsh")
    env["HOME"] = str(cwd)
    env["USERPROFILE"] = str(cwd)
    env["M0_SANDBOX"] = "lowil"
    (cwd / ".tmp").mkdir(parents=True, exist_ok=True)
    (cwd / ".dsh").mkdir(parents=True, exist_ok=True)
    set_dir_low(cwd / ".tmp")
    set_dir_low(cwd / ".dsh")
    return env


def _resolve(cmd: list[str], env: dict) -> list[str]:
    """npm/npx 是 .cmd，CreateProcess 不会自动解析 —— 交给 cmd.exe。"""
    if not cmd:
        return cmd
    exe = cmd[0]
    if os.path.splitext(exe)[1]:
        return cmd
    for ext in (".exe", ".cmd", ".bat"):
        found = shutil.which(exe + ext, path=env.get("PATH", ""))
        if found:
            if found.lower().endswith((".cmd", ".bat")):
                return [os.environ.get("COMSPEC", "cmd.exe"), "/c", found, *cmd[1:]]
            return [found, *cmd[1:]]
    return cmd


def run(cwd: pathlib.Path, cmd: list[str]) -> int:
    set_dir_low(cwd)
    env = sandbox_env(cwd)
    cmd = _resolve(cmd, env)
    lower_own_integrity()
    proc = subprocess.run(cmd, cwd=str(cwd), env=env)
    return proc.returncode


def selftest() -> int:
    root = pathlib.Path(os.environ.get("M0_SANDBOX_TEST_DIR")
                        or pathlib.Path(__file__).resolve().parents[1] / "data" / "sandbox-selftest")
    task = root / "task"
    for p in root.rglob("*"):
        if p.is_file():
            p.unlink()
    task.mkdir(parents=True, exist_ok=True)
    (root / "escape.txt").unlink(missing_ok=True)

    code = (
        "import pathlib,sys\n"
        "try:\n"
        "    pathlib.Path('inside.txt').write_text('ok', encoding='utf-8')\n"
        "    print('INSIDE_OK')\n"
        "except OSError as e:\n"
        "    print('INSIDE_FAIL', e); sys.exit(3)\n"
        "try:\n"
        "    pathlib.Path('..', 'escape.txt').write_text('nope', encoding='utf-8')\n"
        "    print('ESCAPE_OK'); sys.exit(4)\n"
        "except OSError as e:\n"
        "    print('ESCAPE_BLOCKED', type(e).__name__, e.winerror if hasattr(e,'winerror') else '')\n"
    )
    rc = run(task, [sys.executable, "-c", code])
    inside = (task / "inside.txt").exists()
    escaped = (root / "escape.txt").exists()
    print(f"rc={rc} inside_written={inside} escaped={escaped} dir={task}")
    return 0 if (rc == 0 and inside and not escaped) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.cwd or not a.cmd:
        ap.error("--cwd and a command are required")
    cmd = [c for c in a.cmd if c != "--"]
    return run(pathlib.Path(a.cwd).resolve(), cmd)


if __name__ == "__main__":
    sys.exit(main())
