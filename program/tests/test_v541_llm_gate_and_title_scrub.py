"""v5.4.1：llm 统一离线阀门 + 经验标题注入套娃剥离。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import experience, llm
from app.db import get_conn, init_db


# ------------------------------------------------------------------ 1) 统一离线阀门
class _Recorder:
    """记录打进来的 HTTP 请求数，用来证明「阀门关着时一个包都没发」。"""

    def __init__(self):
        self.hits = 0

    def handler(self):
        outer = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):                       # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                outer.hits += 1
                payload = {"choices": [{"message": {"role": "assistant",
                                                    "content": "LIVE-OK"}}],
                           "usage": {"prompt_tokens": 111, "completion_tokens": 22}}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return _H


def test_complete_respects_offline_valve_and_reports_usage(monkeypatch):
    """complete() 与 chat() 共用同一阀门；离线时零 HTTP，在线时 usage 不再被丢掉。

    历史缺陷：M0_MOCK_LLM 只在 runner_native 里生效，llm.complete() 只要环境有 key
    就直接发 HTTP。v5.4.0 封版时正是它让一条用例真的调了 DeepSeek，且测试结果取决于
    本机有没有 .env。
    """
    rec = _Recorder()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), rec.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1/chat"
    try:
        monkeypatch.setenv("LLM_URL", url)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

        # (a) 只有 M0_MOCK_LLM=1：关
        monkeypatch.delenv("M0_LLM_MODE", raising=False)
        monkeypatch.setenv("M0_MOCK_LLM", "1")
        assert llm.is_offline() is True
        assert llm.complete("你好") is None
        assert rec.hits == 0, "离线阀门开着时绝不允许发 HTTP"

        # (b) 显式 M0_LLM_MODE=off 覆盖 M0_MOCK_LLM=0：仍然关
        monkeypatch.setenv("M0_MOCK_LLM", "0")
        monkeypatch.setenv("M0_LLM_MODE", "off")
        assert llm.is_offline() is True
        assert llm.complete("你好") is None
        assert rec.hits == 0

        # (c) 显式放行：才真的联网，并且 usage 被记下来（以前被整个丢掉）
        before = llm.usage_totals()["calls"]
        monkeypatch.setenv("M0_LLM_MODE", "live")
        text, usage = llm.complete_with_usage("你好")
        assert text == "LIVE-OK"
        assert usage == {"prompt_tokens": 111, "completion_tokens": 22}
        assert rec.hits == 1
        after = llm.usage_totals()
        assert after["calls"] == before + 1
        assert after["prompt_tokens"] >= 111 and after["completion_tokens"] >= 22
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------------- 2) 经验标题注入套娃剥离
POLLUTED_TITLE = (
    "驳回：开发最小可用版本：单文件 Python 命令行实现；项目 P-dd0bbd302b42，"
    "需求 失败的原因是什么  【组织经验（自动注入）】 - [决策否决] 任何 Agent")
POLLUTED_CONTENT = (
    "任务《开发最小可用版本 【组织经验（自动注入）】 - [决策否决] 任何 Agent》被驳回。"
    "未通过判据：企业红线检查（命中企业红线：绝对）。下次同类任务先按这些判据自检。")


def test_experience_title_never_inherits_injected_context(tmp_path, monkeypatch):
    """经验标题必须只取原始任务标题，绝不把上一轮注入的召回上下文再抄一遍。

    生产库里真的长成过这样：标题尾部挂着「【组织经验（自动注入）】- [决策否决] …」，
    下一轮召回又把它塞回 prompt，任务标题再截一次 —— 套娃无限增长。
    """
    monkeypatch.setenv("DB_PATH", str(tmp_path / "exp.db"))
    init_db()
    with get_conn() as conn:
        exp = experience.add(
            conn, kind="rejection", title=POLLUTED_TITLE, content=POLLUTED_CONTENT,
            source_type="task", source_id="T-SCRUB")
        assert "【组织经验" not in exp["title"]
        assert "[决策否决]" not in exp["title"]
        assert exp["title"].startswith("驳回：开发最小可用版本"), exp["title"]
        assert exp["title"].endswith("失败的原因是什么"), exp["title"]

        # 正文保留「未通过判据」这类真正有用的信息，只挖掉注入段
        assert "【组织经验" not in exp["content"]
        assert "未通过判据：企业红线检查" in exp["content"]

        # 再沉淀一次同源经验：不会因为标题变了而重复入库，也不会套娃
        again = experience.add(conn, kind="rejection", title=POLLUTED_TITLE,
                               content=POLLUTED_CONTENT, source_type="task",
                               source_id="T-SCRUB")
        assert again["title"] == exp["title"]
        assert conn.execute(
            "SELECT COUNT(*) FROM org_experiences WHERE source_id='T-SCRUB'"
        ).fetchone()[0] == 1
