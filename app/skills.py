"""技能：把"怎么做事"沉淀成可复用的指令包（v1.8）。

为什么不是向量知识库：
  技能要的是**可执行流程**，不是相似文档。模型真正需要的是"遇到 X 就按这几步做"，
  于是技能 = 名字 + 触发描述 + 指令正文（可含 {input} 占位）。
  取用时由 `use_skill` 把正文塞回上下文，模型自己照着执行 ——
  不额外起一次模型调用，省钱、可控、也方便人评审。

隔离：
  bot_id 为空 = 全局技能（所有 Bot 可用）；否则只属于那个 Bot。
  name 在同一归属内唯一。
"""

from __future__ import annotations

import secrets

from . import db
from . import events as ev

MAX_NAME = 40
MAX_DESC = 200
MAX_BODY = 8000

_READY_DBS: set[str] = set()


DEFAULT_GLOBAL_SKILLS = [
    (
        "skl_def_weekrep",
        "周报三段式",
        "将工作事项整理为交付物、问题复盘与下周重点的标准三段式周报",
        "你是资深工作流管理专家。针对输入「{input}」，将其严格整理为高效的「周报三段式」：\n"
        "1. 【本周核心交付物与里程碑】：用定量数据与客观事实陈述已落地成果，拒绝套话；\n"
        "2. 【遇到问题、风险与经验复盘】：指出影响进度的卡点，给出根本原因与具体应对策略；\n"
        "3. 【下周关键行动项与预期交付】：按优先级排列（P0/P1），附带明确的衡量判据与预计时间。\n"
        "要求结构紧凑、人话表达、便于管理层快速签批。",
    ),
    (
        "skl_def_firstprin",
        "第一性原理决策推演",
        "剥离直觉与情绪偏见，从底层物理客观规律推演方案、代价与验证判据",
        "你是第一性原理分析专家。针对输入的问题或需求「{input}」：\n"
        "1. 【归零假设】：列出人们通常的经验偏见与常识假设，并逐条质疑其真实必然性；\n"
        "2. 【客观物理底层事实】：找出该系统不可违背的物理边界、资源约束（时间/带宽/预算/算力）与真实真相；\n"
        "3. 【推演最优路径】：从底层事实重新向上构建实现路径，明确列出收益、代价与不可规避的边际成本；\n"
        "4. 【行动建议与证伪判据】：给出第一步的低成本验证实验与验证失败的止损线。",
    ),
    (
        "skl_def_codeaudit",
        "代码质量与安全审计",
        "全面扫描代码隐患、OWASP 漏洞、注入风险、边界防御与架构异味",
        "你是严苛的安全与架构审计专家。对输入代码「{input}」进行多维度深度审查：\n"
        "1. 【高危安全红线】：检查是否存在 SQL 拼接注入、SSRF 盲区、命令注入、硬编码密钥与未鉴权越权；\n"
        "2. 【异常处理与边界防御】：检查是否缺少空值处理、资源未释放（未关流/内存泄漏）、管道卡死风险；\n"
        "3. 【架构与代码异味】：检查是否存在过长函数、魔法数字、破坏单一职责、违反依赖倒置等设计缺陷；\n"
        "4. 【重构改进方案】：直接给出修复后干净、带类型注解且防御严密的生产级代码实现。",
    ),
    (
        "skl_def_finance",
        "增值税与财务合规审核",
        "核验发票要素真实性、报销合理性与合同/资金/发票三流一致性",
        "你是企业财税合规总监。对报销/财务支出事项「{input}」执行专业合规核验：\n"
        "1. 【三流一致性校验】：核查合同流、发票流、资金流的主体是否绝对一致；\n"
        "2. 【发票关键要素审核】：检查税号、品名类别是否与业务实质相符，判断是否属于虚开或违规列支范畴；\n"
        "3. 【税务风险评级】：标记为【低风险合规】/【中风险需补证】/【高风险红线不予报销】，并给出税法依据；\n"
        "4. 【入账与凭证归档建议】：给出标准的会计科目记账建议与备查附件清单。",
    ),
    (
        "skl_def_ecomreview",
        "电商竞品差评与痛点挖掘",
        "分析用户真实差评与退货反馈，提取痛点特征与详情页差异化打法",
        "你是资深电商操盘手。针对竞品或本品的评价反馈「{input}」：\n"
        "1. 【差评归因聚类】：将吐槽按质量缺陷、尺寸包装、物流履约、客服沟通四大维度聚类统计；\n"
        "2. 【用户真实底层痛点】：提炼买家未被满足的核心期望，分析其退货/给差评的真实心理动机；\n"
        "3. 【产品改版升级清单】：给出 3 条低成本但立竿见影的产品微创新改良建议；\n"
        "4. 【差异化详情页与卖点提炼】：撰写 2 条直接针对竞品软肋降维打击的商品主图文案。",
    ),
    (
        "skl_def_copymulti",
        "爆款文案多平台适配",
        "将核心业务价值适配为公众号深度文、小红书种草、知乎专业问答等多模态排版",
        "你是全媒体内容运营主编。将输入素材「{input}」分别改写并适配为以下平台专属文案：\n"
        "1. 【小红书种草笔记】：爆款黄金前3秒标题 + 表情符号排版 + 痛点共鸣 + 真实测评口吻 + 热门标签堆；\n"
        "2. 【微信公众号深度文】：引人入胜的故事开头 + 结构化小标题论证 + 金句提炼 + 结尾引导互动；\n"
        "3. 【知乎专业答主回答】：谢邀起手 + 业内内幕/第一性原理拆解 + 逻辑严密对比表 + 理性总结。\n"
        "文风自然拒绝生硬机翻味，严禁广告法极限违禁词。",
    ),
    (
        "skl_def_burndown",
        "敏捷项目燃尽与风险复盘",
        "跟踪 WBS 阶段偏差，识别阻碍卡点并输出冲刺燃尽分析与预警清单",
        "你是 PMP/敏捷教练。针对当前项目冲刺进度「{input}」：\n"
        "1. 【工期偏差与燃尽分析】：计算各任务的实际工时与估算偏差率，判断整体是否出现延期风险；\n"
        "2. 【关键路径阻塞诊断】：找出阻碍链路上的单一故障点（卡在谁、卡在哪一步），分析具体技术或协同原因；\n"
        "3. 【范围蔓延与剪法清单】：若已临界延期，给出可下调优先级延至下期的功能剪枝建议；\n"
        "4. 【行动跟进看板】：生成带有具体责任人、明确可验收标准和交付时限的冲刺行动表。",
    ),
    (
        "skl_def_healthcheck",
        "系统健康巡检与探活",
        "检查服务连接、网络延迟、资源水位与进程状态，输出自愈救火指南",
        "你是资深 SRE 运维工程师。针对提供的系统状态或日志「{input}」：\n"
        "1. 【健康度快速定级】：直接标明系统状态【🟢 正常】/【🟡 亚健康告警】/【🔴 故障中断】；\n"
        "2. 【核心瓶颈定位】：分析 CPU、内存、磁盘 IO、连接池占用或 HTTP 5xx 异常，定位首要诱因；\n"
        "3. 【故障自愈操作步骤】：给出 1-2-3 步按顺序可执行的救火操作指令（重启/清缓存/扩容/降级）；\n"
        "4. 【长效防复发建议】：列出对应的监控探针告警阈值配置规范。",
    ),
    (
        "skl_def_gitcommit",
        "Git提交规范提炼",
        "根据代码改动或工作描述，提炼符合 Conventional Commits 标准的提交信息",
        "你是一位严格遵循语义化提交的工程总监。针对输入的改动说明「{input}」：\n"
        "严格按照 Conventional Commits 规范生成提交信息：\n"
        "格式：<type>(<scope>): <short description>\n\n"
        "[可选详细正文 body]\n"
        "[可选影响声明 breaking changes / closes issue]\n"
        "其中 type 必须是 feat, fix, docs, style, refactor, perf, test, chore, ci 之一。\n"
        "要求简明扼要、人话描述、凸显改动本质。",
    ),
    (
        "skl_def_sqlopt",
        "SQL慢查询与索引优化",
        "诊断 SQL 性能瓶颈，消除全表扫描与隐式转换，给出最优联合索引",
        "你是数据库 DBA 专家。对输入的 SQL 语句及场景「{input}」进行性能诊断：\n"
        "1. 【慢查隐患诊断】：检查是否存在 SELECT *、隐式类型转换、函数包裹索引列、深度分页等失误；\n"
        "2. 【索引优化建议】：给出最优联合索引定义（ALTER TABLE ... ADD INDEX ...），并解释最左匹配原理；\n"
        "3. 【重构改写后的 SQL】：给出改写后的高性能 SQL，并说明优化前后预估的执行效率提升。",
    ),
    (
        "skl_def_archreview",
        "架构评审与边界设计",
        "审查技术方案的高可用、无状态伸缩、容灾降级与向后兼容性",
        "你是系统架构师。针对技术架构提案「{input}」进行系统级可用性与伸缩性审查：\n"
        "1. 【单点隐患与耦合分析】：指出系统中是否存在单点故障（SPOF）、硬编码依赖与同步阻塞卡点；\n"
        "2. 【容灾降级与熔断预案】：当外部依赖不可用或突发洪峰时，系统应当如何降级保证核心业务可用；\n"
        "3. 【数据一致性方案】：分析分布式场景下的最终一致性策略、重试幂等与分布式锁边界；\n"
        "4. 【架构裁决与改进清单】：给出必须修改的架构红线要求。",
    ),
    (
        "skl_def_translate",
        "中英双向专业术语翻译",
        "忠实准确、保留行业术语与技术上下文，输出自然流畅的专业翻译",
        "你是高阶技术文献中英互译专家。针对输入文本「{input}」：\n"
        "1. 【术语提取与对齐】：列出文本中关键行业术语的权威对照解释；\n"
        "2. 【专业翻译正文】：忠实原作原意，保持行文流畅自然，杜绝生硬机翻痕迹；\n"
        "3. 【文化语境润色建议】：若有在海外技术社区或商业洽谈中的更地道表达，附带对比润色说明。",
    ),
]


def ensure(conn) -> None:
    if db.db_path() in _READY_DBS:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skills (
            id          TEXT PRIMARY KEY,
            bot_id      TEXT,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            body        TEXT NOT NULL,
            use_count   INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            updated_at  TEXT
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_owner_name"
        " ON skills(IFNULL(bot_id,''), name)"
    )
    for row in DEFAULT_GLOBAL_SKILLS:
        conn.execute(
            """
            INSERT OR IGNORE INTO skills (id, bot_id, name, description, body, use_count, created_at)
            VALUES (?, NULL, ?, ?, ?, 0, ?)
            """,
            (row[0], row[1], row[2], row[3], ev.now_iso()),
        )
    conn.commit()
    _READY_DBS.add(db.db_path())


def _row_to_dict(row) -> dict:
    return dict(row)


def create(conn, *, bot_id: str | None, name: str, description: str,
           body: str) -> dict:
    ensure(conn)
    name = (name or "").strip()[:MAX_NAME]
    body = (body or "").strip()[:MAX_BODY]
    if not name or not body:
        raise ValueError("name 和 body 都不能为空")
    skill_id = "skl_" + secrets.token_hex(8)
    conn.execute(
        "INSERT INTO skills (id, bot_id, name, description, body, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (skill_id, bot_id, name, (description or "").strip()[:MAX_DESC],
         body, ev.now_iso()),
    )
    conn.commit()
    return get(conn, skill_id)


def get(conn, skill_id: str) -> dict | None:
    ensure(conn)
    row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
    return _row_to_dict(row) if row else None


def find_by_name(conn, name: str, *, bot_id: str | None = None) -> dict | None:
    """先找这个 Bot 自己的，再找全局的。同名时优先私有（bot 可以覆盖团队默认流程）。"""
    ensure(conn)
    if bot_id:
        row = conn.execute(
            "SELECT * FROM skills WHERE name=? AND bot_id=?", (name, bot_id)).fetchone()
        if row:
            return _row_to_dict(row)
    row = conn.execute(
        "SELECT * FROM skills WHERE name=? AND (bot_id IS NULL OR bot_id='')",
        (name,)).fetchone()
    return _row_to_dict(row) if row else None


def exists(conn, *, bot_id: str | None, name: str) -> bool:
    """同一归属内名称唯一：全局与私有可以同名（私有覆盖全局是设计意图，不是冲突）。"""
    ensure(conn)
    if bot_id:
        row = conn.execute("SELECT 1 FROM skills WHERE bot_id=? AND name=?",
                           (bot_id, name)).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM skills WHERE (bot_id IS NULL OR bot_id='') AND name=?",
            (name,)).fetchone()
    return row is not None


def list_for(conn, *, bot_id: str | None = None, include_global: bool = True) -> list[dict]:
    ensure(conn)
    if bot_id and include_global:
        rows = conn.execute(
            "SELECT * FROM skills WHERE bot_id=? OR bot_id IS NULL OR bot_id=''"
            " ORDER BY COALESCE(bot_id,'') DESC, name", (bot_id,)).fetchall()
    elif bot_id:
        rows = conn.execute(
            "SELECT * FROM skills WHERE bot_id=? ORDER BY name", (bot_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM skills ORDER BY name").fetchall()
    return [_row_to_dict(r) for r in rows]


def update(conn, skill_id: str, *, name: str | None = None,
           description: str | None = None, body: str | None = None) -> dict | None:
    ensure(conn)
    if get(conn, skill_id) is None:
        return None
    fields: dict = {"updated_at": ev.now_iso()}
    if name is not None:
        fields["name"] = name.strip()[:MAX_NAME]
    if description is not None:
        fields["description"] = description.strip()[:MAX_DESC]
    if body is not None:
        fields["body"] = body.strip()[:MAX_BODY]
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE skills SET {assignments} WHERE id=?",
                 (*fields.values(), skill_id))
    conn.commit()
    return get(conn, skill_id)


def delete(conn, skill_id: str) -> bool:
    ensure(conn)
    cur = conn.execute("DELETE FROM skills WHERE id=?", (skill_id,))
    conn.commit()
    return cur.rowcount > 0


def render(skill: dict, user_input: str | None = None) -> str:
    """把技能正文渲染成模型可执行的指令。{input} 是唯一占位符（故意只留一个，少就是多）。"""
    body = skill["body"]
    if "{input}" in body:
        body = body.replace("{input}", (user_input or "").strip())
    elif user_input:
        body = f"{body}\n\n本次输入：{user_input.strip()}"
    return body


def bump_use(skill_id: str) -> None:
    try:
        with db.get_conn() as conn:
            conn.execute("UPDATE skills SET use_count=use_count+1 WHERE id=?",
                         (skill_id,))
            conn.commit()
    except Exception:                                 # noqa: BLE001 - 计数失败不值得报错
        pass
