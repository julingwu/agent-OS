"""SQLite plumbing: connection, schema bootstrap, per-request transaction."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3
import threading
import time
from contextlib import contextmanager
from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parents[1]

log = logging.getLogger("oaagent.db")

# 绑定态后台线程必须先存在的核心表：缺这些表说明手里的文件根本不是「已初始化的
# 库」（典型来源：临时目录被删后 sqlite3.connect 静默建的 0 表空库）。
CORE_TABLES = ("tasks", "task_runs", "project_stages")


class BoundDbUnavailable(RuntimeError):
    """绑定态后台线程的目标库不可用（文件已被清理 / 未初始化）。

    防御性异常：宁可让这条后台线程干净退出，也绝不静默新建 0 表空库，把
    「no such table」伪装成一个业务错误继续往下跑。
    """


# ---------------------------------------------------------------- 线程级库绑定
_thread_state = threading.local()


def current_thread_db() -> str | None:
    """当前线程被 `pin_thread_db` 绑定的库路径；未绑定返回 None。"""
    return getattr(_thread_state, "db_path", None)


@contextmanager
def pin_thread_db(path: str | None = None):
    """把「这条线程归属哪个库」钉死（v5.4.2 §79）。

    后台线程的库是**发起请求时**那个库，不是执行时 `DB_PATH` 碰巧指向的库。
    绑定读在 `db_path()` 里，所以 connect()/open_conn()/任意深度的 caller 全部
    继承，不需要 20 层调用链层层透传 db_path。
    """
    previous = getattr(_thread_state, "db_path", None)
    target = str(path or db_path())
    _thread_state.db_path = target
    try:
        yield target
    finally:
        _thread_state.db_path = previous


def db_path() -> str:
    """Resolved at call time so tests can point DB_PATH at a temp file.

    优先级：**线程绑定 > DB_PATH 环境变量 > 默认生产库**。优先级必须落在这里而
    不是 get_conn()，否则 connect()/open_conn()/深层 caller 仍会漂移。
    """
    bound = getattr(_thread_state, "db_path", None)
    if bound:
        return bound
    return os.environ.get("DB_PATH") or str(ROOT / "data" / "oa.db")


_failures_lock = threading.Lock()
_bound_db_failures: list[dict] = []


def bound_db_failures() -> list[dict]:
    """最近一批「绑定态库不可用」事件，供测试断言与运维观测。"""
    with _failures_lock:
        return list(_bound_db_failures)


def clear_bound_db_failures() -> None:
    with _failures_lock:
        _bound_db_failures.clear()


def _record_bound_db_failure(path: str, reason: str) -> None:
    entry = {"path": path, "reason": reason,
             "thread": threading.current_thread().name}
    with _failures_lock:
        _bound_db_failures.append(entry)
        del _bound_db_failures[:-50]
    log.warning("bound-thread database unavailable, aborting instead of creating an"
                " empty database: path=%s reason=%s thread=%s",
                path, reason, entry["thread"])


def _readwrite_uri(path: str) -> str:
    """`file:...?mode=rw`：只打开已存在的库，文件不在就失败，绝不新建。"""
    return "file:" + quote(pathlib.Path(path).resolve().as_posix(), safe="/:") + "?mode=rw"


def schema_sql() -> str:
    return (pathlib.Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")


def _enable_wal(conn: sqlite3.Connection) -> None:
    """把连接切到 WAL，并对瞬时并发做重试。

    物理事实：`PRAGMA journal_mode = WAL` 需要短暂独占锁，而 SQLite 的 busy
    handler 对这条 PRAGMA 并不总是生效，进程内其它连接正在读时它会立刻
    SQLITE_BUSY。WAL 是**文件级持久属性**，一旦切成功后续连接读到的就是 wal，
    所以这里先试切换、再看当前模式，都拿不到锁就短退避重试。
    """
    for attempt in range(6):
        try:
            mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() == "wal":
                return
        except sqlite3.OperationalError:
            pass
        try:
            if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
                return
        except sqlite3.OperationalError:
            pass
        time.sleep(0.05 * (attempt + 1))
    # 重试仍失败：明确报错，绝不静默退化成非 WAL（那会破坏 WAL 单写不变量）。
    raise sqlite3.OperationalError("cannot enable WAL journal mode (database is locked)")


def connect(path: str | None = None, *, create: bool | None = None) -> sqlite3.Connection:
    """Open a connection.

    `create` 缺省时按线程状态决定：**绑定态后台线程只允许打开已存在的、已初始化
    的库**（v5.4.2 §79），非绑定态（生产启动 / 测试主线程）保持历史行为——允许
    sqlite 建新库，`init_db` 依赖这一点。
    """
    bound = current_thread_db()
    p = path or db_path()
    if create is None:
        create = bound is None
    if create:
        pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p, timeout=30.0, check_same_thread=False)
    else:
        conn = _open_existing(p)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        if not create:
            _require_initialized(conn, p)
        _enable_wal(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _open_existing(p: str) -> sqlite3.Connection:
    """绑定态专用：文件不存在就以 mode=rw 失败，绝不静默造 0 表空库。"""
    if not pathlib.Path(p).exists():
        _record_bound_db_failure(p, "database file missing")
        raise BoundDbUnavailable(f"database file missing: {p}")
    try:
        return sqlite3.connect(_readwrite_uri(p), timeout=30.0,
                               check_same_thread=False, uri=True)
    except sqlite3.OperationalError as exc:
        _record_bound_db_failure(p, f"cannot open read-write: {exc}")
        raise BoundDbUnavailable(f"cannot open database read-write: {p}") from exc


def _require_initialized(conn: sqlite3.Connection, p: str) -> None:
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    missing = [t for t in CORE_TABLES if t not in names]
    if missing:
        _record_bound_db_failure(
            p, "not an initialized database (missing tables: " + ",".join(missing) + ")")
        raise BoundDbUnavailable(
            f"database not initialized (missing {','.join(missing)}): {p}")


MIGRATIONS = [
    ("artifacts", "state", "ALTER TABLE artifacts ADD COLUMN state TEXT DEFAULT 'active'"),
    ("ai_employees", "api_key", "ALTER TABLE ai_employees ADD COLUMN api_key TEXT"),
    ("ai_employees", "api_base", "ALTER TABLE ai_employees ADD COLUMN api_base TEXT"),
    ("tasks", "owner_employee_id", "ALTER TABLE tasks ADD COLUMN owner_employee_id TEXT"),
    ("chat_members", "state", "ALTER TABLE chat_members ADD COLUMN state TEXT DEFAULT 'active'"),
    ("ai_employees", "preset", "ALTER TABLE ai_employees ADD COLUMN preset TEXT DEFAULT 'standard'"),
    ("ai_employees", "role_id", "ALTER TABLE ai_employees ADD COLUMN role_id TEXT"),
    ("chats", "pinned", "ALTER TABLE chats ADD COLUMN pinned INTEGER DEFAULT 0"),
    ("chats", "hidden", "ALTER TABLE chats ADD COLUMN hidden INTEGER DEFAULT 0"),
    ("chats", "deleted_at", "ALTER TABLE chats ADD COLUMN deleted_at TIMESTAMP"),
    ("chats", "workdir", "ALTER TABLE chats ADD COLUMN workdir TEXT"),

    ("wecom_pushes", "created_at",
     "ALTER TABLE wecom_pushes ADD COLUMN created_at TIMESTAMP"),
    ("task_runs", "usage_tokens", "ALTER TABLE task_runs ADD COLUMN usage_tokens INTEGER DEFAULT 0"),
    ("task_runs", "usage_baseline", "ALTER TABLE task_runs ADD COLUMN usage_baseline INTEGER DEFAULT 0"),
    ("identities", "employee_id", "ALTER TABLE identities ADD COLUMN employee_id TEXT"),
    ("identities", "tenant_id", "ALTER TABLE identities ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("legal_entities", "tenant_id", "ALTER TABLE legal_entities ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("tasks", "tenant_id", "ALTER TABLE tasks ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("task_runs", "tenant_id", "ALTER TABLE task_runs ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("artifacts", "tenant_id", "ALTER TABLE artifacts ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("task_runs", "error", "ALTER TABLE task_runs ADD COLUMN error TEXT"),
    ("task_runs", "mcp_fingerprint", "ALTER TABLE task_runs ADD COLUMN mcp_fingerprint TEXT"),
    ("projects", "tenant_id", "ALTER TABLE projects ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("projects", "veto_note", "ALTER TABLE projects ADD COLUMN veto_note TEXT"),
    ("chats", "tenant_id", "ALTER TABLE chats ADD COLUMN tenant_id TEXT DEFAULT 'TENANT-HQ'"),
    ("task_runs", "actor_identity", "ALTER TABLE task_runs ADD COLUMN actor_identity TEXT"),
    ("task_runs", "approval_id", "ALTER TABLE task_runs ADD COLUMN approval_id TEXT"),
    ("task_runs", "prompt_text", "ALTER TABLE task_runs ADD COLUMN prompt_text TEXT"),
    ("decision_packages", "created_by", "ALTER TABLE decision_packages ADD COLUMN created_by TEXT"),
    ("budgets", "identity_id", "ALTER TABLE budgets ADD COLUMN identity_id TEXT"),
    ("budgets", "limit_amount", "ALTER TABLE budgets ADD COLUMN limit_amount REAL DEFAULT 0"),
    ("budgets", "reserved_amount", "ALTER TABLE budgets ADD COLUMN reserved_amount REAL DEFAULT 0"),
    ("budgets", "used_amount", "ALTER TABLE budgets ADD COLUMN used_amount REAL DEFAULT 0"),
    ("project_stages", "role_tag", "ALTER TABLE project_stages ADD COLUMN role_tag TEXT DEFAULT 'dev'"),
    ("project_stages", "node_kind", "ALTER TABLE project_stages ADD COLUMN node_kind TEXT DEFAULT 'standard'"),
    ("project_stages", "assignee_id", "ALTER TABLE project_stages ADD COLUMN assignee_id TEXT"),
    ("project_stages", "gate_name", "ALTER TABLE project_stages ADD COLUMN gate_name TEXT DEFAULT ''"),
]


def _migrate(conn: sqlite3.Connection) -> set[str]:
    added_tenants = set()
    for table, column, ddl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue          # 表还没建（如 chat_members 由 store.ensure 建），跳过
        if column not in cols:
            conn.execute(ddl)
            if column == "tenant_id":
                added_tenants.add(table)
    return added_tenants


def _backfill_tenants(conn: sqlite3.Connection, added: set[str]) -> None:
    """Inherit tenant ownership once for old tables, then only repair empty rows."""
    cols = {name: {r[1] for r in conn.execute(f"PRAGMA table_info({name})")}
            for name in ("legal_entities", "identities", "projects", "chats",
                         "tasks", "task_runs", "artifacts", "ai_employees")}
    sources = {
        "legal_entities": [],
        "identities": [("legal_entities", "legal_entity_id", "id")],
        "projects": [("legal_entities", "legal_entity_id", "id")],
        "chats": [("legal_entities", "entity_id", "id")],
        "tasks": [("projects", "project_id", "id"),
                  ("legal_entities", "legal_entity_id", "id")],
        "task_runs": [("tasks", "task_id", "id")],
        "artifacts": [("task_runs", "run_id", "id")],
    }
    for table, parents in sources.items():
        if "tenant_id" not in cols[table]:
            continue
        inherited = [f"(SELECT NULLIF(p.tenant_id,'') FROM {parent} p"
                     f" WHERE p.{pk}={table}.{fk})"
                     for parent, fk, pk in parents
                     if fk in cols[table] and "tenant_id" in cols[parent]]
        if table == "chats" and "employee_id" in cols[table] and cols["ai_employees"]:
            inherited.append("(SELECT NULLIF(le.tenant_id,'') FROM ai_employees e"
                             " JOIN legal_entities le ON le.id=e.legal_entity_id"
                             " WHERE e.id=chats.employee_id)")
        value = ", ".join([*inherited, f"NULLIF({table}.tenant_id,'')", "'TENANT-HQ'"])
        where = "1=1" if table in added else "COALESCE(tenant_id,'')=''"
        conn.execute(f"UPDATE {table} SET tenant_id=COALESCE({value}) WHERE {where}")


def init_db(path: str | None = None) -> None:
    conn = connect(path)
    try:
        conn.executescript(schema_sql())
        from . import pipeline
        pipeline.ensure(conn)
        from . import flows
        flows.ensure(conn)
        from .ui import roles
        roles.ensure(conn)
        from . import experience
        experience.ensure(conn)
        from . import evals
        evals.ensure(conn)
        added_tenants = _migrate(conn)
        _backfill_tenants(conn, added_tenants)
        conn.execute("UPDATE wecom_pushes SET created_at = CURRENT_TIMESTAMP"
                     " WHERE created_at IS NULL")
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def open_conn(path: str | None = None):
    """get_conn 的「绑定指定库」版本。

    后台线程必须用它：异步任务的大脑是发起请求时那个库，不是执行时 DB_PATH
    碰巧指向的库。否则线程会漂移到别人的库上（进程内换库的场景下尤其致命）。
    """
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ------------------------------------------------- 后台线程派发口与排空屏障
_bg_lock = threading.Lock()
_active_bg_threads: set[threading.Thread] = set()


def active_bg_threads() -> list[threading.Thread]:
    """当前仍活着的已登记后台线程。"""
    with _bg_lock:
        return [t for t in _active_bg_threads if t.is_alive()]


def spawn_bg_thread(target, *, name: str | None = None,
                    db: str | None = None) -> threading.Thread:
    """后台流水线的统一派发口：登记 + 绑定发起方库 + 退出即注销（v5.4.2 §79）。

    * 绑定：线程内 `db_path()` 永远返回 `db`（发起请求时的库）。
    * 登记：用例 teardown 调 `wait_for_bg_threads()` 排空；hard invariant 要求
      「每个用例结束前必须排空它派生的后台流水线线程」。
    """
    owner_db = str(db or db_path())
    thread_name = name or f"oa-bg-{threading.active_count()}"

    def _run() -> None:
        try:
            with pin_thread_db(owner_db):
                target()
        except BoundDbUnavailable:
            pass                 # 已在 _record_bound_db_failure 里留证据，干净退出
        except BaseException:    # noqa: BLE001 - 后台线程不能把异常抛给解释器
            log.exception("background thread %s crashed", thread_name)
        finally:
            with _bg_lock:
                _active_bg_threads.discard(threading.current_thread())

    t = threading.Thread(target=_run, daemon=True, name=thread_name)
    with _bg_lock:
        _active_bg_threads.add(t)
    t.start()
    return t


def wait_for_bg_threads(timeout: float = 5.0) -> bool:
    """等所有已登记后台线程退出；返回是否已排空（False = 超时仍有残留）。"""
    deadline = time.monotonic() + timeout
    me = threading.current_thread()
    while True:
        with _bg_lock:
            pending = [t for t in _active_bg_threads if t is not me and t.is_alive()]
            if not pending:
                _active_bg_threads.difference_update(
                    t for t in _active_bg_threads if not t.is_alive())
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        pending[0].join(min(remaining, 0.05))
