"""给目录打 Low 完整性标签 —— 走 Win32 原生 API，不调用 icacls，不触发 UAC。

原理
----
强制完整性标签存在对象 SACL 里的一条 ACE（SYSTEM_MANDATORY_LABEL_ACE）：
    S:(ML;OICI;NW;;;LW)
      ML = mandatory label，OICI = 对象/容器继承，NW = NO_WRITE_UP，
      LW = S-1-16-4096（Low）
用 `ConvertStringSecurityDescriptorToSecurityDescriptorW` 造 SD，
再 `SetNamedSecurityInfoW(..., LABEL_SECURITY_INFORMATION, sd)` 写回对象。
对**自己创建**的目录，写标签只需要 WRITE_OWNER，不需要管理员。

如果第一条路被拒（1314 PRIVILEGE_NOT_HELD），退到
`SetFileInformationByHandle(FileLabelInfo)`（Vista+ 的另一个入口）。

两条都失败才回落到 icacls（此时才可能需要提权）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import pathlib

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SDDL_REVISION_1 = 1
LABEL_SECURITY_INFORMATION = 0x00000010
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x00000004

# SDDL: 只带一条强制标签 ACE（无 DACL/Owner），Low + 继承 + no-write-up
LOW_LABEL_SDDL = "S:(ML;OICI;NW;;;LW)"

advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wt.LPCWSTR, wt.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.DWORD)]
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wt.BOOL
advapi32.SetNamedSecurityInfoW.argtypes = [
    wt.LPWSTR, ctypes.c_int, wt.DWORD,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
advapi32.SetNamedSecurityInfoW.restype = wt.DWORD
kernel32.LocalFree.argtypes = [ctypes.c_void_p]


class FILE_OBJECT_LABEL(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Label", ctypes.c_void_p)]


def _make_sd() -> ctypes.c_void_p:
    sd = ctypes.c_void_p()
    size = wt.DWORD()
    ok = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        LOW_LABEL_SDDL, SDDL_REVISION_1, ctypes.byref(sd), ctypes.byref(size))
    if not ok:
        raise OSError(ctypes.get_last_error(), "ConvertStringSecurityDescriptor failed")
    return sd


def set_low_named(path: pathlib.Path) -> int:
    """SetNamedSecurityInfoW + LABEL_SECURITY_INFORMATION。返回 winerror（0=成功）。"""
    sd = _make_sd()
    try:
        rc = advapi32.SetNamedSecurityInfoW(
            str(path), SE_FILE_OBJECT, LABEL_SECURITY_INFORMATION,
            None, None, None, None, sd)
        return int(rc)
    finally:
        kernel32.LocalFree(sd)


def set_low_handle(path: pathlib.Path) -> int:
    """SetFileInformationByHandle(FileLabelInfo=17) —— 另一条免提权入口。"""
    FILE_READ_ATTRIBUTES = 0x0080
    FILE_WRITE_ATTRIBUTES = 0x0100
    FILE_SHARE_ALL = 0x7
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FileLabelInfo = 17

    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, wt.HANDLE]
    CreateFileW.restype = wt.HANDLE

    h = CreateFileW(str(path), FILE_READ_ATTRIBUTES | FILE_WRITE_ATTRIBUTES,
                    FILE_SHARE_ALL, None, OPEN_EXISTING,
                    FILE_FLAG_BACKUP_SEMANTICS, None)
    if h == wt.HANDLE(-1).value or h == -1:
        return ctypes.get_last_error()
    try:
        # SID 指针：S-1-16-4096
        sid = ctypes.c_void_p()
        advapi32.ConvertStringSidToSidW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        if not advapi32.ConvertStringSidToSidW("S-1-16-4096", ctypes.byref(sid)):
            return ctypes.get_last_error()
        try:
            label = FILE_OBJECT_LABEL(Sid=sid, Label=None)
            ok = kernel32.SetFileInformationByHandle(
                h, FileLabelInfo, ctypes.byref(label), ctypes.sizeof(label))
            return 0 if ok else ctypes.get_last_error()
        finally:
            kernel32.LocalFree(sid)
    finally:
        kernel32.CloseHandle(h)


def is_low(path: pathlib.Path) -> bool:
    """用 GetNamedSecurityInfo 读回标签，避免依赖 icacls 文本。"""
    sd = ctypes.c_void_p()
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wt.LPWSTR, ctypes.c_int, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetNamedSecurityInfoW.restype = wt.DWORD
    rc = advapi32.GetNamedSecurityInfoW(str(path), SE_FILE_OBJECT,
                                        LABEL_SECURITY_INFORMATION,
                                        None, None, None, None, ctypes.byref(sd))
    if rc != 0 or not sd:
        return False
    try:
        advapi32.GetSecurityDescriptorSacl.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wt.BOOL), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wt.BOOL)]
        present = wt.BOOL()
        sacl = ctypes.c_void_p()
        defaulted = wt.BOOL()
        if not advapi32.GetSecurityDescriptorSacl(sd, ctypes.byref(present),
                                                  ctypes.byref(sacl), ctypes.byref(defaulted)):
            return False
        if not present.value or not sacl:
            return False
        advapi32.GetAce.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        advapi32.GetAce.restype = wt.BOOL
        advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wt.DWORD]
        advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wt.DWORD)

        ace = ctypes.c_void_p()
        if not advapi32.GetAce(sacl, 0, ctypes.byref(ace)):
            return False
        # ACE_HEADER(4) + Mask(4) + SID 起点在偏移 8
        sid_ptr = ctypes.c_void_p(ace.value + 8)
        cnt = advapi32.GetSidSubAuthorityCount(sid_ptr).contents.value
        if cnt < 1:
            return False
        last = advapi32.GetSidSubAuthority(sid_ptr, cnt - 1).contents.value
        return last == 4096                      # SECURITY_MANDATORY_LOW_RID
    finally:
        kernel32.LocalFree(sd)


def apply_low(path: pathlib.Path) -> tuple[bool, str]:
    """先原生、再句柄、最后 icacls；返回 (成功?, 用了哪条路)。"""
    path = pathlib.Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if is_low(path):
        return True, "already"

    rc = set_low_named(path)
    if rc == 0 and is_low(path):
        return True, "native:SetNamedSecurityInfo"
    first = rc

    rc2 = set_low_handle(path)
    if rc2 == 0 and is_low(path):
        return True, "native:SetFileInformationByHandle"
    second = rc2

    import subprocess
    p = subprocess.run(["icacls", str(path), "/setintegritylevel", "(OI)(CI)L"],
                       capture_output=True)
    if p.returncode == 0 and is_low(path):
        return True, "icacls"
    return False, f"failed(named={first}, handle={second}, icacls={p.returncode})"


if __name__ == "__main__":
    import sys
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "data/lowil-probe")
    # 清理，保证测的是「新建目录」
    import shutil
    shutil.rmtree(target, ignore_errors=True)
    ok, how = apply_low(target)
    print(f"{target}: ok={ok} via={how}")
    print("owns:", os.stat(target).st_uid if ok else "-")
