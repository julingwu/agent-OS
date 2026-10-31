import React, { useState } from "react";
import { api } from "./api.js";

const PRESET_TEMPLATES = [
  {
    icon: "📊",
    label: "数据分析与报表",
    targetType: "dm",
    text: `【任务目标】针对业务数据进行多维度统计与分析，产出可视化报表与洞察总结。
【输入数据】指定数据源或业务指标（如近30天订单数据、用户转化明细）。
【验收要求】
1. 编写分析脚本处理原始数据并计算核心指标；
2. 绘制清晰的趋势图表或对比图；
3. 输出 Markdown 格式的业务洞察总结与改进建议。`,
  },
  {
    icon: "🛠️",
    label: "自动化开发与单测",
    targetType: "dm",
    text: `【任务目标】编写自动化脚本/工具模块并附带完备的单元测试验证。
【功能需求】自动化执行批量数据清洗/接口同步/文件处理。
【交付要求】
1. 完整可执行的 Python 脚本；
2. 包含单元测试用例并验证全部通过；
3. 附带简洁明了的 README 使用文档与运行示例。`,
  },
  {
    icon: "🔍",
    label: "竞品调研与报告",
    targetType: "ceo",
    text: `【任务目标】针对指定行业赛道与对标竞品展开系统性调研。
【研究维度】产品核心功能、商业模式、技术架构优势、用户口碑与市场策略。
【交付要求】
1. 结构化竞品对比矩阵表；
2. 优劣势与差异化壁垒剖析；
3. 落地建议与机会清单（Markdown 格式交付）。`,
  },
  {
    icon: "⚡",
    label: "紧急排障与修复",
    targetType: "ceo",
    text: `[URGENT 紧急排障] 系统服务或任务出现异常，需快速定位根因并产出修复。
【异常现象】服务响应异常或任务卡死，报错日志见上下文。
【要求】
1. 第一时间复现问题并排查根本原因（Root Cause）；
2. 产出最小化、无副作用的修复补丁；
3. 补充防回归测试用例并验证系统恢复正常。`,
  },
  {
    icon: "📝",
    label: "架构方案与 PRD",
    targetType: "ceo",
    text: `【任务目标】撰写系统功能技术方案与产品需求说明书（PRD）。
【覆盖范围】背景目标、用户场景、业务流程时序图、数据模型与 API 接口定义。
【交付要求】包含边界异常处理、权限控制规范与详细的评审检查清单。`,
  },
];

export default function NewTaskModal({ chats, onClose, onCreated, Icon }) {
  const options = chats.filter((c) => c.kind === "dm" || c.kind === "ceo" || c.kind === "group");
  const preferred = options.find((c) => c.kind === "dm" && /Python|开发|工程/.test(c.title)) || options.find((c) => c.kind === "dm") || options[0];
  const [target, setTarget] = useState(preferred?.id || "");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selected = options.find((c) => c.id === target);

  function applyTemplate(tpl) {
    if (text.trim() && !window.confirm("应用模板将替换当前已输入的需求内容，确定替换吗？")) {
      return;
    }
    setText(tpl.text);
    if (tpl.targetType === "ceo") {
      const ceoOpt = options.find((c) => c.kind === "ceo");
      if (ceoOpt) setTarget(ceoOpt.id);
    }
  }

  async function submit(e) {
    e.preventDefault();
    if (!text.trim() || !selected || busy) return;
    setBusy(true); setError("");
    try {
      await api.send(target, text.trim());
      onCreated(selected);
    } catch (err) {
      setError(err.message || "提交失败，请检查连接后重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mask" onClick={() => { if (!busy) onClose(); }}>
      <form className="modal new-task-modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="new-task-heading">
          <span className="metric-icon purple"><Icon name="spark" /></span>
          <button type="button" className="icon-btn" aria-label="关闭新建任务" onClick={onClose} disabled={busy}>
            <Icon name="close" />
          </button>
        </div>
        <h3>创建新任务</h3>
        <p className="new-task-intro">描述你希望完成的工作，AI 团队会帮你推进到交付。</p>

        <div className="field">
          <label htmlFor="new-task-target">交给谁来完成</label>
          <select id="new-task-target" value={target} onChange={(e) => setTarget(e.target.value)} disabled={busy}>
            {options.map((c) => (
              <option key={c.id} value={c.id}>
                {c.employee_name || c.title}
                {c.kind === "ceo" ? " · 自动组建项目" : c.kind === "group" ? " · 团队协作" : " · 直接执行任务"}
              </option>
            ))}
          </select>
        </div>

        <div className="task-mode-note">
          <Icon name={selected?.kind === "ceo" ? "org" : "role"} />
          <span>
            {selected?.kind === "ceo"
              ? "自动调度会创建多阶段项目，组织团队完成需求。"
              : selected?.kind === "group"
              ? "任务将发送到协作群，由团队分工执行。"
              : "直接交给所选 AI 员工执行，进度会显示在会话中。"}
          </span>
        </div>

        <div className="task-templates-section" style={{ marginTop: 14, marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}>一键套用场景模板：</span>
            {text && (
              <button type="button" className="btn ghost" style={{ height: 22, padding: "1px 8px", fontSize: 11 }}
                      onClick={() => setText("")} disabled={busy}>
                清空内容
              </button>
            )}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {PRESET_TEMPLATES.map((tpl) => (
              <button key={tpl.label} type="button" className="btn ghost"
                      style={{ fontSize: 12, padding: "4px 10px", borderRadius: 14, border: "1px solid var(--border)", background: "var(--panel)" }}
                      onClick={() => applyTemplate(tpl)} disabled={busy}>
                <span style={{ marginRight: 4 }}>{tpl.icon}</span>{tpl.label}
              </button>
            ))}
          </div>
        </div>

        <div className="field">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <label htmlFor="new-task-description" style={{ margin: 0 }}>任务需求</label>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>{text.length} / 12,000 字</span>
          </div>
          <textarea
            id="new-task-description"
            rows={7}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={"想完成什么？需要哪些交付物？\n例如：编写一个销售汇总脚本，生成测试数据、运行验证，并附上使用说明。"}
            autoFocus
            disabled={busy}
            maxLength={12000}
          />
        </div>

        <div className="task-input-note">清晰的目标、输入和验收要求，能帮助 AI 更准确地完成任务。</div>
        {error && <div className="err" role="alert">{error}</div>}

        <div className="actions">
          <button type="button" className="btn ghost" disabled={busy} onClick={onClose}>取消</button>
          <button className="btn primary" disabled={busy || !text.trim() || !target}>
            {busy ? <><span className="spin" /> 正在提交…</> : <><Icon name="arrow" /> 创建并执行</>}
          </button>
        </div>
      </form>
    </div>
  );
}
