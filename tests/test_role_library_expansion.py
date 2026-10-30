"""Tests for role library expansion and TAG_WORDS routing."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from app.db import get_conn, init_db
from app.ui import roles, store


EXPECTED_NEW_ROLES = [
    {
        "name": "黄金与贵金属宏观策略分析师",
        "tag": "data",
        "industry": "金融",
        "tools": "web_fetch,file_write,render_chart,web_search",
        "deliverable": "黄金与贵金属投资策略研报.md",
    },
    {
        "name": "全球宏观经济与大类资产配置策略师",
        "tag": "data",
        "industry": "金融",
        "tools": "web_fetch,file_write,render_chart,web_search",
        "deliverable": "全球宏观经济与大类资产配置策略方案.md",
    },
    {
        "name": "原油与大宗商品产业分析师",
        "tag": "data",
        "industry": "金融",
        "tools": "web_fetch,file_write,render_chart,web_search",
        "deliverable": "大宗商品与原油产业供需深度研报.md",
    },
    {
        "name": "加密资产与Web3链上分析师",
        "tag": "data",
        "industry": "金融",
        "tools": "web_fetch,file_write,render_chart,web_search",
        "deliverable": "Web3链上数据与加密资产量化研报.md",
    },
    {
        "name": "跨境电商与出海独立站操盘手",
        "tag": "growth",
        "industry": "市场",
        "tools": "web_fetch,file_write,web_search",
        "deliverable": "跨境出海与独立站全链路操盘方案.md",
    },
    {
        "name": "危机公关与品牌声誉操盘手",
        "tag": "content",
        "industry": "市场",
        "tools": "web_fetch,file_write,web_search",
        "deliverable": "危机公关处置预案与官方声明口径.md",
    },
    {
        "name": "B2B大客户销售与商业谈判顾问",
        "tag": "growth",
        "industry": "市场",
        "tools": "file_write,web_search",
        "deliverable": "B2B大客户销售攻坚与商业谈判方案.md",
    },
    {
        "name": "薪酬绩效与组织激励专家",
        "tag": "hr",
        "industry": "通用",
        "tools": "file_write,web_search",
        "deliverable": "薪酬绩效体系与核心激励方案.md",
    },
    {
        "name": "AI Agent工作流与系统自动化架构师",
        "tag": "architect",
        "industry": "个人智库",
        "tools": "code_exec,file_write,web_fetch",
        "deliverable": "AI_Agent系统工作流架构设计方案.md",
    },
    {
        "name": "红蓝对抗与应用安全渗透专家",
        "tag": "audit",
        "industry": "研发",
        "tools": "code_exec,file_write,web_fetch",
        "deliverable": "应用安全渗透测试与漏洞加固方案.md",
    },
    {
        "name": "全媒体爆款视频导演与分镜师",
        "tag": "design",
        "industry": "自媒体与创作",
        "tools": "file_write,web_search",
        "deliverable": "爆款视频视听语言与工业级分镜脚本.md",
    },
    {
        "name": "供应链与大宗采购谈判官",
        "tag": "ops",
        "industry": "通用",
        "tools": "file_write,web_search",
        "deliverable": "战略采购降本与供应链谈判方案.md",
    },
]


@pytest.fixture
def clean_conn(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test_roles_expansion.db"))
    init_db()
    with get_conn() as conn:
        store.ensure(conn)
        roles.ensure(conn)
        conn.execute("INSERT OR IGNORE INTO legal_entities (id,name,type) VALUES ('LE-GROUP-HQ','集团总部','group_hq')")
        conn.commit()
        yield conn


def test_roles_seed_json_integrity():
    seed_file = Path(__file__).resolve().parents[1] / "app" / "data" / "roles_seed.json"
    assert seed_file.exists(), "roles_seed.json must exist"
    data = json.loads(seed_file.read_text(encoding="utf-8"))
    assert len(data) >= 54, f"roles_seed.json should have at least 54 entries, got {len(data)}"

    names = {r["name"]: r for r in data}
    for item in EXPECTED_NEW_ROLES:
        name = item["name"]
        assert name in names, f"Expected role {name} missing from roles_seed.json"
        role_data = names[name]
        assert role_data["tag"] == item["tag"], f"{name} tag mismatch"
        assert role_data["industry"] == item["industry"], f"{name} industry mismatch"
        assert role_data["tools"] == item["tools"], f"{name} tools mismatch"
        assert role_data["deliverable"] == item["deliverable"], f"{name} deliverable mismatch"
        assert len(role_data["prompt"].strip()) >= 30, f"{name} prompt is too short"
        skills = [s.strip() for s in role_data["skills"].split(",") if s.strip()]
        assert len(skills) >= 5, f"{name} should have at least 5 skills, got {len(skills)}"


def test_gold_analyst_exact_specifications():
    seed_file = Path(__file__).resolve().parents[1] / "app" / "data" / "roles_seed.json"
    data = json.loads(seed_file.read_text(encoding="utf-8"))
    names = {r["name"]: r for r in data}

    gold = names["黄金与贵金属宏观策略分析师"]
    assert gold["tag"] == "data"
    assert gold["industry"] == "金融"
    assert gold["tools"] == "web_fetch,file_write,render_chart,web_search"
    assert gold["deliverable"] == "黄金与贵金属投资策略研报.md"
    assert "伦敦金与沪金行情技术面分析" in gold["skills"]
    assert "美联储货币政策与实际利率推演" in gold["skills"]
    assert "地缘政治与避险溢价量化" in gold["skills"]
    assert "全球央行购金与ETF持仓跟踪" in gold["skills"]
    assert "金银比与大宗商品轮动测算" in gold["skills"]
    assert "多空头寸与期权对冲策略" in gold["skills"]
    assert "render_chart" in gold["prompt"]
    assert "XAU/USD" in gold["prompt"]


def test_role_templates_inserted_and_count(clean_conn):
    all_roles = roles.list_roles(clean_conn)
    # 14 base SEED roles + 54 extra seed roles = 68 roles (~70)
    assert len(all_roles) >= 68, f"Total active roles should be at least 68, got {len(all_roles)}"

    role_by_name = {r["name"]: r for r in all_roles}
    for item in EXPECTED_NEW_ROLES:
        name = item["name"]
        assert name in role_by_name, f"Role {name} not found in role_templates"
        db_role = role_by_name[name]
        assert db_role["tag"] == item["tag"]
        assert db_role["industry"] == item["industry"]
        assert db_role["tools"] == item["tools"]
        assert db_role["deliverable"] == item["deliverable"]
        assert db_role["prompt"]
        assert db_role["skills"]
        skills = db_role["skills"].split(",")
        assert len(skills) >= 5


def test_tag_words_coverage():
    assert "data" in roles.tags_of("请分析当前黄金和贵金属走势")
    assert "data" in roles.tags_of("宏观经济周期与大宗商品轮动")
    assert "data" in roles.tags_of("原油裂解价差与期权对冲")
    assert "data" in roles.tags_of("外汇与汇率波动")
    assert "content" in roles.tags_of("突发危机公关声明与问答口径")
    assert "audit" in roles.tags_of("系统安全渗透测试与漏洞挖掘")
    assert "ops" in roles.tags_of("大宗采购比价与供应链降本")
    assert "growth" in roles.tags_of("出海独立站跨境电商投放")
    assert "growth" in roles.tags_of("B2B大客户商机谈判")
    assert "architect" in roles.tags_of("AI Agent多智能体工作流架构")


def test_instantiate_new_role(clean_conn):
    role = next(r for r in roles.list_roles(clean_conn) if r["name"] == "黄金与贵金属宏观策略分析师")
    emp_id = roles.instantiate(clean_conn, role["id"], "LE-GROUP-HQ")
    assert emp_id.startswith("AI-")

    emp = clean_conn.execute("SELECT * FROM ai_employees WHERE id=?", (emp_id,)).fetchone()
    assert emp is not None
    assert emp["role_id"] == role["id"]
    assert emp["name"] == "黄金与贵金属宏观策略分析师"
    assert "XAU/USD" in emp["soul"]
