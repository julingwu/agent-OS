"""HTML source is checked structurally; advertising rules inspect page copy."""
from __future__ import annotations

import hashlib

import pytest

from app import acceptance_run
from app.acceptance import l0_rules, task_text
from app.api import verbs
from app.db import connect, init_db
from app.engine import task_generator
from app.models import ArbitrationIn


@pytest.mark.parametrize("source", [
    '<html><style>main {width:100%}</style><p>服务说明</p></html>',
    '<HTML><SCRIPT>const claim="绝对100%";</SCRIPT><p>服务说明</p></HTML>',
    '<html><template><template>最好</template>绝对</template><p>服务说明</p></html>',
    '<html><!-- 最好100% --><p title="绝对" style="width:100%">服务说明</p></html>',
    '<html><noscript>100%</noscript><button data-test="永久">开始使用</button></html>',
])
def test_html_source_details_are_not_advertising_claims(source):
    assert l0_rules.evaluate("banned", source, file_path="index.HTML")["ok"]


@pytest.mark.parametrize("copy, hits", [
    ("<p>100%有效，绝对可靠</p>", ["绝对", "100%"]),
    ("<p>100&#37;有效</p>", ["100%"]),
    ("<p>100<span>%</span>有效，最<strong>好</strong>的选择</p>", ["最好", "100%"]),
    ("<template>说明</template><p>可以根治</p>", ["根治"]),
])
def test_html_visible_claims_are_still_rejected(copy, hits):
    result = l0_rules.check_banned("<html>" + copy + "</html>", file_path="page.htm")
    assert not result["ok"]
    assert result["hits"] == hits


def test_plain_and_markdown_checks_keep_full_text_and_html_rules_keep_source():
    text = '<style>width:100%</style>\n说明中禁止使用绝对承诺。'
    for path in (None, "draft.md", "notes.txt"):
        assert l0_rules.check_banned(text, file_path=path)["hits"] == ["绝对", "100%"]
    source = "<html><style>width:100%</style><button>开始</button></html>"
    for rule in ("contains:<html", "contains:<style", "contains:<button"):
        assert l0_rules.evaluate(rule, source, file_path="index.html")["ok"]


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    # No app startup or .env loading; all state belongs to this test directory.
    for key, value in {
        "DB_PATH": str(tmp_path / "acceptance.db"),
        "M0_WORKSPACE": str(tmp_path / "workspace"),
        "DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": "",
        "UI_CRON": "0", "WECOM_WATCHER": "0", "WECOM_APPROVAL_POLLER": "0",
        "OCTO_RUNNER": "0", "RUNNER": "mock", "UI_RUNNER": "mock",
    }.items():
        monkeypatch.setenv(key, value)
    init_db()
    conn = connect()
    yield conn, tmp_path
    conn.close()


@pytest.mark.parametrize("visible_claim", [False, True])
@pytest.mark.parametrize("on_disk", [False, True])
def test_batch_and_arbitration_agree_for_html_copy(isolated_db, visible_claim, on_disk):
    conn, tmp_path = isolated_db
    task = task_generator.generate(conn, "制作一个产品落地页")
    tid = task["task_id"]
    banned = next(c for c in task["criteria"] if c["check_ref"] == "banned")
    for state in ("assigned", "in_progress", "pending_review"):
        conn.execute("UPDATE tasks SET state=? WHERE id=?", (state, tid))
    conn.execute("INSERT INTO task_runs (id,task_id,run_seq,legal_entity_id,state)"
                 " VALUES ('RUN-HTML',?,1,'LE-GROUP-HQ','succeeded')", (tid,))
    copy = "100<span>%</span>有效，绝对可靠" if visible_claim else "欢迎了解产品服务"
    source = ("<!DOCTYPE html><html><style>main{width:100%}</style>"
              "<script>const internal='永久';</script><main><p>" + copy
              + "</p><button>开始使用</button></main>" + " " * 2200 + "</html>")
    path = tmp_path / "index.html"
    if on_disk:
        path.write_text(source, encoding="utf-8")
    conn.execute("INSERT INTO artifacts (id,run_id,art_seq,file_path,content,checksum,bytes)"
                 " VALUES ('ART-HTML','RUN-HTML',1,?,?,?,?)",
                 (str(path), source, hashlib.sha256(source.encode()).hexdigest(), len(source.encode())))
    conn.commit()

    # Raw source remains available for structural rules and evidence.
    assert task_text(conn, tid) == source
    assert "width:100%" not in task_text(conn, tid, check_ref="banned")
    arbitration = verbs.do_request_arbitration(conn, ArbitrationIn(
        run_id="RUN-HTML", arb_seq=1, criterion_id=banned["id"]))
    expected = "failed" if visible_claim else "passed"
    assert arbitration["result"] == expected
    result = acceptance_run.run(conn, tid, "LE-GROUP-HQ")
    checked = {r["criterion_id"]: r for r in result["results"]}
    assert checked[banned["id"]]["state"] == expected
    for criterion in task["criteria"]:
        if (criterion["check_ref"] or "").startswith("contains_any:"):
            assert checked[criterion["id"]]["state"] == "passed"
    if visible_claim:
        assert "100%" in checked[banned["id"]]["detail"]
        assert "绝对" in checked[banned["id"]]["detail"]
