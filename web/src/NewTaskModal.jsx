import React, { useState } from "react";
import { api } from "./api.js";

export default function NewTaskModal({ chats, onClose, onCreated, Icon }) {
  const options = chats.filter((c) => c.kind === "dm" || c.kind === "ceo" || c.kind === "group");
  const preferred = options.find((c) => c.kind === "dm" && /Python|开发|工程/.test(c.title)) || options.find((c) => c.kind === "dm") || options[0];
  const [target, setTarget] = useState(preferred?.id || "");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selected = options.find((c) => c.id === target);
  async function submit(e) {
    e.preventDefault();
    if (!text.trim() || !selected || busy) return;
    setBusy(true); setError("");
    try { await api.send(target, text.trim()); onCreated(selected); }
    catch (err) { setError(err.message || "提交失败，请检查连接后重试。"); }
    finally { setBusy(false); }
  }
  return <div className="mask" onClick={() => { if (!busy) onClose(); }}><form className="modal new-task-modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
    <div className="new-task-heading"><span className="metric-icon purple"><Icon name="spark" /></span><button type="button" className="icon-btn" aria-label="关闭新建任务" onClick={onClose} disabled={busy}><Icon name="close" /></button></div>
    <h3>创建新任务</h3><p className="new-task-intro">描述你希望完成的工作，AI 团队会帮你推进到交付。</p>
    <div className="field"><label htmlFor="new-task-target">交给谁来完成</label><select id="new-task-target" value={target} onChange={(e) => setTarget(e.target.value)} disabled={busy}>{options.map((c) => <option key={c.id} value={c.id}>{c.employee_name || c.title}{c.kind === "ceo" ? " · 自动组建项目" : c.kind === "group" ? " · 团队协作" : " · 直接执行任务"}</option>)}</select></div>
    <div className="task-mode-note"><Icon name={selected?.kind === "ceo" ? "org" : "role"} /><span>{selected?.kind === "ceo" ? "自动调度会创建多阶段项目，组织团队完成需求。" : selected?.kind === "group" ? "任务将发送到协作群，由团队分工执行。" : "直接交给所选 AI 员工执行，进度会显示在会话中。"}</span></div>
    <div className="field"><label htmlFor="new-task-description">任务需求</label><textarea id="new-task-description" rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder={"想完成什么？需要哪些交付物？\n例如：编写一个销售汇总脚本，生成测试数据、运行验证，并附上使用说明。"} autoFocus disabled={busy} maxLength={12000} /></div>
    <div className="task-input-note">清晰的目标、输入和验收要求，能帮助 AI 更准确地完成任务。</div>
    {error && <div className="err" role="alert">{error}</div>}
    <div className="actions"><button type="button" className="btn ghost" disabled={busy} onClick={onClose}>取消</button><button className="btn primary" disabled={busy || !text.trim() || !target}>{busy ? <><span className="spin" /> 正在提交…</> : <><Icon name="arrow" /> 创建并执行</>}</button></div>
  </form></div>;
}
