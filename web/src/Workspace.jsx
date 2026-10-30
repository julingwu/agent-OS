import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

export const TASK_STATES = {
  todo: "待开始", assigned: "待执行", in_progress: "执行中", pending_review: "待验收",
  completed: "已完成", rejected: "已驳回", cancelled: "已中止", failed: "执行失败", blocked: "待处理", awaiting_approval: "待审批",
};

export function StatusBadge({ state }) {
  return <span className={`status-badge status-${state === "awaiting_approval" ? "pending_review" : state || "todo"}`}><i />{TASK_STATES[state] || state || "待开始"}</span>;
}

function timeLabel(value) {
  if (!value) return "—";
  const d = new Date(value.includes("T") ? value : value.replace(" ", "T") + "Z");
  return Number.isNaN(d.getTime()) ? value.slice(5, 16) : d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

export default function Workspace({ chats, pendingCount, onChat, onSelectTask, onNavigate, onNewTask, Icon, tasksOnly = false }) {
  const [data, setData] = useState({ tasks: [], counts: {} });
  const [employees, setEmployees] = useState([]);
  const [fileCount, setFileCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const request = useRef(0);
  const pageSize = tasksOnly ? 20 : 8;
  const queryKey = JSON.stringify([filter, search, page, pageSize]);
  const latestQuery = useRef(queryKey);
  latestQuery.current = queryKey;
  useEffect(() => {
    if (query.trim() === search) return;
    const timer = setTimeout(() => { setSearch(query.trim()); setPage(0); }, 250);
    return () => clearTimeout(timer);
  }, [query, search]);

  const load = useCallback(async () => {
    if (queryKey !== latestQuery.current) return;
    const id = ++request.current;
    const isCurrent = () => id === request.current && queryKey === latestQuery.current;
    setError(null); setFetching(true);
    try {
      const [tasks, team, artifacts] = await Promise.all([api.tasks(pageSize, { state: filter === "all" ? "" : filter, q: search, offset: page * pageSize }), api.employees(), api.files()]);
      if (!isCurrent()) return;
      setData({ ...tasks, queryKey }); setEmployees(team.employees || []); setFileCount(artifacts.total ?? artifacts.files?.length ?? 0);
    } catch (e) { if (isCurrent()) setError({ queryKey, message: e.message || "暂时无法获取工作台数据，请重试。" }); }
    finally { if (isCurrent()) { setLoading(false); setFetching(false); } }
  }, [filter, search, page, pageSize, queryKey]);
  useEffect(() => { load(); const timer = setInterval(load, 15000); window.addEventListener("oa-tasks-changed", load); return () => { ++request.current; clearInterval(timer); window.removeEventListener("oa-tasks-changed", load); }; }, [load]);

  const counts = data.counts || {};
  const activeCount = (counts.in_progress || 0) + (counts.assigned || 0) + (counts.todo || 0);
  const reviewCount = counts.pending_review || 0;
  const total = Object.values(counts).reduce((sum, n) => sum + Number(n || 0), 0);
  const team = employees.filter((e) => e.status !== "fired");
  // Counts are global and stay visible during navigation. Rows and result totals
  // belong only to the query/page that actually produced them.
  const debouncing = query.trim() !== search;
  const dataCurrent = data.queryKey === queryKey && !debouncing;
  const listError = error?.queryKey === queryKey && !debouncing;
  const shown = dataCurrent && !listError ? (data.tasks || []) : [];
  const navigationBusy = fetching || debouncing || (!listError && !dataCurrent);
  const pickTask = (task) => {
    if (onSelectTask) { onSelectTask(task); return; }
    const chat = chats.find((c) => c.id === task.chat_id);
    if (chat) onChat(chat);
  };

  return <div className="workspace-page">
    <div className="page-heading">
      <div><div className="eyebrow">YOUR AI WORKSPACE</div><h1>{tasksOnly ? "任务中心" : "工作台"}</h1><p>{tasksOnly ? "每一项任务，从开始到交付，进展尽在掌握。" : "欢迎回来，开始今天的高效协作。"}</p></div>

    </div>

    {!tasksOnly && <section className="welcome-card">
      <div className="welcome-copy"><span className="welcome-label"><span className="live-dot" /> AI 团队，准备就绪</span><h2>专注你的目标，<br />把执行交给 AI。</h2><p>描述需求、分配任务、审查交付。<br className="hero-break" />在一个工作空间，让想法稳步落地。</p><button className="btn welcome-action" onClick={() => onNavigate("chats")}>呼叫 CEO 调度台 <Icon name="arrow" /></button></div>
      <div className="workflow-preview" aria-label="工作流程：描述需求、AI 执行、验收交付">
        <div className="workflow-step"><span className="workflow-icon"><Icon name="chat" /></span><span><b>描述需求</b><small>明确目标与交付物</small></span><span className="step-number">01</span></div>
        <div className="workflow-connector" />
        <div className="workflow-step highlighted"><span className="workflow-icon"><Icon name="spark" /></span><span><b>AI 团队执行</b><small>进度与工具调用实时可见</small></span><span className="step-number">02</span></div>
        <div className="workflow-connector" />
        <div className="workflow-step"><span className="workflow-icon"><Icon name="check" /></span><span><b>验收与交付</b><small>确认结果，沉淀工作产物</small></span><span className="step-number">03</span></div>
      </div>
    </section>}

    <div className="metric-grid" aria-busy={loading}>
      {[
        ["进行中任务", activeCount, "持续推进中的工作", "play", "purple"],
        ["待验收任务", reviewCount, "等待你的结果确认", "check", "amber"],
        ["已完成任务", counts.completed || 0, `累计 ${total} 项任务`, "complete", "green"],
        ["工作产物", fileCount, "当前文件库中的交付物", "files", "blue"],
      ].map(([label, value, note, icon, color]) => <div className="metric-card" key={label}><div className="metric-top"><span>{label}</span><span className={`metric-icon ${color}`}><Icon name={icon} /></span></div><div className="metric-value">{loading ? <span className="skeleton-number" /> : value}<span>{label === "工作产物" ? "份" : "项"}</span></div><div className="metric-note">{note}</div></div>)}
    </div>

    {listError && <div className="workspace-error" role="alert"><Icon name="alert" /><span>{error.message}</span><button className="btn" onClick={load} disabled={fetching}>重试</button></div>}

    <div className={tasksOnly ? "workspace-columns tasks-only" : "workspace-columns"}>
      <section className="surface task-surface">
        <div className="surface-heading"><div><h2>{tasksOnly ? "全部任务" : "最近任务"}<span className="count-label">{total}</span></h2><p>追踪执行进度，及时确认工作成果</p></div><button className="icon-btn" title="刷新任务" aria-label="刷新任务" onClick={load}><Icon name="refresh" /></button></div>
        <div className="task-toolbar"><div className="filter-tabs" role="group" aria-label="任务状态筛选">{[["all", "全部"], ["active", "进行中"], ["pending_review", "待验收"], ["completed", "已完成"]].map(([key, label]) => <button key={key} className={filter === key ? "selected" : ""} aria-pressed={filter === key} onClick={() => { setFilter(key); setPage(0); }}>{label}</button>)}</div><label className="task-search"><Icon name="search" /><input aria-label="搜索任务" placeholder="搜索任务…" value={query} onChange={(e) => setQuery(e.target.value)} /></label></div>
        <div className="task-table" role="table" aria-label="任务列表" aria-busy={navigationBusy}><div className="task-table-head" role="row"><span role="columnheader">任务名称</span><span role="columnheader">状态</span><span className="task-updated" role="columnheader">最近更新</span><span role="columnheader" className="task-link-col">操作</span></div>
          {listError ? <div className="table-empty"><span className="empty-symbol"><Icon name="alert" /></span><b>任务列表加载失败</b><span>请使用上方的“重试”重新获取当前页面。</span></div> : !dataCurrent ? <div className="table-empty" role="status"><span className="spin" /> 正在获取任务…</div> : shown.length === 0 ? <div className="table-empty"><span className="empty-symbol"><Icon name={query ? "search" : "tasks"} /></span><b>{query ? "没有找到匹配的任务" : filter === "all" ? "从第一项任务开始" : "当前分类暂无任务"}</b><span>{query ? "试试其他名称或任务编号" : "给 AI 团队一个清晰的目标，进展会显示在这里。可前往【赛博指挥舱 · 总经理办公室】或【协作会话 · CEO 调度台】发起新项目。"}</span>{filter === "all" && !query && <div style={{ display: "flex", gap: 10, marginTop: 12 }}><button className="btn" onClick={() => onNavigate("cyberdeck")}><Icon name="spark" /> 前往总经理办公室</button><button className="btn primary" onClick={() => onNavigate("chats")}><Icon name="chat" /> 呼叫 CEO 调度台</button></div>}</div> : shown.map((task) => <div className="task-table-row" role="row" key={task.id}><div className="task-title-cell" role="cell"><span className="task-file-icon"><Icon name="tasks" /></span><div><b title={task.title}>{task.title || "未命名任务"}</b><small>{task.owner_name || "AI 团队"}<span>·</span>{task.id.slice(-8)}</small></div></div><div role="cell"><StatusBadge state={task.awaiting_approval ? "awaiting_approval" : task.state} /></div><span className="task-updated" role="cell">{timeLabel(task.updated_at || task.created_at)}</span><div className="task-link-col" role="cell" style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 6 }}><button className="text-button" onClick={() => pickTask(task)}>查看 <Icon name="arrow" /></button>{["failed", "rejected", "cancelled", "completed"].includes(task.state) && <button className="icon-btn" title="删除任务" aria-label={`删除任务：${task.title}`} style={{ width: 22, height: 22, padding: 0, opacity: 0.6 }} onClick={async (e) => { e.stopPropagation(); if (!confirm(`确定删除任务「${task.title || task.id}」？关联数据与产物将一并清除。`)) return; try { await api.deleteTask(task.id); load(); window.dispatchEvent(new Event("oa-tasks-changed")); } catch (err) { alert(err.message || "删除失败"); } }}><Icon name="close" /></button>}{task.awaiting_approval && task.pending_approval && <button className="text-button" aria-label={`前往审批中心：${task.title}`} onClick={() => onNavigate("approvals")}>审批</button>}</div></div>)}
        </div>
        {tasksOnly && <div className="surface-footer pagination"><span>{listError ? `第 ${page + 1} 页加载失败` : dataCurrent ? `共 ${data.total || 0} 项 · 第 ${page + 1} 页` : `正在加载第 ${page + 1} 页…`}</span><button className="btn ghost" disabled={navigationBusy || page === 0} onClick={() => setPage((value) => value - 1)}>上一页</button><button className="btn ghost" disabled={navigationBusy || !dataCurrent || listError || (page + 1) * pageSize >= (data.total || 0)} onClick={() => setPage((value) => value + 1)}>下一页</button></div>}
        {!tasksOnly && <div className="surface-footer"><span>仅展示实际任务记录</span><button className="text-button" onClick={() => onNavigate("tasks")}>查看全部任务 <Icon name="arrow" /></button></div>}
      </section>

      {!tasksOnly && <div className="workspace-side">
        <section className="surface team-surface"><div className="surface-heading"><div><h2>你的 AI 团队 <span className="count-label">{team.length}</span></h2><p>各司其职，协同完成工作</p></div><Icon name="org" /></div><div className="team-list">{team.slice(0, 5).map((employee, index) => <button className="team-member" key={employee.id} onClick={() => { const chat = chats.find((c) => c.employee_id === employee.id); if (chat) onChat(chat); else onNavigate("org"); }}><span className={`team-avatar tone-${index % 4}`}>{employee.name.slice(0, 1)}</span><span className="team-name"><b>{employee.name}</b><small>{employee.title || employee.model || "AI 员工"}</small></span><Icon name="chevron" /></button>)}</div><div className="surface-footer"><button className="text-button" onClick={() => onNavigate("org")}>管理团队 <Icon name="arrow" /></button></div></section>
        <section className="review-callout"><span className="metric-icon amber"><Icon name="shield" /></span><div><h3>{pendingCount ? `${pendingCount} 项操作等待审批` : "每一步，都由你掌控"}</h3><p>{pendingCount ? "确认操作范围后，AI 可继续推进任务。" : "涉及额外权限的操作，会在执行前等待你的确认。"}</p><button className="text-button" onClick={() => onNavigate("approvals")}>前往审批中心 <Icon name="arrow" /></button></div></section>
      </div>}
    </div>
    <div className="workspace-footnote"><span className="live-dot" /> 工作空间已连接<span>·</span>数据来自当前本地工作区</div>
  </div>;
}
