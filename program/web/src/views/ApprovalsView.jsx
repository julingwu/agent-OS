import React, { useState, useEffect, useCallback } from "react";
import api from "../api.js";
import { initials, cls } from "./common.jsx";

export const APPROVAL_STATE = { pending: "待审批", approved: "已批准", rejected: "已拒绝" };

export function ApprovalCard({ meta, onAct }) {
  const m = meta || {};
  const [state, setState] = useState(m.state || "pending");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function decide(ok) {
    if (busy) return;
    setBusy(true); setErr("");
    try {
      const r = await api.decideApproval(m.approval_id, ok);
      setState(r.state || (ok ? "approved" : "rejected"));
      onAct && onAct();
    } catch (e) { setErr(e.message || "审批失败"); }
    finally { setBusy(false); }
  }
  return (
    <div className="card approval-card">
      <div className="row">
        <h4>⛔ 越权拦截 · {APPROVAL_STATE[state] || state}</h4>
        <span className="spacer" />
        <span className="mono hint">{m.approval_id}</span>
      </div>
      <div className="ap-grid">
        <div><span>工具</span><b className="mono">{m.tool}</b></div>
        <div><span>资源</span><b>{m.resource_id || "—"}</b></div>
        <div className="wide"><span>目标</span><b className="mono">{m.target || "—"}</b></div>
        <div><span>Run</span><b className="mono">{m.run_id || "—"}</b></div>
        <div><span>会话</span><b className="mono">{String(m.session_id || "").slice(0, 10)}…</b></div>
      </div>
      <div className="hint">批准后用同一个 run + 会话从断点继续，只补被拦下的一步，不重放前文。</div>
      {err && <div className="hint bad-text">{err}</div>}
      {state === "pending" && (
        <div className="actions">
          <button className="btn danger" disabled={busy} onClick={() => decide(false)}>拒绝</button>
          <button className="btn primary" disabled={busy} onClick={() => decide(true)}>
            {busy ? "处理中…" : "允许并续跑"}
          </button>
        </div>
      )}
    </div>
  );
}

export function ApprovalsView({ onChanged }) {
  const [data, setData] = useState({ pending: [], history: [] });
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const load = useCallback(async () => {
    try { setData(await api.approvals()); } catch { /* ignore */ }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [load]);

  async function decide(id, ok) {
    setBusy(id); setNote("");
    try {
      const r = await api.decideApproval(id, ok);
      setNote(ok ? (r.resumable ? "已批准 · 正在用原会话断点续跑…" : "已批准")
                 : "已拒绝 · 该操作不会执行");
      await load(); onChanged && onChanged();
    } catch (e) { setNote(e.message || "操作失败"); }
    finally { setBusy(""); }
  }

  async function approveAll() {
    if (!data.pending.length || busy) return;
    if (!confirm(`确定一次性批准全部 ${data.pending.length} 项待批操作并断点续跑吗？`)) return;
    setBusy("all"); setNote("");
    let success = 0;
    for (const ap of data.pending) {
      try {
        await api.decideApproval(ap.id, true);
        success++;
      } catch { /* continue */ }
    }
    setNote(`已批量批准 ${success} 项操作，正在断点续跑`);
    setBusy("");
    await load();
    onChanged && onChanged();
  }

  const pushed = (a) => a.pushed_at
    ? `已推送 ${a.push_channel === "wecom" ? "企微" : a.push_channel || "站内"}`
    : "推送中…";

  return (
    <div className="main">
      <div className="topbar">
        <div className="avatar warn">审</div>
        <div>
          <div className="who">审批中心</div>
          <div className="sub">越权拦截 / 高危工具 · 待批 {data.pending.length} 条</div>
        </div>
        <div className="spacer" />
        {data.pending.length > 1 && (
          <button className="btn primary" disabled={Boolean(busy)} onClick={approveAll}>
            {busy === "all" ? "正在批量处理…" : `一键全部批准 (${data.pending.length})`}
          </button>
        )}
        <button className="btn ghost" onClick={load}>刷新</button>
      </div>
      <div className="stream">
        <div className="stream-inner">
          {note && <div className="note-bar">{note}</div>}
          {data.pending.length === 0 && (
            <div className="empty">没有待批事项。去「文件库 → 资源目录」发起一次越权演练试试。</div>
          )}
          {data.pending.map((a) => (
            <div className="ap-card" key={a.id}>
              <div className="ap-head">
                <span className="avatar" style={{ width: 26, height: 26, fontSize: 11 }}>
                  {initials(a.agent_name || a.agent_id)}
                </span>
                <b>{a.agent_name || a.agent_id}</b>
                <span>试图</span><span className="mono">{a.tool}</span>
                <span>→</span><span className="pill bad">{a.resource_name || a.resource_id}</span>
                <span className="spacer" />
                <span className="hint">{a.created_at}</span>
              </div>
              <div className="ap-target mono">{a.target || "—"}</div>
              <div className="ap-meta">
                <span>Run <b className="mono">{a.run_id || "—"}</b></span>
                <span>会话 <b className="mono">{String(a.session_id || "").slice(0, 10)}…</b></span>
                <span>任务 {a.task_title || a.task_id || "—"}</span>
                <span className={a.pushed_at ? "ok-text" : "hint"}>{pushed(a)}</span>
              </div>
              <div className="actions">
                <button className="btn danger" disabled={busy === a.id}
                        onClick={() => decide(a.id, false)}>拒绝</button>
                <button className="btn primary" disabled={busy === a.id}
                        onClick={() => decide(a.id, true)}>
                  {busy === a.id ? "处理中…" : "允许并断点续跑"}
                </button>
              </div>
            </div>
          ))}

          {data.history.length > 0 && (
            <>
              <h5 style={{ marginTop: 18 }}>历史审批</h5>
              <table className="ap-table">
                <thead><tr><th>单号</th><th>员工</th><th>动作</th><th>资源</th><th>结果</th><th>时间</th></tr></thead>
                <tbody>
                  {data.history.slice(0, 20).map((a) => (
                    <tr key={a.id}>
                      <td className="mono">{a.id}</td>
                      <td>{a.agent_name || a.agent_id}</td>
                      <td className="mono">{a.tool}</td>
                      <td>{a.resource_name || a.resource_id || "—"}</td>
                      <td><span className={cls("pill", a.state === "approved" ? "ok" : "bad")}>
                        {APPROVAL_STATE[a.state] || a.state}</span></td>
                      <td className="hint">{a.decided_at || a.created_at}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default ApprovalsView;
