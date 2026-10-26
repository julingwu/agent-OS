"""v5.4.2 §79: 线程级数据库上下文继承 + 后台线程排空屏障的回归测试。

覆盖三个物理事实（都是 v5.4.1 偶发 no such table 的根因链上的环节）：
1. 后台线程里**不传参数**的 `db.get_conn()` 严格命中 `pin_thread_db(owner_db)`，
   哪怕中间隔着若干层函数、且 `DB_PATH` 环境变量早已指向别的库。
2. 绑定态下目标库文件被删 / 库未初始化时，`connect()` 必须失败，绝不允许 sqlite
   静默新建 0 表空库。
3. 后台线程必须登记进 `_active_bg_threads`，`wait_for_bg_threads()` 能把它排空。
"""
from __future__ import annotations

import pathlib
import sqlite3
import threading
import time

import pytest

from app import db


def _make_db(path: pathlib.Path) -> None:
    """建一个「已初始化」的最小库：够过 CORE_TABLES 校验。"""
    conn = sqlite3.connect(str(path))
    try:
        for table in db.CORE_TABLES:
            conn.execute(f"CREATE TABLE {table}(id TEXT)")
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------ 继承
def test_db_path_priority_binding_over_env(tmp_path, monkeypatch):
    bound = tmp_path / "owner.db"
    other = tmp_path / "other.db"
    monkeypatch.setenv("DB_PATH", str(other))
    assert pathlib.Path(db.db_path()) == other              # 未绑定：读 DB_PATH
    with db.pin_thread_db(str(bound)):
        assert pathlib.Path(db.db_path()) == bound          # 绑定优先于 DB_PATH
    assert pathlib.Path(db.db_path()) == other              # 退出后恢复


def _deep_call_depth_3():
    """模拟 run_project 内部深层调用：一层不传 db_path。"""
    def _depth_2():
        def _depth_1():
            with db.get_conn() as conn:
                return conn.execute("PRAGMA database_list").fetchall()
        return _depth_1()
    return _depth_2()


def test_background_thread_get_conn_inherits_pinned_db(tmp_path):
    owner = tmp_path / "owner.db"
    _make_db(owner)
    seen: dict[str, object] = {}

    def _worker() -> None:
        with db.pin_thread_db(str(owner)):
            rows = _deep_call_depth_3()
            seen["files"] = {r["file"] for r in rows if r["file"]}
            seen["db_path"] = db.db_path()
            seen["thread"] = threading.current_thread().name

    t = db.spawn_bg_thread(_worker, name="oa-test-inherit", db=str(owner))
    assert db.wait_for_bg_threads(timeout=10.0)
    assert not t.is_alive()
    assert seen["db_path"] == str(owner)
    files = {pathlib.Path(f).resolve() for f in seen["files"]}
    assert owner.resolve() in files       # 连接真的落在 owner 库上，而不是别人的


def test_background_thread_inherits_even_if_env_moves(tmp_path, monkeypatch):
    """用例 A 的线程活到用例 B（DB_PATH 已换库）时，仍必须锁在 A 的库。"""
    owner = tmp_path / "a.db"
    moved = tmp_path / "b.db"
    _make_db(owner)
    _make_db(moved)
    monkeypatch.setenv("DB_PATH", str(owner))
    started, release = threading.Event(), threading.Event()
    seen: dict[str, str] = {}

    def _worker() -> None:
        started.set()
        release.wait(5)                  # 等主线程把 DB_PATH 换成下一个用例的库
        with db.get_conn() as conn:
            seen["file"] = dict(conn.execute("PRAGMA database_list").fetchone())["file"]

    t = db.spawn_bg_thread(_worker, name="oa-test-moved", db=db.db_path())
    assert started.wait(timeout=5)
    monkeypatch.setenv("DB_PATH", str(moved))        # 执行时环境已经漂移到 b.db
    release.set()
    assert db.wait_for_bg_threads(timeout=10.0)
    assert not t.is_alive()
    assert pathlib.Path(seen["file"]).resolve() == owner.resolve()


# ------------------------------------------------------------- 空库防御
def test_bound_thread_refuses_missing_database_file(tmp_path):
    gone = tmp_path / "deleted" / "owner.db"
    with db.pin_thread_db(str(gone)):
        with pytest.raises(db.BoundDbUnavailable) as exc:
            db.connect()
    assert "missing" in str(exc.value)
    # 绝不允许 sqlite 顺手把文件（甚至父目录）造出来
    assert not gone.exists()
    assert not gone.parent.exists()
    assert any("missing" in f["reason"] for f in db.bound_db_failures())


def test_bound_thread_refuses_uninitialized_database(tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(str(empty)).close()          # 0 表的空库，正是事故现场
    with db.pin_thread_db(str(empty)):
        with pytest.raises(db.BoundDbUnavailable) as exc:
            db.connect()
    assert "not initialized" in str(exc.value)
    assert any("not an initialized" in f["reason"] for f in db.bound_db_failures())


def test_unbound_connect_still_creates_database(tmp_path):
    """非绑定态保持历史行为：init_db/测试主线程依赖 connect() 建新库。"""
    fresh = tmp_path / "fresh.db"
    assert not fresh.exists()
    conn = db.connect(str(fresh))
    conn.close()
    assert fresh.exists()


def test_spawned_worker_dies_cleanly_when_db_removed(tmp_path):
    owner = tmp_path / "owner.db"
    _make_db(owner)
    owner.unlink()                               # 用例结束，临时库被回收
    outcomes: list[str] = []

    def _worker() -> None:
        try:
            with db.get_conn() as conn:
                conn.execute("SELECT 1")
            outcomes.append("opened")
        except db.BoundDbUnavailable:
            outcomes.append("clean-exit")

    db.spawn_bg_thread(_worker, name="oa-test-gone", db=str(owner))
    assert db.wait_for_bg_threads(timeout=10.0)
    assert outcomes == ["clean-exit"]
    assert not owner.exists()                    # 依然没有被偷偷重建


# ----------------------------------------------------------- 排空屏障
def test_spawn_registers_and_waits_for_threads():
    gate = threading.Event()
    done = threading.Event()

    def _worker() -> None:
        gate.wait(10)
        done.set()

    t = db.spawn_bg_thread(_worker, name="oa-test-drain")
    assert t in db.active_bg_threads()
    assert db.wait_for_bg_threads(timeout=0.2) is False     # 还在跑：不许误判排空
    gate.set()
    assert db.wait_for_bg_threads(timeout=10.0) is True
    assert done.is_set()
    assert db.active_bg_threads() == []


def test_wait_for_bg_threads_noop_when_idle():
    assert db.wait_for_bg_threads(timeout=0.1) is True
    assert db.active_bg_threads() == []
