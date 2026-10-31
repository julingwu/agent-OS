"""进程 supervisor：把「进程」当一等公民管起来（契约 02 的本地实现，W4）。

设计取舍（第一性原理）：
  * 进程的生命周期独立于客户端连接 / turn / 服务端进程本身（否则关窗口、网络抖动、
    服务端重启都会毁掉长任务）。所以用 Popen 起**独立进程组**（POSIX
    ``start_new_session``，Windows ``CREATE_NEW_PROCESS_GROUP``），输出直接重定向到
    ``workspace/logs/<process_id>.log``：父进程不持有管道，服务端重启带不走它。
  * **DB 是事实源，进程句柄只在内存**。重启后 DB 里仍写 running 但没有句柄的条目
    既不能 poll 也不能杀，由 ``recover_interrupted()`` 统一标成 lost（不静默假装还活着）。
  * 输出**绝不进事件表**：事件只记 ``log_path`` / ``log_bytes``，否则长任务的日志会把
    事件流淹没（契约 02 §2）。
  * 存活判断只用 ``poll()``（内部就 waitpid），每 tick 把退出码收进 DB 并写
    ``process.exited``；绝不留僵尸。
  * tick 由 supervisor 线程定时调（默认 15s）；pytest 里由用例手动调 tick，**不启动
    常驻线程**（同 routines.py 的约定，避免线程跨用例污染别人的临时库）。
  * 建表结果按 ``db.db_path()`` 缓存（测试每个用例一个新库）。

表结构说明：契约 02 的 DDL 已经在 ``app/schema_v1.sql`` 里（``status`` / ``exited_at``
那套，由 ``init_db()`` 建），而本工作包对外用的是 ``state`` / ``ended_at`` /
``session_id`` / ``name`` / ``created_at``。两者同时维护，不推翻已定稿的契约表：

    state("running|exited|failed|stopped|lost")
      <-> status("running|exited|failed|killed|orphaned")

事件 origen 统一 ``system``：进程生命周期是服务端事实，不是模型/用户说的。
"""

from __future__ import annotations

import os
import pathlib
import platform
import secrets
import shlex
import signal
import subprocess
import threading
from datetime import datetime

from . import db
from . import events as ev
from .config import get_workspace_root

IS_WINDOWS = platform.system() == "Windows"

MAX_CMD = 8000
MAX_NAME = 80
DEFAULT_STOP_TIMEOUT = 5.0
DEFAULT_TICK = 15.0
LOG_TAIL_BYTES = 256 * 1024
MAX_LOG_TAIL_LINES = 2000

# 本工作包的状态词表（DB 的 state 列 / API 返回值）
STATES = frozenset({"running", "exited", "failed", "stopped", "lost"})

# state -> 契约 02 的 status 词表（两套字段名同时维护，见模块 docstring）
_STATUS_MIRROR = {
    "running": "running",
    "exited": "exited",
    "failed": "failed",
    "stopped": "killed",
    "lost": "orphaned",
}

# 反向：老行只有 status 时，把契约词表翻译回本工作包的 state 词表
_STATE_ALIASES = {"killed": "stopped", "orphaned": "lost", "timeout": "failed"}

_READY_DBS: set[str] = set()          # 按 DB 路径缓存建表结果（测试每个用例一个新库）
_LOCK = threading.RLock()
_PROCS: dict[str, subprocess.Popen] = {}   # 进程句柄只活在内存（DB 才是事实源）
_TICKER: threading.Thread | None = None
_STOP = threading.Event()

# 契约 02 的 DDL 已由 schema_v1.sql 建好；这里只做**增量补列**，让本工作包的字段
# 也能落库（同 db._migrate 的写法：PRAGMA table_info 对比后再 ALTER）。
_MIGRATIONS = (
    ("session_id", "ALTER TABLE processes ADD COLUMN session_id TEXT"),
    ("name", "ALTER TABLE processes ADD COLUMN name TEXT"),
    ("state", "ALTER TABLE processes ADD COLUMN state TEXT"),
    ("ended_at", "ALTER TABLE processes ADD COLUMN ended_at TEXT"),
    ("created_at", "ALTER TABLE processes ADD COLUMN created_at TEXT"),
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS processes (
    id           TEXT PRIMARY KEY,
    bot_id       TEXT NOT NULL,
    session_id   TEXT,
    name         TEXT,
    cmd          TEXT NOT NULL,
    cwd          TEXT,
    pid          INTEGER,
    pgid         INTEGER,
    state        TEXT NOT NULL,
    status       TEXT NOT NULL,
    background   INTEGER NOT NULL DEFAULT 1,
    exit_code    INTEGER,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    exited_at    TEXT,
    log_path     TEXT NOT NULL,
    log_bytes    INTEGER NOT NULL DEFAULT 0,
    last_tick_at TEXT,
    created_at   TEXT NOT NULL
)
"""


# ---------------------------------------------------------------- 建表 / 迁移
def ensure(conn) -> None:
    """幂等建表 + 补列。按 DB 路径缓存，避免每次请求都摸一遍 schema。"""
    with _LOCK:
        path = db.db_path()
        if path in _READY_DBS:
            return
        conn.execute(_CREATE_TABLE)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(processes)")}
        for column, ddl in _MIGRATIONS:
            if column not in columns:
                conn.execute(ddl)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_processes_state ON processes(state)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_processes_bot"
            " ON processes(bot_id, created_at)")
        # 老行（只有 status）按契约词表翻译回 state；created_at 缺失就拿 started_at 顶上。
        conn.execute(
            "UPDATE processes SET state = CASE status"
            " WHEN 'killed' THEN 'stopped' WHEN 'orphaned' THEN 'lost'"
            " WHEN 'timeout' THEN 'failed' ELSE status END"
            " WHERE (state IS NULL OR state = '') AND status IS NOT NULL")
        conn.execute(
            "UPDATE processes SET created_at = started_at WHERE created_at IS NULL")
        conn.commit()
        _READY_DBS.add(path)


def _state_of(row) -> str:
    value = str(row["state"] or row["status"] or "lost")
    return _STATE_ALIASES.get(value, value)


def _raw(conn, process_id: str):
    return conn.execute("SELECT * FROM processes WHERE id=?", (process_id,)).fetchone()


def _row_to_dict(row) -> dict:
    item = dict(row)
    item["state"] = _state_of(row)
    ended = item.get("ended_at") or item.get("exited_at")
    item["ended_at"] = ended
    item["exited_at"] = ended
    item["running"] = item["state"] == "running"
    item["alive"] = bool(item["running"] and pid_alive(item.get("pid")))
    item["elapsed_s"] = _elapsed_s(item)
    return item


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.astimezone()


def _elapsed_s(item: dict) -> float | None:
    started = _parse_ts(item.get("started_at"))
    if started is None:
        return None
    end = _parse_ts(item.get("ended_at")) or datetime.now().astimezone()
    return round(max(0.0, (end - started).total_seconds()), 3)


# ---------------------------------------------------------------- 进程存活
def pid_alive(pid: int | None) -> bool:
    """pid 还活着吗？跨平台且**只读**（Windows 上绝不碰 TerminateProcess）。

    Windows 的 ``os.kill(pid, 0)`` 实际会 TerminateProcess，会误杀进程，所以用
    OpenProcess + WaitForSingleObject 判存活。
    """
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if IS_WINDOWS:
        return _win_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _win_pid_alive(pid: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:                                     # noqa: BLE001 - 极端环境降级
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x102
        ERROR_ACCESS_DENIED = 5
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
        if not handle:
            # 拒绝访问 = 进程存在但不是我们能打开的；其余（含 87 无此 pid）= 已消失。
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    except Exception:                                     # noqa: BLE001
        return False


# ---------------------------------------------------------------- 命令行解析
def argv_from_cmd(cmd) -> list[str]:
    """把 cmd（str 或 list）变成 argv。字符串按**原生命令行规则**切分。

    Windows 上不能用 shlex.split：它把反斜杠当转义符，``C:\\...\\python.exe`` 会被
    切坏。这里用 shell32.CommandLineToArgvW（Windows 自己解析命令行的函数）。
    """
    if isinstance(cmd, (list, tuple)):
        argv = [str(part) for part in cmd]
    elif isinstance(cmd, str):
        text = cmd.strip()
        if not text:
            raise ValueError("cmd 不能为空")
        argv = _split_command_line(text)
    else:
        raise ValueError("cmd 必须是字符串或字符串列表")
    if not argv:
        raise ValueError("cmd 不能为空")
    if not argv[0].strip():
        raise ValueError("cmd 的第一项（可执行文件）不能为空")
    for token in argv:
        if "\x00" in token:
            raise ValueError("cmd 不允许包含 NUL 字节")
    if len(" ".join(argv)) > MAX_CMD:
        raise ValueError(f"cmd 太长（上限 {MAX_CMD} 字符）")
    return argv


def _split_command_line(text: str) -> list[str]:
    if IS_WINDOWS:
        parsed = _win_argv(text)
        if parsed:
            return parsed
    return shlex.split(text)


def _win_argv(text: str) -> list[str] | None:
    """Windows 原生命令行切分；失败返回 None 让调用方退回 shlex。"""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:                                     # noqa: BLE001
        return None
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        shell32.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR,
                                               ctypes.POINTER(ctypes.c_int))
        kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel32.LocalFree.restype = ctypes.c_void_p
        argc = ctypes.c_int(0)
        argv = shell32.CommandLineToArgvW(text, ctypes.byref(argc))
        if not argv:
            return None
        try:
            return [argv[i] for i in range(argc.value)]
        finally:
            kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))
    except Exception:                                     # noqa: BLE001
        return None


def _command_text(argv: list[str]) -> str:
    """命令的展示/重跑形式（不是拿它去执行，执行走 argv）。"""
    if IS_WINDOWS:
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _resolve_cwd(cwd) -> pathlib.Path:
    if cwd:
        text = str(cwd)
        if "\x00" in text:
            raise ValueError("cwd 不允许包含 NUL 字节")
        candidate = pathlib.Path(os.path.expanduser(text))
        if not candidate.is_absolute():
            candidate = get_workspace_root() / candidate
        candidate = candidate.resolve()
        if not candidate.is_dir():
            raise ValueError(f"cwd 不是已存在的目录：{candidate}")
        return candidate
    return get_workspace_root().resolve()


def log_dir() -> pathlib.Path:
    """进程日志目录：``<workspace>/logs``（父进程不持有管道，写文件才安全）。"""
    target = get_workspace_root() / "logs"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _log_bytes(path: str | None) -> int:
    if not path:
        return 0
    try:
        return pathlib.Path(path).stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------- 事件
def _emit(conn, *, bot_id: str, kind: str, payload: dict,
          session_id: str | None = None) -> None:
    """写事件 -> 提交 -> 广播（顺序不能反：回滚后广播就是幽灵事件）。"""
    event = ev.emit(conn, bot_id=bot_id, session_id=session_id, kind=kind,
                    payload=payload, origin="system")
    conn.commit()
    ev.broadcaster.publish(event)


# ---------------------------------------------------------------- 启动
def start(conn, *, bot_id: str, cmd, session_id: str | None = None,
          name: str | None = None, cwd=None, env: dict[str, str] | None = None,
          log_path: str | None = None) -> dict:
    """起一个**后台**进程并登记。返回进程行（dict）。

    顺序很重要：先 spawn（拿到 pid），再落库 + 发事件；落库失败就把刚起的进程
    杀掉收尸，绝不留下"跑了但没人知道"的进程。
    """
    ensure(conn)
    if not bot_id:
        raise ValueError("bot_id 必填")
    argv = argv_from_cmd(cmd)
    workdir = _resolve_cwd(cwd)

    process_id = "proc_" + secrets.token_hex(8)
    target_log = (pathlib.Path(log_path) if log_path
                  else log_dir() / f"{process_id}.log")
    target_log.parent.mkdir(parents=True, exist_ok=True)

    child_env = os.environ.copy()
    if env:
        child_env.update({str(k): str(v) for k, v in env.items()})

    popen_kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.STDOUT,
        "cwd": str(workdir),
        "env": child_env,
        "close_fds": True,
    }
    if IS_WINDOWS:
        # 独立进程组：CTRL_BREAK 只打这一组，不会波及服务端自己
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # 新 session + 新进程组，等价于 setsid：服务端被杀也不影响它
        popen_kwargs["start_new_session"] = True

    log_file = open(target_log, "ab")           # 不用 PIPE：没人读的管道会卡死子进程
    try:
        handle = subprocess.Popen(argv, stdout=log_file, **popen_kwargs)
    finally:
        log_file.close()                        # 子进程已继承句柄，父进程这份可以关

    now = ev.now_iso()
    row_id = process_id
    display_name = (name or _default_name(argv)).strip()[:MAX_NAME]
    try:
        conn.execute(
            "INSERT INTO processes (id, bot_id, session_id, name, cmd, cwd, pid, pgid,"
            " state, status, background, exit_code, started_at, ended_at, exited_at,"
            " log_path, log_bytes, last_tick_at, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row_id, bot_id, session_id, display_name, _command_text(argv),
             str(workdir), handle.pid, _pgid(handle.pid), "running",
             _STATUS_MIRROR["running"], 1, None, now, None, None,
             str(target_log), 0, now, now),
        )
        conn.commit()
        _emit(conn, bot_id=bot_id, session_id=session_id, kind="process.started",
              payload={"process_id": process_id, "cmd": _command_text(argv),
                       "pid": handle.pid, "pgid": _pgid(handle.pid),
                       "cwd": str(workdir), "name": display_name,
                       "log_path": str(target_log), "background": True})
    except BaseException:
        _terminate(handle, pgid=_pgid(handle.pid), timeout=2.0)
        raise

    with _LOCK:
        _PROCS[process_id] = handle
    return get(conn, process_id)


def _default_name(argv: list[str]) -> str:
    stem = pathlib.PurePath(argv[0]).stem
    return (stem or argv[0])[:MAX_NAME]


def _pgid(pid: int | None) -> int | None:
    """进程组 id：新 session / 新进程组下 pgid == pid（POSIX 再核一遍）。"""
    if not pid:
        return None
    if IS_WINDOWS:
        return int(pid)
    try:
        return os.getpgid(int(pid))
    except OSError:
        return int(pid)


# ---------------------------------------------------------------- 查询
def get(conn, process_id: str) -> dict | None:
    ensure(conn)
    row = _raw(conn, process_id)
    return _row_to_dict(row) if row else None


def list_processes(conn, *, bot_id: str | None = None,
                   state: str | None = None, limit: int = 100) -> list[dict]:
    ensure(conn)
    sql = ["SELECT * FROM processes"]
    where: list[str] = []
    args: list = []
    if bot_id:
        where.append("bot_id = ?")
        args.append(bot_id)
    if state:
        where.append("COALESCE(state, status) = ?")
        args.append(state)
    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY COALESCE(created_at, started_at) DESC, started_at DESC LIMIT ?")
    args.append(max(1, min(int(limit), 500)))
    rows = conn.execute(" ".join(sql), tuple(args)).fetchall()
    return [_row_to_dict(r) for r in rows]


def log_tail(conn, process_id: str, *, tail: int = 200) -> dict | None:
    """读日志末尾 N 行（只回 tail 一段字节，长任务日志不会把内存吃穿）。"""
    ensure(conn)
    row = _raw(conn, process_id)
    if row is None:
        return None
    tail = max(1, min(int(tail), MAX_LOG_TAIL_LINES))
    path = pathlib.Path(row["log_path"])
    lines: list[str] = []
    total = 0
    if path.is_file():
        try:
            total = path.stat().st_size
            data = _read_tail_bytes(path, LOG_TAIL_BYTES)
            lines = data.decode("utf-8", errors="replace").splitlines()[-tail:]
        except OSError:
            lines = []
    return {"process_id": process_id, "log_path": str(path), "bytes": total,
            "lines": lines, "eof": True, "state": _state_of(row)}


def _read_tail_bytes(path: pathlib.Path, limit: int) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - limit))
        return fh.read()


# ---------------------------------------------------------------- 每 tick 对账
def tick(conn, *, emit_ticks: bool = True) -> list[dict]:
    """扫一遍内存里的进程：还活着刷心跳，退出了收尸 + 写 process.exited。

    测试里手动调用它，生产由 supervisor 线程每 15s 调一次（见 start_supervisor）。
    """
    ensure(conn)
    with _LOCK:
        snapshot = list(_PROCS.items())
    results: list[dict] = []
    for process_id, handle in snapshot:
        code = handle.poll()
        if code is None:
            results.append(_heartbeat(conn, process_id, emit_ticks=emit_ticks))
            continue
        results.append(_finalize(conn, process_id, code))
        with _LOCK:
            _PROCS.pop(process_id, None)
    return [item for item in results if item]


def _reap(handle: subprocess.Popen, code: int | None) -> int | None:
    """收尸：poll() 已经 waitpid 过，这里再 wait 一次保证没有僵尸。"""
    try:
        return int(handle.wait(timeout=1.0))
    except subprocess.TimeoutExpired:
        return code
    except (OSError, ValueError):
        return code


def _finalize(conn, process_id: str, code: int) -> dict:
    row = _raw(conn, process_id)
    if row is None:
        return {}
    if _state_of(row) != "running":
        # 已被 stop()/recover 定案（两个线程同时处理同一进程）：不能把 stopped
        # 覆盖成 exited，也不要重复写事件。
        return get(conn, process_id) or {}
    state = "exited" if int(code) == 0 else "failed"
    now = ev.now_iso()
    size = _log_bytes(row["log_path"])
    conn.execute(
        "UPDATE processes SET state=?, status=?, exit_code=?, ended_at=?, exited_at=?,"
        " log_bytes=?, last_tick_at=? WHERE id=?",
        (state, _STATUS_MIRROR[state], int(code), now, now, size, now, process_id),
    )
    conn.commit()
    item = get(conn, process_id) or {}
    _emit(conn, bot_id=row["bot_id"], session_id=row["session_id"],
          kind="process.exited",
          payload={"process_id": process_id, "exit_code": int(code),
                   "state": state, "log_path": row["log_path"],
                   "log_bytes": size,
                   "duration_s": item.get("elapsed_s")})
    return item


def _heartbeat(conn, process_id: str, *, emit_ticks: bool) -> dict:
    row = _raw(conn, process_id)
    if row is None:
        # 没有行就无法发事件（bot_id 不可知）；保留句柄继续 poll，绝不静默丢进程。
        return {}
    now = ev.now_iso()
    size = _log_bytes(row["log_path"])
    conn.execute(
        "UPDATE processes SET log_bytes=?, last_tick_at=? WHERE id=?",
        (size, now, process_id),
    )
    conn.commit()
    item = get(conn, process_id) or {}
    if emit_ticks:
        _emit(conn, bot_id=row["bot_id"], session_id=row["session_id"],
              kind="process.tick",
              payload={"process_id": process_id, "state": "running",
                       "pid": row["pid"], "log_bytes": size,
                       "elapsed_s": item.get("elapsed_s")})
    return item


# ---------------------------------------------------------------- 停止
def stop(conn, process_id: str, *, timeout: float = DEFAULT_STOP_TIMEOUT,
         by: str = "api") -> dict | None:
    """停机：POSIX 先 SIGTERM（整个进程组）再 SIGKILL；Windows 先 CTRL_BREAK 再 kill。

    已退出的进程走"收尸"路径（写 process.exited），不再补 process.killed。
    """
    ensure(conn)
    row = _raw(conn, process_id)
    if row is None:
        return None
    current = _state_of(row)
    with _LOCK:
        handle = _PROCS.get(process_id)
    if current != "running":
        return get(conn, process_id)
    if handle is None:
        # 没有句柄 = 上一代 supervisor 的残留：发不了信号，只能判定丢失。
        return _mark_lost(conn, process_id, reason="stop without handle")

    code = handle.poll()
    if code is not None:                      # 刚好自己退出了：按正常退出记账
        with _LOCK:
            _PROCS.pop(process_id, None)
        return _finalize(conn, process_id, code)

    pgid = _pgid(row["pgid"] or handle.pid)
    code = _terminate(handle, pgid=pgid, timeout=timeout)
    with _LOCK:
        _PROCS.pop(process_id, None)

    row = _raw(conn, process_id)
    if row is None:
        return None
    if _state_of(row) != "running":
        # 并发的 tick() 已经把它收成 exited/failed：按对方定案，不再补 killed。
        return get(conn, process_id)

    now = ev.now_iso()
    size = _log_bytes(row["log_path"])
    conn.execute(
        "UPDATE processes SET state='stopped', status=?, exit_code=?, ended_at=?,"
        " exited_at=?, log_bytes=?, last_tick_at=? WHERE id=?",
        (_STATUS_MIRROR["stopped"], code, now, now, size, now, process_id),
    )
    conn.commit()
    item = get(conn, process_id) or {}
    _emit(conn, bot_id=row["bot_id"], session_id=row["session_id"],
          kind="process.killed",
          payload={"process_id": process_id, "by": by, "exit_code": code,
                   "signal": "SIGTERM/SIGKILL" if not IS_WINDOWS else "CTRL_BREAK",
                   "log_path": row["log_path"]})
    return item


def _terminate(handle: subprocess.Popen, *, pgid: int | None,
               timeout: float = DEFAULT_STOP_TIMEOUT) -> int | None:
    """先礼后兵：优雅信号 -> 等 timeout -> 强杀 -> 收尸。返回退出码（拿不到给 None）。"""
    code = handle.poll()
    if code is not None:
        return _reap(handle, code)

    if IS_WINDOWS:
        try:
            handle.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            pass
    elif pgid:
        try:
            os.killpg(pgid, signal.SIGTERM)     # 整组，子进程的子孙也一起收
        except OSError:
            pass
    else:
        try:
            handle.terminate()
        except OSError:
            pass

    try:
        return int(handle.wait(timeout=timeout))
    except subprocess.TimeoutExpired:
        pass

    if IS_WINDOWS:
        try:
            handle.kill()
        except OSError:
            pass
    elif pgid:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
    else:
        try:
            handle.kill()
        except OSError:
            pass

    try:
        return int(handle.wait(timeout=max(1.0, timeout)))
    except subprocess.TimeoutExpired:
        return None


# ---------------------------------------------------------------- 重启对账
def recover_interrupted(conn, *, by: str = "restart") -> list[str]:
    """服务启动时对账：DB 里还写着 running、但内存里没有句柄的条目一律标 lost。

    DB 是事实源、句柄只在内存：没有句柄意味着上一代 supervisor 已经不在了，这条
    进程"既不能 poll 也不能杀"，必须显式标 lost（契约 02 §4 的 orphaned 语义）。
    有句柄的条目留给 tick() 收尸，这里不动。
    """
    ensure(conn)
    with _LOCK:
        tracked = set(_PROCS)
    rows = conn.execute(
        "SELECT * FROM processes WHERE COALESCE(state, status) = 'running'").fetchall()
    lost: list[str] = []
    for row in rows:
        process_id = row["id"]
        if process_id in tracked:
            continue
        item = _mark_lost(conn, process_id,
                          reason=f"{by}: no supervisor handle"
                                 f" (pid_alive={pid_alive(row['pid'])})")
        if item and item.get("state") == "lost":
            lost.append(process_id)
    return lost


def _mark_lost(conn, process_id: str, *, reason: str) -> dict:
    row = _raw(conn, process_id)
    if row is None:
        return {}
    if _state_of(row) != "running":
        return get(conn, process_id) or {}     # 已经定案过，不重复标 lost/发事件
    now = ev.now_iso()
    conn.execute(
        "UPDATE processes SET state='lost', status=?, ended_at=?, exited_at=?,"
        " last_tick_at=?, log_bytes=? WHERE id=?",
        (_STATUS_MIRROR["lost"], now, now, now, _log_bytes(row["log_path"]), process_id),
    )
    conn.commit()
    item = get(conn, process_id) or {}
    _emit(conn, bot_id=row["bot_id"], session_id=row["session_id"],
          kind="process.exited",
          payload={"process_id": process_id, "state": "lost", "reason": reason,
                   "pid": row["pid"], "exit_code": None,
                   "log_path": row["log_path"],
                   "duration_s": item.get("elapsed_s")})
    return item


# ---------------------------------------------------------------- supervisor 线程
def tracked() -> dict[str, int]:
    """内存里还管着的进程：``{process_id: pid}``（排查用；DB 才是事实源）。"""
    with _LOCK:
        return {process_id: handle.pid for process_id, handle in _PROCS.items()}


def _loop(interval: float) -> None:
    while not _STOP.is_set():
        try:
            with db.get_conn() as conn:
                tick(conn)
        except Exception as exc:                    # noqa: BLE001 - supervisor 自身不能死
            print(f"[processes] supervisor tick failed: {exc}")
        _STOP.wait(interval)


def start_supervisor(interval: float = DEFAULT_TICK) -> None:
    global _TICKER
    with _LOCK:
        if _TICKER is not None and _TICKER.is_alive():
            return
        _STOP.clear()
        _TICKER = threading.Thread(target=_loop, args=(interval,),
                                   name="process-supervisor", daemon=True)
        _TICKER.start()


def stop_supervisor() -> None:
    """只停 tick 线程，**不杀子进程**（契约 02：进程生命周期独立于服务端进程）。"""
    _STOP.set()
