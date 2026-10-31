"""W4 进程 supervisor 验收（契约 02 的本地实现）。

覆盖：
  * 契约表（schema_v1.sql 的 status/exited_at 那套，init_db 已建）上的增量补列；
  * 起进程：argv 列表 + Windows 原生命令行字符串切分，日志落 workspace/logs；
  * 列表 / 详情 / 日志 tail；
  * tick 收尸：exit 0 -> exited、exit≠0 -> failed，进程句柄退出后不留僵尸；
  * stop：状态 stopped、process.killed 事件、幂等、pid 真的没了；
  * 重启对账：DB 里 running 但没有句柄的残留 -> lost；
  * 非法 cmd / cwd / 不存在的可执行文件 -> 400；全路由 require_device 鉴权。

铁律（和 routines 一样）：
  * **不启动常驻 supervisor 线程**，tick 手动调；
  * 每个用例结束必须把子进程收干净（autouse fixture 兜底 + 断言内存注册表为空），
    否则 pytest 会留一堆 python.exe 在后台。
"""

from __future__ import annotations

import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, devices, processes
from app import events as ev
from app.api_v1 import processes as process_api

POLL_TIMEOUT = 10.0


# ---------------------------------------------------------------- fixtures
@pytest.fixture()
def client():
    """只挂 processes 路由的最小 app：不依赖 Lead 在 main.py 的接线。"""
    app = FastAPI()
    app.include_router(process_api.router)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def device():
    with db.get_conn() as conn:
        return devices.issue(conn, name="pytest-processes", kind="exe")


@pytest.fixture(autouse=True)
def no_leaked_children():
    """兜底：用例结束停掉所有还 running 的进程，并断言内存注册表已清空。"""
    yield
    with db.get_conn() as conn:
        for row in processes.list_processes(conn, limit=500):
            if row["state"] == "running":
                try:
                    processes.stop(conn, row["id"], timeout=3.0, by="pytest")
                except Exception:                     # noqa: BLE001 - teardown 不掩盖原始失败
                    pass
        still_running = [r["id"] for r in processes.list_processes(conn, limit=500)
                         if r["state"] == "running"]
    assert still_running == [], f"测试结束后仍有进程在跑：{still_running}"
    assert processes.tracked() == {}, f"内存注册表没清空：{processes.tracked()}"


def _auth(device: dict) -> dict:
    return {"Authorization": f"Bearer {device['token']}"}


def _start(client, device, *, cmd, **extra) -> dict:
    body = {"bot_id": "bot_chief", "session_id": "ses_proc_test", "cmd": cmd, **extra}
    res = client.post("/v1/processes", headers=_auth(device), json=body)
    assert res.status_code == 200, res.text
    item = res.json()["item"]
    assert item["state"] == "running" and item["running"] is True
    return item


def _tick(*, emit_ticks: bool = False) -> list[dict]:
    with db.get_conn() as conn:
        return processes.tick(conn, emit_ticks=emit_ticks)


def _get(process_id: str) -> dict:
    with db.get_conn() as conn:
        return processes.get(conn, process_id)


def _wait_state(process_id: str, states: set[str],
                *, timeout: float = POLL_TIMEOUT, emit_ticks: bool = False) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        _tick(emit_ticks=emit_ticks)
        last = _get(process_id)
        if last and last["state"] in states:
            return last
        time.sleep(0.1)
    raise AssertionError(f"{process_id} 没在 {timeout}s 内进入 {states}，最后：{last}")


def _wait_log(client, device, process_id: str, predicate, *, timeout: float = POLL_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/v1/processes/{process_id}/log?tail=200",
                          headers=_auth(device)).json()
        if predicate(last):
            return last
        time.sleep(0.1)
    raise AssertionError(f"日志没在 {timeout}s 内满足条件，最后：{last}")


def _events():
    with db.get_conn() as conn:
        return ev.read(conn, since=0, limit=ev.MAX_READ_LIMIT)


def _kinds() -> list[str]:
    return [event.kind for event in _events()]


SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]


# ---------------------------------------------------------------- 建表 / 补列
def test_ensure_adds_workflow_columns_on_top_of_contract_table():
    """schema_v1.sql 已经建了契约表；ensure() 只能增量补列，不能推翻它。"""
    with db.get_conn() as conn:
        before = {row[1] for row in conn.execute("PRAGMA table_info(processes)")}
        assert {"status", "exited_at", "pgid", "log_bytes"} <= before
        assert "state" not in before          # init_db 建的是契约列
        # 契约词表的老行（killed）要能翻译成 state 词表（stopped）
        conn.execute(
            "INSERT INTO processes (id, bot_id, cmd, status, log_path, started_at)"
            " VALUES ('proc_legacy', 'bot_chief', 'python x.py', 'killed',"
            " 'logs/legacy.log', ?)", (ev.now_iso(),))
        conn.commit()
        processes.ensure(conn)
        after = {row[1] for row in conn.execute("PRAGMA table_info(processes)")}
        legacy = processes.get(conn, "proc_legacy")

    assert {"id", "bot_id", "session_id", "name", "cmd", "cwd", "pid", "state",
            "status", "exit_code", "started_at", "ended_at", "exited_at",
            "log_path", "created_at"} <= after
    assert legacy["state"] == "stopped" and legacy["status"] == "killed"
    assert legacy["created_at"] == legacy["started_at"]
    # 按 db.db_path() 缓存：同一库第二次是 no-op，不能抛也不能重复建
    assert db.db_path() in processes._READY_DBS
    with db.get_conn() as conn:
        processes.ensure(conn)


# ---------------------------------------------------------------- 起进程 / 查询
def test_start_list_detail_and_log(client, device):
    printer = [sys.executable, "-c",
               "print('hello-process', flush=True); import time; time.sleep(30)"]
    item = _start(client, device, cmd=printer, name="打个招呼")

    assert item["pid"] and item["pid"] > 0 and item["pgid"] == item["pid"]
    assert item["name"] == "打个招呼" and item["bot_id"] == "bot_chief"
    assert item["session_id"] == "ses_proc_test"
    assert item["cwd"], "默认 cwd 应该是 Bot 工作区"
    normalised = item["log_path"].replace("\\", "/")
    assert normalised.endswith(f"/logs/{item['id']}.log")
    assert processes.pid_alive(item["pid"]) is True

    listed = client.get("/v1/processes?bot_id=bot_chief", headers=_auth(device)).json()
    assert listed["count"] == 1 and listed["items"][0]["id"] == item["id"]

    detail = client.get(f"/v1/processes/{item['id']}", headers=_auth(device)).json()
    assert detail["item"]["state"] == "running"
    assert detail["item"]["cmd"]                       # 展示用命令行非空

    log = _wait_log(client, device, item["id"],
                    lambda out: "hello-process" in out["lines"])
    assert log["lines"] == ["hello-process"]
    assert log["bytes"] > 0 and log["log_path"] == item["log_path"]
    assert log["state"] == "running"

    tail = client.get(f"/v1/processes/{item['id']}/log?tail=1",
                      headers=_auth(device)).json()
    assert tail["lines"] == ["hello-process"]

    started = [e for e in _events() if e.kind == "process.started"]
    assert len(started) == 1
    assert started[0].payload["process_id"] == item["id"]
    assert started[0].payload["pid"] == item["pid"]


def test_log_tail_keeps_only_last_lines(client, device):
    script = ("print('l1', flush=True); print('l2', flush=True); print('l3', flush=True);"
              " import time; time.sleep(30)")
    item = _start(client, device, cmd=[sys.executable, "-c", script])
    out = _wait_log(client, device, item["id"], lambda body: len(body["lines"]) >= 3)
    assert out["lines"] == ["l1", "l2", "l3"]

    tail = client.get(f"/v1/processes/{item['id']}/log?tail=2",
                      headers=_auth(device)).json()
    assert tail["lines"] == ["l2", "l3"]


def test_string_cmd_is_split_with_native_rules(client, device):
    """字符串 cmd 要能吃带反斜杠/空格的路径：Windows 走 CommandLineToArgvW。"""
    script = "print('from-string-cmd', flush=True); import time; time.sleep(30)"
    item = _start(client, device, cmd=f'"{sys.executable}" -c "{script}"')
    log = _wait_log(client, device, item["id"],
                    lambda out: "from-string-cmd" in out["lines"])
    assert log["lines"] == ["from-string-cmd"]


# ---------------------------------------------------------------- tick
def test_tick_heartbeats_running_process(client, device):
    item = _start(client, device, cmd=SLEEPER)
    results = _tick(emit_ticks=True)
    assert [row["id"] for row in results] == [item["id"]]
    assert results[0]["state"] == "running"

    row = _get(item["id"])
    assert row["state"] == "running"
    assert row["last_tick_at"] and row["last_tick_at"] >= row["started_at"]
    assert row["log_bytes"] >= 0

    kinds = _kinds()
    assert "process.tick" in kinds
    assert "process.exited" not in kinds            # 心跳绝不写退出


def test_tick_finalizes_normal_exit_and_reaps(client, device):
    item = _start(client, device, cmd=[sys.executable, "-c", "print('bye', flush=True)"])
    pid = item["pid"]
    row = _wait_state(item["id"], {"exited", "failed"})

    assert row["state"] == "exited" and row["exit_code"] == 0
    assert row["running"] is False and row["alive"] is False
    assert row["ended_at"], "退出必须写 ended_at"
    assert row["ended_at"] == row["exited_at"]
    assert processes.pid_alive(pid) is False, "poll/watch 之后不能留僵尸"
    assert processes.tracked() == {}

    lines = client.get(f"/v1/processes/{item['id']}/log?tail=10",
                       headers=_auth(device)).json()["lines"]
    assert lines == ["bye"]

    exited = [e for e in _events() if e.kind == "process.exited"]
    assert len(exited) == 1
    assert exited[0].payload["process_id"] == item["id"]
    assert exited[0].payload["exit_code"] == 0
    assert exited[0].payload["state"] == "exited"

    # 再 tick 一次：注册表已清空，不会重复写事件
    assert _tick() == []
    assert _kinds().count("process.exited") == 1


def test_tick_marks_nonzero_exit_failed(client, device):
    script = "print('boom', flush=True); import sys; sys.exit(3)"
    item = _start(client, device, cmd=[sys.executable, "-c", script])
    row = _wait_state(item["id"], {"exited", "failed"})

    assert row["state"] == "failed" and row["exit_code"] == 3
    assert row["status"] == "failed"            # 契约 status 镜像
    assert processes.pid_alive(item["pid"]) is False
    exited = [e for e in _events() if e.kind == "process.exited"]
    assert exited and exited[0].payload["state"] == "failed"
    assert exited[0].payload["exit_code"] == 3


# ---------------------------------------------------------------- 停止
def test_stop_writes_killed_event_and_is_idempotent(client, device):
    item = _start(client, device, cmd=SLEEPER)
    pid = item["pid"]
    assert processes.pid_alive(pid) is True

    res = client.post(f"/v1/processes/{item['id']}/stop", headers=_auth(device),
                      json={"timeout_s": 3})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["stopped"] is True
    assert body["item"]["state"] in ("stopped", "exited")
    assert body["item"]["state"] == "stopped"
    assert body["item"]["status"] == "killed"          # 契约 status 镜像
    assert body["item"]["exit_code"] is not None       # 一定等到退出码，才叫收尸
    assert body["item"]["ended_at"]
    assert processes.pid_alive(pid) is False, "stop 之后不能留子进程"
    assert processes.tracked() == {}

    # 幂等：再停一次不报错、不重复写 killed（stopped 表示"调用结束时它已不在运行"）
    again = client.post(f"/v1/processes/{item['id']}/stop", headers=_auth(device))
    assert again.status_code == 200
    assert again.json()["stopped"] is True
    assert again.json()["item"]["state"] == "stopped"
    assert again.json()["item"]["exit_code"] == body["item"]["exit_code"]
    killed = [e for e in _events() if e.kind == "process.killed"]
    assert len(killed) == 1
    assert killed[0].payload["process_id"] == item["id"]
    assert killed[0].payload["by"] == "api"
    assert killed[0].payload["exit_code"] is not None
    # 被 stop 的进程只记 killed，tick 不该再补一条 exited
    assert _tick() == []
    assert "process.exited" not in _kinds()


def test_stop_on_database_only_row_marks_lost(client, device):
    """有行没句柄（上一代 supervisor 的残留）：stop 不能假装成功，只能标 lost。"""
    with db.get_conn() as conn:
        processes.ensure(conn)
        conn.execute(
            "INSERT INTO processes (id, bot_id, session_id, name, cmd, cwd, pid, state,"
            " status, background, started_at, log_path, log_bytes, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,1,?,?,0,?)",
            ("proc_no_handle", "bot_chief", "ses_proc_test", "残留命令",
             "python -c pass", str(processes.log_dir()), None, "running", "running",
             ev.now_iso(), str(processes.log_dir() / "proc_no_handle.log"),
             ev.now_iso()),
        )
        conn.commit()

    res = client.post("/v1/processes/proc_no_handle/stop", headers=_auth(device))
    assert res.status_code == 200, res.text
    assert res.json()["item"]["state"] == "lost"
    assert res.json()["stopped"] is True
    assert "process.killed" not in _kinds()


# ---------------------------------------------------------------- 重启对账
def test_recover_interrupted_marks_leftover_running_lost(client, device):
    live = _start(client, device, cmd=SLEEPER)          # 有句柄的活进程，不能被误标
    with db.get_conn() as conn:
        processes.ensure(conn)
        for process_id, pid in (("proc_lost_nopid", None), ("proc_lost_deadpid", 0x7FFFFFF0)):
            conn.execute(
                "INSERT INTO processes (id, bot_id, session_id, name, cmd, cwd, pid,"
                " state, status, background, started_at, log_path, log_bytes, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,1,?,?,0,?)",
                (process_id, "bot_chief", "ses_proc_test", "重启残留", "python long_task.py",
                 str(processes.log_dir()), pid, "running", "running", ev.now_iso(),
                 str(processes.log_dir() / f"{process_id}.log"), ev.now_iso()),
            )
        conn.commit()
        lost = processes.recover_interrupted(conn)

    assert set(lost) == {"proc_lost_nopid", "proc_lost_deadpid"}
    with db.get_conn() as conn:
        assert processes.get(conn, "proc_lost_nopid")["state"] == "lost"
        dead = processes.get(conn, "proc_lost_deadpid")
        assert dead["state"] == "lost" and dead["status"] == "orphaned"
        assert dead["ended_at"] and dead["running"] is False
        assert processes.get(conn, live["id"])["state"] == "running"

    lost_events = [e for e in _events()
                   if e.kind == "process.exited" and e.payload.get("state") == "lost"]
    assert {e.payload["process_id"] for e in lost_events} == {
        "proc_lost_nopid", "proc_lost_deadpid"}
    # 再对账一次：已经是 lost 的不重复处理
    with db.get_conn() as conn:
        assert processes.recover_interrupted(conn) == []
        assert processes.get(conn, live["id"])["state"] == "running"


# ---------------------------------------------------------------- 参数校验 / 鉴权
@pytest.mark.parametrize("cmd", ["", "   ", [], ["   "]])
def test_rejects_empty_cmd(client, device, cmd):
    res = client.post("/v1/processes", headers=_auth(device),
                      json={"bot_id": "bot_chief", "cmd": cmd})
    assert res.status_code == 400, res.text
    assert "cmd" in res.json()["detail"]


def test_rejects_bad_cwd_and_missing_binary(client, device, tmp_path):
    bad_cwd = client.post("/v1/processes", headers=_auth(device), json={
        "bot_id": "bot_chief", "cmd": [sys.executable, "-c", "pass"],
        "cwd": str(tmp_path / "no-such-dir")})
    assert bad_cwd.status_code == 400
    assert "cwd" in bad_cwd.json()["detail"]

    missing = client.post("/v1/processes", headers=_auth(device), json={
        "bot_id": "bot_chief", "cmd": "definitely_not_a_real_binary_xyz --help"})
    assert missing.status_code == 400
    assert "spawn failed" in missing.json()["detail"]


def test_unknown_process_is_404(client, device):
    headers = _auth(device)
    assert client.get("/v1/processes/proc_nope", headers=headers).status_code == 404
    assert client.post("/v1/processes/proc_nope/stop", headers=headers).status_code == 404
    assert client.get("/v1/processes/proc_nope/log", headers=headers).status_code == 404


def test_every_route_requires_device(client):
    assert client.get("/v1/processes").status_code == 401
    assert client.post("/v1/processes", json={
        "bot_id": "bot_chief", "cmd": [sys.executable, "-c", "pass"]}).status_code == 401
    assert client.get("/v1/processes/proc_x").status_code == 401
    assert client.post("/v1/processes/proc_x/stop").status_code == 401
    assert client.get("/v1/processes/proc_x/log").status_code == 401
    # 带了非法令牌也不行
    assert client.get("/v1/processes",
                      headers={"Authorization": "Bearer nope"}).status_code == 401
