import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const empty = { title: "", content: "", tags: "", mandatory: false, redline_terms: "" };
const states = { pending: "待审核", approved: "已生效", rejected: "已驳回" };
// v5.2 TASK-3.1：按 kind / 标签 / 来源分组展示，批量通过 / 批量驳回。
const kinds = { rejection: "驳回经验", consensus: "共识结论", decision: "决策否决" };
const sources = {
  manual: "手动整理", task: "任务沉淀", task_self_healed: "自愈成功",
  workshop_veto: "研讨会否决", project_retro: "项目复盘", decision: "决策否决",
  workshop: "研讨会共识", unknown: "未标记",
};
const groupModes = { kind: "按类型", tag: "按标签", source: "按来源" };
const groupKeyOf = (item, mode) => {
  if (mode === "tag") return (item.tags || "").split(",").map(t => t.trim()).filter(Boolean)[0] || "未打标签";
  if (mode === "source") return item.source_type || "unknown";
  return item.kind || "unknown";
};
const groupLabelOf = (key, mode) => mode === "kind" ? (kinds[key] || key)
  : mode === "source" ? (sources[key] || key) : `#${key}`;

export default function GovernanceView({ me }) {
  const canReview = ["ceo", "system"].includes(me?.role);
  const [data, setData] = useState({ experiences: [], stats: {} });
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [form, setForm] = useState(empty);
  const [editing, setEditing] = useState(null);
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [notes, setNotes] = useState({});
  const [confirming, setConfirming] = useState("");   // 删除二次确认的经验 id
  const [kind, setKind] = useState("");
  const [groupMode, setGroupMode] = useState("kind");
  const [selected, setSelected] = useState({});
  const request = useRef(0);
  const load = useCallback(async () => {
    const current = ++request.current;
    try {
      const result = await api.experiences(kind || undefined, filter);
      if (current === request.current) setData(result);
    } catch (error) { if (current === request.current) throw error; }
  }, [filter, kind]);
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
  // 搜索 + 选择：只有 pending 才能被勾选，避免误批量通过已生效规则。
  const visible = data.experiences.filter(item => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (item.title || "").toLowerCase().includes(q) ||
           (item.content || "").toLowerCase().includes(q) ||
           (item.tags || "").toLowerCase().includes(q) ||
           (item.redline_terms || "").toLowerCase().includes(q);
  });
  const pendingVisible = visible.filter(item => item.state === "pending");
  const selectedIds = Object.keys(selected).filter(id => selected[id]);
  const allSelected = pendingVisible.length > 0 && pendingVisible.every(item => selected[item.id]);
  const toggleSelect = id => setSelected(prev => ({ ...prev, [id]: !prev[id] }));
  const toggleSelectAll = () => setSelected(allSelected ? {}
    : Object.fromEntries(pendingVisible.map(item => [item.id, true])));
  async function batchReview(action) {
    const ids = selectedIds;
    if (!ids.length) { setError("请先勾选要处理的待审核经验。"); setNotice(""); return; }
    const verb = action === "approve" ? "通过" : "驳回";
    if (!window.confirm(`确定批量${verb} ${ids.length} 条经验吗？`)) return;
    const ok = await perform(() => api.batchReviewExperiences(ids, action, `批量${verb}`, false),
      `已批量${verb} ${ids.length} 条经验。`);
    if (ok) setSelected({});
  }
  const grouped = [];
  const groupIndex = {};
  for (const item of visible) {
    const key = groupKeyOf(item, groupMode);
    if (!groupIndex[key]) { groupIndex[key] = { key, items: [] }; grouped.push(groupIndex[key]); }
    groupIndex[key].items.push(item);
  }
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
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <label>分组 <select aria-label="分组方式" value={groupMode} onChange={e => setGroupMode(e.target.value)}>{Object.entries(groupModes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <label>类型 <select aria-label="筛选经验类型" value={kind} onChange={e => setKind(e.target.value)}><option value="">全部</option>{Object.entries(kinds).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <label>审核状态 <select aria-label="筛选审核状态" value={filter} onChange={e => setFilter(e.target.value)}><option value="">全部</option>{Object.entries(states).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
          <input
            type="text"
            placeholder="🔍 搜索经验标题、内容或标签..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ padding: "4px 10px", fontSize: 12, borderRadius: 6, width: 220, border: "1px solid var(--line, rgba(255,255,255,0.15))", background: "rgba(0,0,0,0.15)", color: "inherit" }}
          />
          {canReview && <>
            <button className="btn ghost" disabled={busy || !pendingVisible.length} onClick={toggleSelectAll}>
              {allSelected ? "取消全选本页" : `全选本页（${pendingVisible.length}）`}
            </button>
            <button className="btn primary" disabled={busy || !selectedIds.length} onClick={() => batchReview("approve")}>
              ✅ 批量通过{selectedIds.length ? `（${selectedIds.length}）` : ""}
            </button>
            <button className="btn danger" disabled={busy || !selectedIds.length} onClick={() => batchReview("reject")}>
              🚫 批量驳回{selectedIds.length ? `（${selectedIds.length}）` : ""}
            </button>
            <button
              className="btn ghost"
              disabled={busy}
              onClick={async () => {
                if (!window.confirm("确定一键归档所有未审核经验与红线噪音吗？")) return;
                await perform(() => api.batchReviewExperiences([], "reject", "一键批量归档未审核噪音", true), "已归档所有未审核红线噪音。");
              }}
              style={{ borderColor: "var(--warn, #f59e0b)", color: "var(--warn, #f59e0b)" }}
            >
              📦 归档所有未审核红线噪音
            </button>
          </>}
        </div></div>
        {selectedIds.length > 0 && <p className="hint">已选 {selectedIds.length} 条待审核经验，可批量通过或批量驳回。</p>}
        {!visible.length && <p className="hint">暂无符合条件的经验。任务自愈成功、研讨会一票否决与项目复盘会生成待审核记录。</p>}
        {grouped.map(group => {
          const groupPending = group.items.filter(item => item.state === "pending");
          const groupAll = groupPending.length > 0 && groupPending.every(item => selected[item.id]);
          return <div className="exp-group" key={group.key} style={{ marginBottom: 18 }}>
            <div className="integration-heading" style={{ marginBottom: 8 }}>
              <h4 style={{ margin: 0 }}>{groupLabelOf(group.key, groupMode)} <span className="chip">{group.items.length}</span>
                {groupPending.length > 0 && <span className="hint" style={{ marginLeft: 8 }}>待审核 {groupPending.length}</span>}</h4>
              {canReview && groupPending.length > 0 && <button className="btn ghost" disabled={busy}
                onClick={() => setSelected(prev => {
                  const next = { ...prev };
                  groupPending.forEach(item => { next[item.id] = !groupAll; });
                  return next;
                })}>{groupAll ? "取消本组" : `全选本组（${groupPending.length}）`}</button>}
            </div>
            {group.items.map(item => <article className="exp-row" key={item.id}>
              <div className="exp-head">
                {canReview && item.state === "pending" && <input type="checkbox" aria-label={`选择经验：${item.title}`} checked={Boolean(selected[item.id])} onChange={() => toggleSelect(item.id)} style={{ marginRight: 8 }} />}
                <span className={`pill ${item.state === "approved" ? "ok" : item.state === "rejected" ? "bad" : "warn"}`}>{states[item.state] || item.state}</span><strong>{item.title}</strong>{Boolean(item.mandatory) && <span className="chip">强制规则</span>}
              </div>
              <p className="exp-content">{item.content}</p>
              <div className="hint">版本 {item.version || 1} · {sources[item.source_type] || item.source_type || "任务沉淀"} · 使用 {item.uses || 0} 次</div>
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
          </div>;
        })}
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
