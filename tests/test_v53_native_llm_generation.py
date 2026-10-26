"""v5.3 TASK-5.1：NativeKernel 双模驱动 —— 真 LLM 生成 vs 离线确定性模板。

两条物理事实决定了设计：
1. 测试必须秒级、零外网、可复现，所以默认走 template；llm 只在显式开启且有 key 时走。
2. LLM 模式下的 token 必须来自 API 的 usage 字段（真实账本），不能再叠加模板模式的估算值，
   否则 finance 的 80% 预算熔断会被虚高数字带偏。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.audit import new_id
from app.db import get_conn, init_db
from app.engine import runner_native, task_generator

# 一个真·护肤品落地页：与 FinOps 计算器毫无关系，正好用来验证「不再吐同一套模板」。
GOOD_HTML = (
    '<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<title>秋季敏感肌修护方案</title>\n<style>\n'
    'body{background:#fdf7f2;color:#3a2f2a;font-family:sans-serif;margin:0;padding:2rem;}\n'
    '.hero{padding:3rem 2rem;text-align:center;}\n'
    '.cta{background:#c8734a;color:#fff;border:0;border-radius:999px;padding:1rem 2.4rem;'
    'font-size:1.1rem;cursor:pointer;}\n'
    '</style>\n</head>\n<body>\n'
    '<section class="hero">\n<h1>换季敏感肌修护方案</h1>\n'
    '<p>秋季干燥，屏障容易受损。用温和配方补水锁水，帮助肌肤恢复耐受。</p>\n'
    '<button class="cta" type="button">立即购买</button>\n</section>\n'
    '<section>\n<h2>三步修护</h2>\n'
    '<p>第一步温和清洁，第二步补水保湿，第三步修护屏障。</p>\n'
    '<p>敏感肌在换季期应当减少去角质频率，注意防晒与滋润。</p>\n'
    '</section>\n'
    + "<p>秋季护肤常识：早晚各一次基础护理，保持室内湿度，多喝水。</p>\n" * 24
    + '</body>\n</html>\n'
)

BAD_HTML = ('<!doctype html>\n<html><body><p>页面还没做完。</p></body></html>\n')


# --------------------------------------------------------------------- fixtures
@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "v53.db"))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    init_db()
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO legal_entities (id,name,type) VALUES (?,?,?)",
                  ("LE-GROUP-HQ", "集团总部", "group_hq"))
        c.commit()
        yield c


def _web_task(conn, title: str = "做一个秋季护肤落地页，含 Banner 与立即购买 CTA") -> str:
    out = task_generator.generate(conn, title, pack_id="web-v1")
    return out["task_id"]


class _MockLLM:
    """按脚本返回 Chat Completions 响应。

    只把「带 tools 的请求」当作 NativeKernel 的工具调用轮来计数并推进脚本：
    同一个 LLM_URL 上可能还有别的后台线程在调 llm.complete()（它们不带 tools），
    那部分流量既不该推进脚本，也不该进断言——否则测试会被跨用例的线程泄漏带崩。
    """

    def __init__(self, script: list[dict]):
        self.script = script
        self.requests: list[dict] = []      # 只记带 tools 的工具调用请求
        self.other_requests = 0             # 其它（如 llm.complete）的流量，仅供观测

    def handler(self):
        outer = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *args):        # 静音
                return

            def do_POST(self):                   # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    body = {}
                if body.get("tools"):
                    idx = min(len(outer.requests), len(outer.script) - 1)
                    outer.requests.append(body)
                    payload = outer.script[idx]
                else:
                    outer.other_requests += 1
                    payload = {"choices": [{"message": {"role": "assistant",
                                                        "content": "ok"}}],
                               "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return _H


def _start_mock(script: list[dict]):
    mock = _MockLLM(script)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mock.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, mock


def _write_call(call_id: str, path: str, content: str) -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": "write_file",
                         "arguments": json.dumps({"path": path, "content": content},
                                                 ensure_ascii=False)}}


def _run_row(conn, task_id: str) -> str:
    """只建 run 行，不动 tasks.state —— 状态跃迁由 trg_tasks_state_guard 管，
    测试没有理由去伪造一个跃迁（也不该绕过它）。"""
    run_id = new_id("RUN")
    conn.execute(
        "INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id, state, runner)"
        " VALUES (?,?,1,'LE-GROUP-HQ','running','native')", (run_id, task_id))
    conn.commit()
    return run_id


# ------------------------------------------------------------- 双模开关
def test_explicit_template_mode_wins_even_with_key(monkeypatch):
    monkeypatch.setenv("M0_NATIVE_MODE", "template")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert runner_native._native_mode() == "template"


def test_explicit_llm_mode(monkeypatch):
    monkeypatch.setenv("M0_NATIVE_MODE", "llm")
    assert runner_native._native_mode() == "llm"


def test_auto_without_key_stays_offline(monkeypatch):
    """没有真实凭据时必须自动留在模板分支 —— 这是 591 个存量用例的确定性来源。"""
    monkeypatch.delenv("M0_NATIVE_MODE", raising=False)
    monkeypatch.delenv("M0_MOCK_LLM", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert runner_native._native_mode() == "template"


def test_mock_flag_forces_offline_even_with_key(monkeypatch):
    monkeypatch.delenv("M0_NATIVE_MODE", raising=False)
    monkeypatch.setenv("M0_MOCK_LLM", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert runner_native._native_mode() == "template"


# ---------------------------------------------------- 自检脚本按判据派生
def _bare_kernel(conn, task_id: str) -> runner_native.NativeKernel:
    k = runner_native.NativeKernel.__new__(runner_native.NativeKernel)
    k.conn = conn
    k.task_id = task_id
    return k


def test_check_spec_is_derived_from_the_task_criteria(conn):
    k = _bare_kernel(conn, _web_task(conn))
    spec = k._check_spec()
    kinds = [c["kind"] for c in spec["checks"]]
    assert "file_exists" in kinds, "应派生 file_exists（来自本任务 L2 判据）"
    assert "banned" in kinds, "应派生广告法禁用词检查"
    assert "contains_any" in kinds, "应派生结构检查"
    ext = [c for c in spec["checks"] if c["kind"] == "file_exists"][0]["ext"]
    assert ext.lower() == ".html"


def test_check_script_never_hardcodes_finops(conn):
    """派生脚本里绝不能出现写死的 FinOps 断言 —— 那正是旧缺陷的形态。"""
    k = _bare_kernel(conn, _web_task(conn))
    script = k._render_check_py_from_criteria()
    assert "__SPEC_B64__" not in script
    assert "btn-calc" not in script
    assert "FinOps" not in script
    assert "slider-tasks" not in script


def test_derived_check_passes_good_page_and_blocks_bad_one(conn, tmp_path):
    k = _bare_kernel(conn, _web_task(conn))
    work = tmp_path / "chk"
    work.mkdir()
    (work / "test_check.py").write_text(k._render_check_py_from_criteria(), encoding="utf-8")

    def _run() -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "test_check.py"], cwd=str(work),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)

    (work / "index.html").write_text(GOOD_HTML, encoding="utf-8")
    good = _run()
    assert good.returncode == 0, good.stdout[-1200:]
    assert "ALL CHECKS PASSED" in good.stdout

    (work / "index.html").write_text(BAD_HTML, encoding="utf-8")
    bad = _run()
    assert bad.returncode == 1
    assert "SOME CHECKS FAILED" in bad.stdout


# ------------------------------------------------------- LLM 工具调用循环
def test_llm_loop_generates_task_specific_artifacts(conn, monkeypatch):
    tid = _web_task(conn)
    run_id = _run_row(conn, tid)
    script = [
        {"choices": [{"message": {"role": "assistant", "content": None,
                                  "tool_calls": [_write_call("c1", "index.html", GOOD_HTML)]},
                      "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 1200, "completion_tokens": 800}},
        {"choices": [{"message": {"role": "assistant", "content": "已生成 index.html"},
                      "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 300, "completion_tokens": 20}},
    ]
    srv, mock = _start_mock(script)
    try:
        monkeypatch.setenv("M0_NATIVE_MODE", "llm")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1/chat")
        runner_native.execute_native(
            conn, run_id, task_id=tid, entity="LE-GROUP-HQ", actor="A-LLM",
            prompt="做一个秋季护肤落地页，含 Banner 与立即购买 CTA")
    finally:
        srv.shutdown()

    # 1) 真的发出了两轮 HTTP 请求，且请求里带了工具定义与任务主题
    assert len(mock.requests) == 2
    first = mock.requests[0]
    assert [t["function"]["name"] for t in first.get("tools") or []] == [
        "write_file", "read_file", "run_script"]
    assert "秋季护肤" in first["messages"][-1]["content"]

    # 2) 产物与任务主题一致，且不再残留 FinOps 字样
    arts = [r["file_path"] for r in conn.execute(
        "SELECT file_path FROM artifacts WHERE run_id=?", (run_id,))]
    assert any(p.endswith("index.html") for p in arts), arts
    page = next(p for p in arts if p.endswith("index.html"))
    with open(page, encoding="utf-8") as fh:
        body = fh.read()
    assert "秋季护肤" in body
    assert "立即购买" in body
    assert "FinOps" not in body
    assert "slider-tasks" not in body

    # 3) 自检脚本也按本任务判据派生，并已落盘
    assert any(p.endswith("test_check.py") for p in arts), arts

    # 4) token 账本是 API 的真实 usage，没有被模板估算值污染
    row = conn.execute("SELECT state, usage_tokens, error FROM task_runs WHERE id=?",
                       (run_id,)).fetchone()
    assert row["state"] == "succeeded", row["error"]
    assert row["usage_tokens"] == 1200 + 800 + 300 + 20


def test_template_mode_never_calls_the_llm(conn, monkeypatch):
    """离线模式下绝不触网 —— 这是 591 个存量用例能秒级跑完的前提。

    直接探 llm.chat 而不是数 HTTP 请求：同一进程里别的后台线程也可能访问 LLM_URL，
    数流量会被它们干扰，探函数调用才是这条不变量本身。
    """
    tid = _web_task(conn)
    run_id = _run_row(conn, tid)
    calls: list = []
    monkeypatch.setattr(runner_native.llm, "chat",
                        lambda *a, **k: calls.append((a, k)) or None)
    monkeypatch.setenv("M0_NATIVE_MODE", "template")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    runner_native.execute_native(
        conn, run_id, task_id=tid, entity="LE-GROUP-HQ", actor="A-OFF",
        prompt="做一个秋季护肤落地页")

    assert calls == [], "template 模式绝不允许调用 llm.chat"
    row = conn.execute("SELECT state FROM task_runs WHERE id=?", (run_id,)).fetchone()
    assert row["state"] == "succeeded"

    # 产物确实是确定性模板分支的（含 FinOps 骨架），证明走的是 template 而非 llm
    arts = [r["file_path"] for r in conn.execute(
        "SELECT file_path FROM artifacts WHERE run_id=?", (run_id,))]
    page = next(p for p in arts if p.endswith("index.html"))
    with open(page, encoding="utf-8") as fh:
        assert "FinOps" in fh.read()


def test_llm_failure_falls_back_to_template(conn, monkeypatch):
    """模型这条路走不通时必须回落模板并把原因记进审计，而不是把任务炸掉。"""
    tid = _web_task(conn)
    run_id = _run_row(conn, tid)
    # 返回一个既没有 tool_calls 也没有写文件的第一轮 -> LLMUnavailable('no_file_written')
    srv, mock = _start_mock([{"choices": [{"message": {"content": "抱歉我做不到"}}],
                              "usage": {"prompt_tokens": 10, "completion_tokens": 5}}])
    try:
        monkeypatch.setenv("M0_NATIVE_MODE", "llm")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_URL", f"http://127.0.0.1:{srv.server_address[1]}/v1/chat")
        runner_native.execute_native(
            conn, run_id, task_id=tid, entity="LE-GROUP-HQ", actor="A-FB",
            prompt="做一个秋季护肤落地页")
    finally:
        srv.shutdown()

    arts = [r["file_path"] for r in conn.execute(
        "SELECT file_path FROM artifacts WHERE run_id=?", (run_id,))]
    assert any(p.endswith("index.html") for p in arts), "应已回落模板产出 index.html"
    row = conn.execute("SELECT state FROM task_runs WHERE id=?", (run_id,)).fetchone()
    assert row["state"] == "succeeded"
    # 回落原因要留痕，不能静默降级
    hit = conn.execute(
        "SELECT COUNT(*) c FROM ai_audit_logs"
        " WHERE run_id=? AND response_json LIKE '%no_file_written%'", (run_id,)).fetchone()["c"]
    assert hit >= 1, "回落原因未留痕"
