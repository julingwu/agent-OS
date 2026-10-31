import React, { useState, useEffect, useCallback } from "react";
import api from "../api.js";
import { cls } from "./common.jsx";

export function ResourceSection({ onDrill }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const load = useCallback(async () => {
    try { setData(await api.resources()); } catch { setData({ resources: [] }); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function seed() {
    setBusy("seed"); setNote("");
    try { setData(await api.seedResources()); setNote("资源目录已就绪"); }
    catch (e) { setNote(e.message || "初始化失败"); }
    finally { setBusy(""); }
  }
  async function drill(r) {
    const agent = r.id === "RES-FIN" ? "AI-QUANT-01" : "AI-FIN-01";
    setBusy(r.id); setNote("");
    try {
      const out = await api.drill({ resource_id: r.id, agent_id: agent, mode: "auto" });
      setNote(`演练已启动：${out.agent_id} → ${out.resource_id}（run ${out.run_id}）。去它的会话看拦截卡，或在「审批」页处理。`);
    } catch (e) { setNote(e.message || "演练启动失败"); }
    finally { setBusy(""); }
  }

  const rows = data?.resources || [];
  return (
    <div className="res-section">
      <div className="res-head">
        <b>资源目录 · RBAC</b>
        <span className="hint mono">{data?.shared_root || ""}</span>
        <span className="spacer" />
        {data?.pending_approvals > 0 && (
          <span className="pill bad">待批 {data.pending_approvals}</span>
        )}
        <button className="btn ghost" disabled={busy === "seed"} onClick={seed}>
          {busy === "seed" ? "初始化…" : "补齐目录"}
        </button>
      </div>
      {note && <div className="note-bar">{note}</div>}
      <div className="res-grid">
        {rows.map((r) => (
          <div className="res-card" key={r.id}>
            <div className="res-top">
              <span className="res-ico">{r.id === "RES-FIN" ? "🔒" : "📁"}</span>
              <b>{r.name}</b>
              <span className="pill">{r.id}</span>
              <span className="spacer" />
              <span className={cls("pill", r.exists ? "ok" : "bad")}>
                {r.exists ? "已挂载" : "目录缺失"}
              </span>
            </div>
            <div className="mono hint res-path">{r.path}</div>
            <div className="res-files">
              {r.files.length === 0 && <div className="hint">目录为空</div>}
              {r.files.map((f) => (
                <div className="res-file" key={f.rel}>
                  <span>📄</span>
                  <span className="mono">{f.rel}</span>
                  <span className="spacer" />
                  <span className="hint">{f.bytes} B · {f.mtime}</span>
                </div>
              ))}
            </div>
            <div className="res-grants">
              {r.grants.length === 0 && <span className="hint">无授权</span>}
              {r.grants.map((g) => (
                <span className="chip" key={g.agent_id + g.permission}>
                  {g.name || g.agent_id} · {g.permission}
                </span>
              ))}
            </div>
            <div className="actions">
              <button className="btn danger" disabled={busy === r.id || !r.exists}
                      onClick={() => drill(r)}>
                {busy === r.id ? "启动中…" : "发起越权演练"}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const cny = (n) => "¥" + (Number(n) || 0).toLocaleString("zh-CN",
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function FinOpsView() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [note, setNote] = useState("");
  // v4.3：项目预算池 + 待批追加
  const [budgets, setBudgets] = useState([]);
  const [pending, setPending] = useState([]);
  const [busyBudget, setBusyBudget] = useState("");
  const [expandedBudgetPid, setExpandedBudgetPid] = useState(null);
  const [stageBudgets, setStageBudgets] = useState({});
  const [loadingBudgetPid, setLoadingBudgetPid] = useState("");

  async function toggleBudgetStages(pid) {
    if (expandedBudgetPid === pid) {
      setExpandedBudgetPid(null);
      return;
    }
    setExpandedBudgetPid(pid);
    if (!stageBudgets[pid]) {
      setLoadingBudgetPid(pid);
      try {
        const res = await api.budget(pid);
        setStageBudgets(prev => ({ ...prev, [pid]: res.stages || [] }));
      } catch {
        setStageBudgets(prev => ({ ...prev, [pid]: [] }));
      } finally {
        setLoadingBudgetPid("");
      }
    }
  }

  function exportCsv() {
    if (!data) return;
    const t = data.totals || {};
    const lines = [
      `\ufeff"财务总览 · FinOps 用量与费用报表 (近 ${days} 天)"`,
      `"导出时间","${new Date().toLocaleString()}"`,
      `"总支出 (元)","${t.cost || 0}","总 Token","${t.tokens || 0}","运行次数","${t.runs || 0}","单次均价 (元)","${t.avg_run_cost || 0}"`,
      "",
      `"【按部门消耗统计】"`,
      `"部门名称","部门 ID","参与员工数","运行次数","消耗 Token","折算费用 (元)"`,
      ...(data.by_dept || []).map(d => `"${d.dept_name}","${d.dept_id}","${d.agents}","${d.runs}","${d.tokens}","${d.cost}"`),
      "",
      `"【按模型消耗统计】"`,
      `"模型名称","运行次数","消耗 Token","折算费用 (元)"`,
      ...(data.by_model || []).map(m => `"${m.model}","${m.runs}","${m.tokens}","${m.cost}"`),
      "",
      `"【按员工消耗统计 (Top 50)】"`,
      `"员工名称","所属部门","运行次数","消耗 Token","折算费用 (元)"`,
      ...(data.by_agent || []).slice(0, 50).map(a => `"${a.agent_name}","${a.dept_name}","${a.runs}","${a.tokens}","${a.cost}"`),
      "",
      `"【每日支出明细】"`,
      `"日期","消耗 Token","支出金额 (元)"`,
      ...(data.series || []).map(s => `"${s.date}","${s.tokens}","${s.cost}"`)
    ];
    const blob = new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `finops_report_${days}d_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }
  const load = useCallback(async () => {
    try { setData(await api.finops(days)); } catch (e) { setNote(e.message || "加载失败"); }
  }, [days]);
  const loadBudgets = useCallback(async () => {
    try {
      const [p, a] = await Promise.all([api.budgetProjects(), api.budgetApprovals()]);
      setBudgets(p.projects || []); setPending(a.approvals || []);
    } catch { /* 预算接口挂了不影响财务总览 */ }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadBudgets(); }, [loadBudgets]);

  async function decideBudget(ap, approve) {
    setBusyBudget(ap.id);
    try { await api.decideBudget(ap.id, approve); }
    catch (e) { setNote(e.message || "审批失败"); }
    finally { setBusyBudget(""); loadBudgets(); load(); }
  }

  if (!data) return <div className="main"><div className="empty"><span className="spin" /> 正在算账…</div></div>;
  const t = data.totals || {};
  const maxCost = Math.max(1, ...data.series.map((s) => s.cost));

  return (
    <div className="main">
      <div className="topbar">
        <div className="avatar">¥</div>
        <div>
          <div className="who">财务总览 · FinOps</div>
          <div className="sub">token 折算人民币 · 按部门 / 员工 / 模型 · 近 {days} 天</div>
        </div>
        <div className="spacer" />
        <div className="seg" style={{ margin: 0, width: 210 }}>
          {[7, 30, 90].map((d) => (
            <button key={d} className={cls("seg-btn", days === d && "on")}
                    onClick={() => setDays(d)}>{d} 天</button>
          ))}
        </div>
        <button className="btn primary" onClick={exportCsv} title="导出当前周期内的用量与账单报表为 CSV 文件">导出 CSV 报表</button>
        <button className="btn ghost" onClick={load}>刷新</button>
      </div>
      <div className="stream">
        <div className="stream-inner">
          {note && <div className="note-bar">{note}</div>}
          <div className="fin-hero">
            <div className="fin-hero-main">
              <span className="fin-label">总支出（估算）</span>
              <b className="fin-big">{cny(t.cost)}</b>
              <span className="hint">{(t.tokens || 0).toLocaleString()} tokens · {t.runs || 0} 次运行</span>
            </div>
            <div className="fin-kpi"><span>单次均价</span><b>{cny(t.avg_run_cost)}</b></div>
            <div className="fin-kpi"><span>参与员工</span><b>{t.agents || 0}</b></div>
            <div className="fin-kpi"><span>预算池使用</span><b>{cny(data.budget.used_cny)} / {cny(data.budget.limit_cny)}</b>
              <span className="hint">{(data.budget.left_tokens || 0).toLocaleString()} tokens 可用</span></div>
          </div>

          <div className="fin-cols">
            <div className="fin-card">
              <h5>项目预算池（动态财务引擎）</h5>
              <table className="fin-table">
                <thead><tr><th>项目</th><th>预算池</th><th>已花</th><th>剩余</th><th>状态 / 阶段</th></tr></thead>
                <tbody>
                  {(budgets || []).map((b) => (
                    <React.Fragment key={b.project_id}>
                      <tr style={{ cursor: "pointer" }} onClick={() => toggleBudgetStages(b.project_id)}>
                        <td>
                          <b>{b.name}</b>
                          <span className="hint"> · {b.project_id}</span>
                          <span style={{ marginLeft: 8, fontSize: 11, color: "var(--accent)" }}>
                            {expandedBudgetPid === b.project_id ? "▲ 收起" : "▼ 阶段明细"}
                          </span>
                        </td>
                        <td className="mono">{b.pool.toLocaleString()}</td>
                        <td className="mono">{b.spent.toLocaleString()}</td>
                        <td className="mono money">{b.remaining.toLocaleString()}</td>
                        <td className="hint">{b.state === "paused" ? "暂停待批"
                          : b.state === "halted" ? "已停摆" : `${b.pct}%`}</td>
                      </tr>
                      {expandedBudgetPid === b.project_id && (
                        <tr>
                          <td colSpan="5" style={{ background: "var(--panel)", padding: "10px 14px" }}>
                            <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6 }}>
                              阶段预算分解（WBS 配额与花费）：
                              {loadingBudgetPid === b.project_id && <span style={{ marginLeft: 8, color: "var(--muted)" }}>加载中…</span>}
                            </div>
                            {stageBudgets[b.project_id]?.length ? (
                              <table className="fin-table" style={{ margin: 0, fontSize: 12 }}>
                                <thead>
                                  <tr>
                                    <th>阶段名称</th>
                                    <th>角色</th>
                                    <th>基础预估</th>
                                    <th>阶段配额 (tokens)</th>
                                    <th>已花 (tokens)</th>
                                    <th>使用率</th>
                                    <th>状态</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {stageBudgets[b.project_id].map((st) => (
                                    <tr key={st.key}>
                                      <td>{st.title || st.key}</td>
                                      <td><span className="pill">{st.role || "未分配"}</span></td>
                                      <td className="mono">{(st.base_estimate || 0).toLocaleString()}</td>
                                      <td className="mono">{(st.quota || 0).toLocaleString()}</td>
                                      <td className="mono">{(st.spent || 0).toLocaleString()}</td>
                                      <td>
                                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                          <div style={{ width: 50, height: 6, background: "var(--border)", borderRadius: 3, overflow: "hidden" }}>
                                            <div style={{ width: `${Math.min(100, st.pct || 0)}%`, height: "100%", background: (st.pct || 0) >= 80 ? "var(--bad)" : "var(--accent)" }} />
                                          </div>
                                          <span className="mono">{st.pct || 0}%</span>
                                        </div>
                                      </td>
                                      <td><span className={`pill ${st.state === "done" ? "ok" : st.state === "awaiting_budget" ? "bad" : ""}`}>{st.state}</span></td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            ) : (
                              <p className="hint" style={{ margin: 0 }}>{loadingBudgetPid === b.project_id ? "正在读取阶段配额…" : "该项目暂无阶段预算拆分记录。"}</p>
                            )}
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                  {(budgets || []).length === 0 && <tr><td colSpan="5" className="hint">还没有项目预算池</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="fin-card">
              <h5>待批追加预算（{pending.length}）</h5>
              {pending.length === 0
                ? <p className="hint">没有待批的追加申请。</p>
                : pending.map((ap) => (
                  <div key={ap.id} className="fin-budget-row">
                    <div style={{ flex: 1 }}>
                      <b>{ap.project_id}</b> · 阶段 {ap.stage_key}
                      <div className="hint">申请 {ap.requested_tokens.toLocaleString()} tokens
                        {" · "}池内可用 {ap.remaining.toLocaleString()}</div>
                    </div>
                    <button className="btn" disabled={busyBudget === ap.id}
                            onClick={() => decideBudget(ap, true)}>批准</button>
                    <button className="btn ghost" disabled={busyBudget === ap.id}
                            onClick={() => decideBudget(ap, false)}>驳回</button>
                  </div>
                ))}
            </div>
          </div>

          <div className="fin-cols">
            <div className="fin-card">
              <h5>按部门</h5>
              <table className="fin-table">
                <thead><tr><th>部门</th><th>员工</th><th>tokens</th><th>费用</th></tr></thead>
                <tbody>
                  {data.by_dept.map((d) => (
                    <tr key={d.dept_id}>
                      <td>{d.dept_name}</td><td>{d.agents}</td>
                      <td className="mono">{d.tokens.toLocaleString()}</td>
                      <td className="mono money">{cny(d.cost)}</td>
                    </tr>
                  ))}
                  {data.by_dept.length === 0 && <tr><td colSpan="4" className="hint">还没有消耗记录</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="fin-card">
              <h5>按模型</h5>
              <table className="fin-table">
                <thead><tr><th>模型</th><th>运行</th><th>tokens</th><th>费用</th></tr></thead>
                <tbody>
                  {data.by_model.map((m) => (
                    <tr key={m.model}>
                      <td className="mono">{m.model}</td><td>{m.runs}</td>
                      <td className="mono">{m.tokens.toLocaleString()}</td>
                      <td className="mono money">{cny(m.cost)}</td>
                    </tr>
                  ))}
                  {data.by_model.length === 0 && <tr><td colSpan="4" className="hint">还没有消耗记录</td></tr>}
                </tbody>
              </table>
            </div>
          </div>

          <div className="fin-card">
            <h5>每日支出（人民币）</h5>
            <div className="fin-bars">
              {data.series.map((s) => (
                <div className="fin-bar-col" key={s.date} title={`${s.date} · ${cny(s.cost)} · ${s.tokens} tok`}>
                  <div className="fin-bar" style={{ height: Math.max(2, (s.cost / maxCost) * 100) + "%" }} />
                </div>
              ))}
            </div>
            <div className="fin-axis"><span>{data.series[0]?.date}</span><span>今天</span></div>
          </div>

          <div className="fin-card">
            <h5>按员工（Top 20）</h5>
            <table className="fin-table">
              <thead><tr><th>员工</th><th>部门</th><th>运行</th><th>tokens</th><th>费用</th></tr></thead>
              <tbody>
                {data.by_agent.slice(0, 20).map((a) => (
                  <tr key={a.agent_id}>
                    <td>{a.agent_name}</td><td>{a.dept_name}</td><td>{a.runs}</td>
                    <td className="mono">{a.tokens.toLocaleString()}</td>
                    <td className="mono money">{cny(a.cost)}</td>
                  </tr>
                ))}
                {data.by_agent.length === 0 && <tr><td colSpan="5" className="hint">还没有消耗记录</td></tr>}
              </tbody>
            </table>
          </div>

          <div className="hint fin-pricing">
            计价口径：{data.pricing.note} · 输入占比 {(data.pricing.input_ratio * 100).toFixed(0)}% ·
            当前模型 {data.pricing.default_model} · 单价见 .env 的 MODEL_PRICES_JSON（元 / 百万 token）。
          </div>
        </div>
      </div>
    </div>
  );
}

export default FinOpsView;
