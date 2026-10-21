import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const empty = { title: "", content: "", tags: "", mandatory: false, redline_terms: "" };
const states = { pending: "待审核", approved: "已生效", rejected: "已驳回" };

export default function GovernanceView({ me }) {
  const canReview = ["ceo", "system"].includes(me?.role);
  const [data, setData] = useState({ experiences: [], stats: {} });
  const [filter, setFilter] = useState("");
  const [form, setForm] = useState(empty);
  const [editing, setEditing] = useState(null);
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [notes, setNotes] = useState({});
  const [confirming, setConfirming] = useState("");   // 删除二次确认的经验 id
  const request = useRef(0);
  const load = useCallback(async () => {
    const current = ++request.current;
    try {
      const result = await api.experiences(undefined, filter);
      if (current === request.current) setData(result);
    } catch (error) { if (current === request.current) throw error; }
  }, [filter]);
  useEffect(() => { load().catch(e => setError(e.message)); return () => { ++request.current; }; }, [load]);
  async function perform(fn, message) {
    setBusy(true); setError(""); setNotice("");
    try { await fn(); await load(); setNotice(message); return true; }
    catch (e) { setError(e.message); return false; }
    finally { setBusy(false); }
  }
  async function save(e) {
    e.preventDefault();
    const ok = await perform(() => editing ? api.editExperience(editing, form) : api.createExperience({ ...form, kind: "decision" }),
      "已保存为待审核。审核通过后才会用于新任务。");
    if (ok) { setForm(empty); setEditing(null); }
  }
  const counts = data.stats?.by_state || {};
  return <div className="main"><div className="topbar"><div>
    <div className="who">组织经验</div><div className="sub">审核有用经验，明确交付规则，让后续任务少走弯路</div>
  </div><span className="spacer" /><button className="btn ghost" disabled={busy} onClick={() => perform(async () => {}, "已刷新。")}>刷新</button></div>
    <div className="stream"><div className="stream-inner integration-page">
      {error && <div className="integration-alert" role="alert">{error}</div>}
      {notice && <div className="integration-notice" role="status">{notice}</div>}
      <div className="fin-hero memory-hero">{Object.entries(states).map(([state, text]) => <div className="fin-kpi" key={state}><span>{text}</span><b>{counts[state] || 0}</b></div>)}</div>
      {canReview && <section className="fin-card"><h3>{editing ? "编辑经验" : "添加经验与规则"}</h3>
        <p className="hint">新内容和修改都需要单独审核。“强制规则”通过审核后适用于当前工作空间的任务。</p>
        <form onSubmit={save}>
          <label className="field">经验标题<input required maxLength={160} value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} /></label>
          <label className="field">经验内容<textarea required maxLength={4000} rows={3} value={form.content} onChange={e => setForm({ ...form, content: e.target.value })} placeholder="写清楚适用条件、具体要求和判断标准" /></label>
          <label className="field">检索标签<input value={form.tags} onChange={e => setForm({ ...form, tags: e.target.value })} placeholder="用逗号分隔，例如：文案,质量" /></label>
          <label className="integration-checkbox"><input type="checkbox" checked={Boolean(form.mandatory)} onChange={e => setForm({ ...form, mandatory: e.target.checked })} />作为强制规则</label>
          {form.mandatory && <label className="field">禁止出现的用语<input value={form.redline_terms} onChange={e => setForm({ ...form, redline_terms: e.target.value })} placeholder="用逗号分隔；验收时检查交付内容" /></label>}
          <div className="actions"><button className="btn primary" disabled={busy}>提交待审核</button>{editing && <button type="button" className="btn ghost" onClick={() => { setEditing(null); setForm(empty); }}>取消编辑</button>}</div>
        </form>
      </section>}
      <section className="fin-card"><div className="integration-heading"><h3>经验库</h3>
        <label>审核状态 <select aria-label="筛选审核状态" value={filter} onChange={e => setFilter(e.target.value)}><option value="">全部</option>{Object.entries(states).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
        {!data.experiences.length && <p className="hint">暂无符合条件的经验。任务驳回和项目复盘也会生成待审核记录。</p>}
        {data.experiences.map(item => <article className="exp-row" key={item.id}>
          <div className="exp-head"><span className={`pill ${item.state === "approved" ? "ok" : item.state === "rejected" ? "bad" : "warn"}`}>{states[item.state] || item.state}</span><strong>{item.title}</strong>{Boolean(item.mandatory) && <span className="chip">强制规则</span>}</div>
          <p className="exp-content">{item.content}</p>
          <div className="hint">版本 {item.version || 1} · {item.source_type === "manual" ? "手动整理" : "任务沉淀"} · 使用 {item.uses || 0} 次</div>
          {item.review_note && <p className="hint">审核说明：{item.review_note}</p>}
          {canReview && <div className="integration-review">
            {item.state === "pending" && <label className="field">审核说明<input aria-label={`审核说明：${item.title}`} value={notes[item.id] || ""} onChange={e => setNotes({ ...notes, [item.id]: e.target.value })} placeholder="可选，记录采纳或驳回原因" /></label>}
            <div className="actions">{item.state === "pending" && <>
              <button className="btn primary" disabled={busy} onClick={() => perform(() => api.reviewExperience(item.id, "approve", notes[item.id] || "", item.version), "审核通过，经验已生效。")}>审核通过</button>
              <button className="btn danger" disabled={busy} onClick={() => perform(() => api.reviewExperience(item.id, "reject", notes[item.id] || "", item.version), "经验已驳回，不会用于新任务。")}>驳回</button></>}
              <button className="btn ghost" disabled={busy} onClick={() => { setEditing(item.id); setForm({ title: item.title, content: item.content, tags: item.tags || "", mandatory: Boolean(item.mandatory), redline_terms: Array.isArray(item.redline_terms) ? item.redline_terms.join(",") : item.redline_terms || "" }); }}>编辑经验</button>
              <button className={confirming === item.id ? "btn danger" : "btn ghost"} disabled={busy}
                      onClick={async () => {
                        if (confirming !== item.id) { setConfirming(item.id); return; }
                        const ok = await perform(() => api.deleteExperience(item.id), "经验已彻底删除，强制规则不再生效。");
                        setConfirming("");
                        if (ok && editing === item.id) { setEditing(null); setForm(empty); }
                      }}>
                {confirming === item.id ? "确认删除" : "删除"}
              </button>
              {confirming === item.id && <button className="btn ghost" disabled={busy} onClick={() => setConfirming("")}>取消</button>}
            </div>
          </div>}
        </article>)}
      </section>
      <section className="fin-card"><h3>任务引用预览</h3><p className="hint">查看新需求会引用哪些已审核经验。未审核、已驳回和其他工作空间的内容不会被引用。</p>
        <form onSubmit={async e => { e.preventDefault(); setBusy(true); setError(""); try { setPreview(await api.previewExperience(query)); } catch (ex) { setError(ex.message); } finally { setBusy(false); } }}>
          <label className="field">新任务需求<textarea required rows={2} value={query} onChange={e => setQuery(e.target.value)} /></label>
          <button className="btn" disabled={busy}>预览引用</button>
        </form>
        {preview && <div className="exp-preview"><p>将引用 {preview.count} 条经验</p>{preview.text && <pre>{preview.text}</pre>}</div>}
      </section>
    </div></div>
  </div>;
}

export function ProjectRecovery({ data, onChanged }) {
  const [requirement, setRequirement] = useState(data.project.requirement || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const blockers = data.gates.filter(g => !g.superseded && ["failed", "vetoed"].includes(g.state));
  if (data.project.state !== "blocked") return data.project.state === "retrying" ? <p role="status">正在重新检查项目，请稍候…</p> : null;
  return <div className="project-recovery">
    <h4>修改方案后重新检查</h4>
    <p>{data.project.veto_note || data.project.last_error || "项目仍有未通过的检查，请补充修改后的完整需求。"}</p>
    {blockers.map(g => <p className="hint" key={g.id}>{g.name}：{g.note || "未通过"}</p>)}
    {data.allowed_actions?.includes("retry") && <form onSubmit={async e => {
      e.preventDefault(); setBusy(true); setError("");
      try { await api.retryProject(data.project.id, requirement); onChanged(await api.project(data.project.id)); }
      catch (ex) { setError(ex.message); } finally { setBusy(false); }
    }}><label className="field">修改后的完整需求<textarea rows={4} required value={requirement} onChange={e => setRequirement(e.target.value)} /></label>
      <button className="btn primary" disabled={busy || requirement.trim() === data.project.requirement?.trim()}>提交修改并重新检查</button>
    </form>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
