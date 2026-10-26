"""v5.2 红线自指缺陷：任务自己生成的验收自检脚本不得被当成对外文案扫描。

物理事实（生产库实测）：501 个 web-v1 任务里 431 次红线失败，归因抽样 40/40 全部落在
任务自己写出的 test_check.py —— 而该脚本按设计必须硬编码「要检的禁用词清单」，
拿它去扫它自己的禁用词，命中是逻辑必然，不是风险信号。这属于验收层的自指陷阱。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from app import acceptance_run, experience, identity
from app.db import connect, init_db


@pytest.fixture()
def env(tmp_path, monkeypatch):
    path = tmp_path / "selfref.db"
    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setenv("M0_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("M0_PIPELINE_EXEC", "0")
    init_db()
    conn = connect(str(path))
    conn.execute("INSERT OR IGNORE INTO legal_entities(id,name,type)"
                 " VALUES ('LE-GROUP-HQ','HQ','group_hq')")
    ceo = identity.issue(conn, name="CEO", role="ceo")
    conn.commit()
    yield conn, ceo, tmp_path
    conn.close()


def _rule(conn, who, terms="100%,绝对"):
    row = experience.add(conn, kind="decision", title="宣传内容红线",
                         content="宣传不得夸大承诺", mandatory=True, redline_terms=terms,
                         entity="LE-GROUP-HQ", actor=who["id"])
    return experience.review(conn, row["id"], "approve", reviewer=who["id"],
                             entity="LE-GROUP-HQ")


def _tasks(conn):
    conn.execute("INSERT OR IGNORE INTO tasks(id,legal_entity_id,tenant_id,task_type,title,state)"
                 " VALUES ('T1','LE-GROUP-HQ','TENANT-HQ','web_write','落地页','pending_review')")
    conn.execute("INSERT INTO task_runs(id,task_id,run_seq,legal_entity_id,tenant_id,state)"
                 " VALUES ('R1','T1',1,'LE-GROUP-HQ','TENANT-HQ','succeeded')")


def _artifact(conn, folder, name, text, seq):
    path = folder / "R1" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    conn.execute("INSERT INTO artifacts(id,run_id,tenant_id,art_seq,file_path,content,"
                 "checksum,bytes) VALUES (?,?,'TENANT-HQ',?,?,?,?,?)",
                 (f"A{seq}", "R1", seq, str(path), text,
                  hashlib.sha256(text.encode()).hexdigest(), len(text.encode())))
    return path


def test_self_check_script_is_not_scanned_as_advertising_copy(env):
    """交付页干净、只有自检脚本含禁用词 —— 不得因此驳回。"""
    conn, ceo, folder = env
    _rule(conn, ceo)
    _tasks(conn)
    # 可见文案干净，百分比只出现在 CSS 里（既有行为：CSS 不算文案）
    _artifact(conn, folder, "index.html",
              "<style>.hero{width:100%}</style><button>立即咨询</button><p>秋季保湿方案</p>", 1)
    # 自检脚本按设计必须列出禁用词
    _artifact(conn, folder, "test_check.py",
              'banned = ["100%", "绝对", "最好"]\n'
              'assert "100%" not in open("index.html", encoding="utf-8").read()\n', 2)
    conn.commit()

    result = acceptance_run.validate_mandatory(conn, "T1")
    assert result["ok"] is True, result["detail"]
    assert result["hits"] == []

    scan = conn.execute("SELECT snapshot FROM redline_scans ORDER BY rowid DESC LIMIT 1") \
        .fetchone()
    snapshot = json.loads(scan["snapshot"])
    # 跳过必须可审计：既要真的跳过，也要留痕
    assert any(p.endswith("test_check.py") for p in snapshot["skipped_tooling"])
    scanned = [a["name"] for a in snapshot["artifacts"]]
    assert "index.html" in scanned
    assert "test_check.py" not in scanned


def test_banned_copy_in_the_page_still_blocks(env):
    """安全边界不能被这次修复放宽：页面可见文案里的禁用词照样拦。"""
    conn, ceo, folder = env
    _rule(conn, ceo)
    _tasks(conn)
    _artifact(conn, folder, "index.html",
              "<style>.hero{width:100%}</style><p>本产品效果绝对 100% 保证</p>", 1)
    _artifact(conn, folder, "test_check.py", 'banned = ["100%"]\n', 2)
    conn.commit()

    result = acceptance_run.validate_mandatory(conn, "T1")
    assert result["ok"] is False
    assert "100%" in result["detail"] or "绝对" in result["detail"]
