"""自主多轮交叉验证深度研报工作流（DeepSearch / deep_research）完备单测。

覆盖场景：
1. 课题分解与检索矩阵构建（多深度裁剪、重点聚焦维度、四大核心交叉维度覆盖）；
2. 多源并行检索与严格 URL / 标题去重；
3. 核心权威信源筛选与正文脱水提取；
4. 工业级多轮交叉核验研报全流程端到端生成与落盘（Hermes / Grok 标准 5 大模块校验）；
5. 产物 Artifact 发布与伴生预览图生成；
6. 会话黑板 Blackboard 联动挂载；
7. 网络受限/异常时的知识库合成降级容错保护（绝不抛出致命异常）；
8. tools.dispatch("deep_research", ...) 调度集成。
"""

from __future__ import annotations

import pathlib
import pytest

from app import artifacts
from app.agent import deep_research, tools, web_tools
from app.agent import blackboard


# ================================================================
# 1. 检索矩阵构建与课题分解测试
# ================================================================

def test_build_search_matrix_standard():
    """验证标准模式下衍生 4 个核心维度的子 Query。"""
    topic = "DeepSeek-V3 架构解析与训练优化"
    matrix = deep_research.build_search_matrix(topic, depth="standard")

    assert len(matrix) == 4
    dimensions = [item["dimension"] for item in matrix]
    intents = [item["intent"] for item in matrix]

    assert "事实时效" in dimensions
    assert "底层技术" in dimensions
    assert "商业竞品" in dimensions
    assert "争议反方" in dimensions

    assert "fact_timeliness" in intents
    assert "tech_mechanism" in intents
    assert "business_market" in intents
    assert "controversy_counter" in intents

    for item in matrix:
        assert topic in item["query"]
        assert len(item["query"]) > len(topic)


def test_build_search_matrix_quick_and_deep():
    """验证 quick (3条) 与 deep (5条含未来趋势) 模式。"""
    topic = "人形机器人运控算法"

    quick_matrix = deep_research.build_search_matrix(topic, depth="quick")
    assert len(quick_matrix) == 3

    deep_matrix = deep_research.build_search_matrix(topic, depth="deep")
    assert len(deep_matrix) == 5
    assert any(m["dimension"] == "未来趋势" for m in deep_matrix)


def test_build_search_matrix_focus_areas():
    """验证 focus_areas 重点关注维度的注入。"""
    topic = "混合专家模型 MoE"
    focus_areas = ["通信开销", "负载均衡路由"]
    matrix = deep_research.build_search_matrix(topic, depth="standard", focus_areas=focus_areas)

    assert len(matrix) == 4
    focus_queries = [m for m in matrix if "焦点关注" in m["dimension"]]
    assert len(focus_queries) >= 1
    assert "通信开销" in focus_queries[0]["query"]


# ================================================================
# 2. 课题 Slug 生成与工作区安全解析
# ================================================================

def test_topic_slug_generation():
    """验证中英文、标点、空格等特殊字符能够生成合法的文件系统 slug。"""
    assert deep_research._generate_topic_slug("DeepSeek-V3: 架构 & 成本 / 2026?") == "DeepSeek-V3_架构_成本_2026"
    assert deep_research._generate_topic_slug("   ") != ""
    assert deep_research._generate_topic_slug("").startswith("report_")


def test_resolve_workspace(tmp_path):
    """验证工作区路径解析，支持自定义传入与环境变量回退。"""
    custom_ws = tmp_path / "custom_ws"
    resolved = deep_research._resolve_workspace(str(custom_ws))
    assert resolved == custom_ws
    assert resolved.exists()


# ================================================================
# 3. 多源并行检索与去重测试
# ================================================================

def test_execute_parallel_search_and_deduplication(monkeypatch):
    """验证 ThreadPoolExecutor 并发检索及 URL/标题去重。"""
    call_counts = {"count": 0}

    def mock_web_search(query: str, count: int = 8, search_type: str = "all"):
        call_counts["count"] += 1
        # 模拟返回包含重复 URL 和相似标题的数据
        return {
            "ok": True,
            "results": [
                {
                    "title": f"权威报道: {query} 取得重大突破 - 36氪",
                    "url": "https://36kr.com/p/123456789?utm_source=feed&ref=banner",
                    "snippet": f"{query} 是当前最具颠覆性的前沿方案，指标提升 40%...",
                    "source": "36kr",
                    "time": "2026-02-01",
                },
                {
                    "title": f"权威报道: {query} 取得重大突破 - 知乎",
                    "url": "https://36kr.com/p/123456789",  # 规范化后与上一条重复
                    "snippet": "重复链接内容...",
                    "source": "zhihu",
                },
                {
                    "title": f"{query} 深度架构设计 - GitHub",
                    "url": f"https://github.com/project/{call_counts['count']}",
                    "snippet": f"开源实现代码仓库与算法说明...",
                    "source": "github",
                    "time": "2026-02-15",
                },
            ],
        }

    monkeypatch.setattr(web_tools, "web_search", mock_web_search)

    sub_queries = [
        {"dimension": "事实时效", "intent": "fact_timeliness", "query": "DeepSeek 进展"},
        {"dimension": "底层技术", "intent": "tech_mechanism", "query": "DeepSeek 技术原理"},
    ]

    results = deep_research.execute_parallel_search(sub_queries, count_per_query=5)
    assert call_counts["count"] == 2
    # 验证去重有效：重复的 36kr url 仅保留 1 条
    urls = [r["url"] for r in results]
    assert len(urls) == len(set(urls))
    # 验证保留了 dimension 标记
    assert all("dimension" in r for r in results)


# ================================================================
# 4. 权威信源筛选与正文脱水测试
# ================================================================

def test_select_top_sources_and_dehydration(monkeypatch):
    """验证优先选取权威信源与并发正文脱水。"""
    candidates = [
        {"title": "普通博客文章", "url": "https://blog.example.com/p1", "snippet": "简要介绍...", "dimension": "事实时效"},
        {"title": "Arxiv 技术论文", "url": "https://arxiv.org/abs/2601.12345", "snippet": "这是一篇非常严谨的架构论文，包含详细推导与消融实验...", "dimension": "底层技术"},
        {"title": "知乎深度专栏", "url": "https://zhuanlan.zhihu.com/p/9999", "snippet": "工业落地的成本与反方视角...", "dimension": "争议反方"},
        {"title": "36氪产业报告", "url": "https://36kr.com/p/8888", "snippet": "商业落地对比与竞品分析...", "dimension": "商业竞品"},
    ]

    top_sources = deep_research.select_top_sources(candidates, target_count=3)
    assert len(top_sources) == 3
    # 权威论文优先入选
    top_urls = [s["url"] for s in top_sources]
    assert "https://arxiv.org/abs/2601.12345" in top_urls

    # 模拟 web_fetch
    def mock_web_fetch(url: str, max_chars: int = 12000, render_js: bool = False):
        if "arxiv.org" in url:
            return {
                "ok": True,
                "url": url,
                "title": "Arxiv Technical Paper",
                "content": "# Abstract\n\nWe present an ultra-scalable model architecture...\n\n## 1. Introduction\n\nKey parameters...",
            }
        return {"ok": False, "error": "Fetch timeout"}

    monkeypatch.setattr(web_tools, "web_fetch", mock_web_fetch)

    dehydrated = deep_research.dehydrate_sources_content(top_sources)
    assert len(dehydrated) == 3
    arxiv_item = next(d for d in dehydrated if "arxiv.org" in d["url"])
    assert arxiv_item["fetch_ok"] is True
    assert "ultra-scalable model architecture" in arxiv_item["dehydrated_content"]

    # 抓取失败的信源优雅回落到 snippet
    failed_item = next(d for d in dehydrated if "arxiv.org" not in d["url"])
    assert failed_item["fetch_ok"] is False
    assert failed_item["dehydrated_content"] == failed_item["snippet"]


# ================================================================
# 5. 端到端研报工作流全流程测试（落盘 + Artifact 发布 + 5大板块校验）
# ================================================================

def test_run_deep_research_full_flow(tmp_path, monkeypatch):
    """端到端验证自主多轮交叉验证深度研报全流程。"""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    monkeypatch.setenv("M0_WORKSPACE", str(ws))

    # Mock web_search
    def mock_web_search(query: str, count: int = 8, search_type: str = "all"):
        return {
            "ok": True,
            "results": [
                {
                    "title": f"{query} 官方发布会与白皮书 - DeepSeek",
                    "url": f"https://deepseek.com/news/{hash(query)}",
                    "snippet": f"正式发布最新版本，在训练成本上实现 10 倍降低，长上下文达 128k...",
                    "source": "official",
                    "time": "2026-01-10",
                },
                {
                    "title": f"{query} 行业对标与第三方评测 - 36氪",
                    "url": f"https://36kr.com/p/{hash(query)}",
                    "snippet": "实测吞吐提升 35%，但在极端多卡通信下可能出现微秒级抖动，反方对此提出质疑...",
                    "source": "36kr",
                    "time": "2026-01-12",
                },
            ],
        }

    # Mock web_fetch
    def mock_web_fetch(url: str, max_chars: int = 12000, render_js: bool = False):
        return {
            "ok": True,
            "url": url,
            "title": "Page Content",
            "content": f"# 正文提取\n\n针对该课题详细展开技术评测，实测指标与数据表现优异。\n\n- 关键指标一：吞吐翻倍\n- 关键指标二：延迟降低 40%",
        }

    monkeypatch.setattr(web_tools, "web_search", mock_web_search)
    monkeypatch.setattr(web_tools, "web_fetch", mock_web_fetch)

    topic = "DeepSeek-V3 架构机理与产业化落地"
    focus = ["显存开销", "商业化成本"]

    result = deep_research.run_deep_research(
        topic=topic,
        depth="standard",
        focus_areas=focus,
        publish_report=True,
        session_id="sess_flow_test",
        workspace_path=str(ws),
    )

    # 1. 结构化字典断言
    assert result["ok"] is True
    assert result["fallback"] is False
    assert result["topic"] == topic
    assert result["depth"] == "standard"
    assert result["focus_areas"] == focus
    assert result["sources_count"] > 0
    assert len(result["cross_validation_table"]) >= 4

    # 2. 验证落盘文件 reports/{topic_slug}.md
    report_file = pathlib.Path(result["report_path"])
    assert report_file.exists()
    assert report_file.is_file()
    assert "reports" in report_file.parts

    content = report_file.read_text(encoding="utf-8")
    assert content == result["report_markdown"]

    # 3. 严格验证 Hermes / Grok 工业级研报 5 大模块存在
    assert "## 执行摘要 (Executive Summary)" in content
    assert "## 核心关键指标与对比大表" in content
    assert "## 深度事实剖析与争议研判" in content
    assert "## 结论与行动建议" in content
    assert "## 溯源引用文献清单 (Sources & Citations)" in content

    # 4. 验证对比大表包含各核心要素
    assert "| 评估维度 / 核心指标 | 主流/共识观点 (Consensus) | 交叉分歧与反方观点 (Discrepancy / Counter) | 核验证据与可信度评级 | 权威信源支撑 |" in content

    # 5. 验证可点击超链接与文献引用
    assert "[^1]: [" in content
    assert "http" in content

    # 6. 验证 Artifact 发布及伴生预览
    assert result["artifact"] is not None
    art_info = result["artifact"]
    assert art_info["kind"] == "text/markdown"
    assert (artifacts.root() / art_info["file"]).exists()

    preview_path = artifacts.root() / f"{art_info['id']}.preview.png"
    assert preview_path.exists()

    # 7. 验证会话黑板挂载记录
    bb = blackboard.get_blackboard(None, "sess_flow_test")
    assert any(topic in step for step in bb["completed_steps"])
    assert any("DeepSeek-V3" in a.get("name", "") for a in bb["artifacts"])


# ================================================================
# 6. 外网受限 / 异常熔断的优雅降级保护测试
# ================================================================

def test_run_deep_research_fallback_on_network_error(tmp_path, monkeypatch):
    """外网完全不可达或网络受限抛出异常时，自动触发内置知识库降级，绝不崩溃。"""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    monkeypatch.setenv("M0_WORKSPACE", str(ws))

    # 模拟网络抛出严重连接异常
    def mock_web_search_fail(*args, **kwargs):
        raise ConnectionResetError("Connection reset by peer / Network unreachable")

    monkeypatch.setattr(web_tools, "web_search", mock_web_search_fail)

    topic = "异构量子计算 QPU 架构"
    res = deep_research.run_deep_research(
        topic=topic,
        depth="standard",
        publish_report=True,
        workspace_path=str(ws),
    )

    # 验证容错保护生效
    assert res["ok"] is True
    assert res["fallback"] is True
    assert res["sources_count"] > 0

    # 降级研报依旧严谨规范，满足 5 大模块
    md = res["report_markdown"]
    assert "## 执行摘要 (Executive Summary)" in md
    assert "## 核心关键指标与对比大表" in md
    assert "## 深度事实剖析与争议研判" in md
    assert "## 结论与行动建议" in md
    assert "## 溯源引用文献清单 (Sources & Citations)" in md

    # 落盘与产物依旧正常产出
    assert pathlib.Path(res["report_path"]).exists()
    assert res["artifact"] is not None


def test_run_deep_research_fallback_on_empty_search(tmp_path, monkeypatch):
    """搜索结果为空时，自动切入降级知识库模式。"""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    monkeypatch.setenv("M0_WORKSPACE", str(ws))

    monkeypatch.setattr(web_tools, "web_search", lambda *a, **k: {"ok": True, "results": []})

    res = deep_research.run_deep_research(
        topic="超前沿冷门课题",
        depth="quick",
        publish_report=False,
        workspace_path=str(ws),
    )

    assert res["ok"] is True
    assert res["fallback"] is True
    assert res["artifact"] is None  # publish_report=False
    assert pathlib.Path(res["report_path"]).exists()


# ================================================================
# 7. Agent tools.dispatch("deep_research", ...) 调度集成测试
# ================================================================

def test_deep_research_tools_dispatch(tmp_path, monkeypatch):
    """验证通过 Agent 全局工具分发器调用 deep_research。"""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(tools, "WORKSPACE", ws)
    monkeypatch.setenv("M0_WORKSPACE", str(ws))

    # Mock web search & fetch
    monkeypatch.setattr(web_tools, "web_search", lambda q, count=8: {
        "ok": True,
        "results": [
            {
                "title": f"{q} 研究简报",
                "url": "https://example.org/article",
                "snippet": "研究简报摘要...",
                "source": "example",
            }
        ]
    })
    monkeypatch.setattr(web_tools, "web_fetch", lambda u, **k: {
        "ok": True,
        "content": "正文内容...",
    })

    # 通过 tools.dispatch 分发
    dispatch_res = tools.dispatch(
        "deep_research",
        {
            "topic": "多智能体协作总线",
            "depth": "quick",
            "focus_areas": ["黑板机制"],
            "publish_report": True,
        },
        session_id="sess_dispatch_test",
    )

    assert dispatch_res["ok"] is True
    assert "report_path" in dispatch_res or "summary" in dispatch_res
