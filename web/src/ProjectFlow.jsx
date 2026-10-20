import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

// V4.1：阶段全景 + 实时执行流 + 失败诊断。都挂在项目卡上，不新开页面。

const STAGE_STATE = { done: "已完成", active: "进行中", pending: "待开始", blocked: "已失败",
                      awaiting_budget: "待批预算", halted: "已停摆" };

const fmtTokens = (n) => Number(n || 0).toLocaleString();

const fmtMs = (ms) => (ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : Math.round(ms || 0) + "ms");

/* ------------------------------------------------ SSE：逐条执行流 */
export function LiveStream({ runId, onFinished }) {
  const [lines, setLines] = useState([]);
  const [state, setState] = useState("");
  const [done, setDone] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!runId) return undefined;
    setLines([]); setState(""); setDone(false);
    const es = new EventSource(api.streamUrl(`/runs/${runId}/live-stream`));
    const push = (kind, label) => (ev) => {
      let d = {};
      try { d = JSON.parse(ev.data || "{}"); } catch { d = {}; }
      setLines((list) => [...list, { kind, label, ...d }].slice(-200));
    };
    es.addEventListener("thinking", push("thinking", "思考"));
    es.addEventListener("tool_call", push("tool", "工具调用"));
    es.addEventListener("tool_result", push("result", "工具结果"));
    es.addEventListener("model", push("model", "模型"));
    es.addEventListener("span", push("span", "步骤"));
    es.addEventListener("status", (ev) => {
      try { setState(JSON.parse(ev.data).state || ""); } catch { /* 忽略坏帧 */ }
    });
    es.addEventListener("done", () => { setDone(true); es.close(); onFinished?.(); });
    return () => es.close();
  }, [runId, onFinished]);

  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [lines]);

  return (
    <div className="live-wrap">
      <div className="live-head">
        <span className={cls2("live-dot", done ? "off" : "on")} />
        <b>实时执行流</b>
        <span className="hint">{runId} · {state || "连接中"} · {lines.length} 条</span>
      </div>
      <div className="live-stream" ref={boxRef} role="log" aria-live="polite">
        {!lines.length && <div className="hint">等待模型的第一条思考…</div>}
        {lines.map((l, i) => (
          <div key={i} className={cls2("live-line", l.kind)}>
            <span className="live-badge">{l.label}</span>
            <span className="live-ms">{l.start_ms != null ? fmtMs(Math.max(0, (l.end_ms || 0) - l.start_ms)) : ""}</span>
            <span className="live-name">{l.name || ""}</span>
            <span className="live-detail">{String(l.detail || "").slice(0, 400)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------ 失败诊断 */
export function FailureDiagnosis({ runId, taskId, onRetry }) {
  const [diag, setDiag] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!runId) return undefined;
    let alive = true;
    setDiag(null); setError("");
    api.runDiagnosis(runId).then((d) => { if (alive) setDiag(d); })
      .catch((e) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [runId]);

  async function retry() {
    if (!taskId || busy) return;
    setBusy(true); setError("");
    try { await api.retryTask(taskId, "按诊断建议重试"); onRetry?.(); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div className="diag-box" role="region" aria-label="失败诊断">
      <div className="diag-head">⚠ 失败诊断 <span className="hint">{runId}</span></div>
      {error && <div className="hint" role="alert">{error}</div>}
      {!diag && !error && <div className="hint">正在分析失败原因…</div>}
      {diag && (
        <>
          <p className="diag-cause">{diag.cause}</p>
          <ul className="diag-fixes">
            {(diag.fixes || []).map((f, i) => <li key={i}>{f}</li>)}
          </ul>
          <div className="diag-foot">
            <span className="hint">{diag.source === "llm" ? "模型诊断" : "规则诊断"}{diag.cached ? " · 已缓存" : ""}</span>
            {taskId && <button className="btn ghost" disabled={busy} onClick={retry}>一键重试</button>}
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------ 阶段详情 */
export function StageDetail({ projectId, stageKey, onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setError("");
    api.stageDetail(projectId, stageKey).then(setData).catch((e) => setError(e.message));
  }, [projectId, stageKey]);
  useEffect(() => { load(); }, [load]);

  if (error) {
    return (
      <div className="mask" onClick={onClose}><section className="modal wide" onClick={(e) => e.stopPropagation()}>
        <h3>阶段 {stageKey}</h3><p role="alert">{error}</p>
        <button className="btn" onClick={onClose}>关闭</button>
      </section></div>
    );
  }
  if (!data) {
    return <div className="mask" onClick={onClose}><section className="modal wide" onClick={(e) => e.stopPropagation()}>
      <h3>阶段 {stageKey}</h3><p className="hint">载入中…</p>
    </section></div>;
  }

  const st = data.stage;
  const lastRun = data.runs[0];
  const failedRun = data.runs.find((r) => r.state === "failed" || r.state === "cancelled");

  return (
    <div className="mask" onClick={onClose}>
      <section className="modal wide stage-detail" onClick={(e) => e.stopPropagation()}
               aria-label={`阶段详情 ${st.title}`}>
        <div className="stage-detail-head">
          <div>
            <div className="eyebrow">STAGE DETAILS</div>
            <h3>{st.seq + 1}. {st.title}</h3>
            <p className="hint">
              角色 {st.role_tag || "平台"} · 状态 {STAGE_STATE[st.state] || st.state}
              {st.ended_at ? ` · 完成于 ${st.ended_at}` : ""}
            </p>
          </div>
          <button className="icon-btn" aria-label="关闭阶段详情" onClick={onClose}>✕</button>
        </div>

        {st.state === "active" && lastRun && (
          <LiveStream runId={lastRun.run_id} onFinished={() => { load(); onChanged?.(); }} />
        )}
        {st.state === "blocked" && failedRun && (
          <FailureDiagnosis runId={failedRun.run_id} taskId={failedRun.task_id}
                            onRetry={() => { load(); onChanged?.(); }} />
        )}

        <h4>交付物（{data.deliverables.length}）</h4>
        {data.deliverables.length ? (
          <table className="fin-table"><thead><tr><th>类型</th><th>文件</th><th>大小</th><th>时间</th><th /></tr></thead>
            <tbody>{data.deliverables.map((d) => (
              <tr key={d.id}>
                <td>{d.kind}</td>
                <td className="mono">{String(d.file_path).split(/[\\/]/).pop()}</td>
                <td>{d.bytes} B</td>
                <td className="hint">{d.created_at}</td>
                <td><a className="text-button" href={api.stageFileUrl(projectId, stageKey, d.id)}>下载</a></td>
              </tr>))}</tbody>
          </table>
        ) : <p className="hint">这个阶段还没有交付物。</p>}

        <h4>闸门（{data.gates.length}）</h4>
        {data.gates.length ? (
          <ul className="gate-list">{data.gates.map((g) => (
            <li key={g.id}><span className={cls2("pill", g.state === "passed" ? "ok" : "bad")}>{g.state}</span>
              {g.name} <span className="hint">{g.note || ""}</span></li>
          ))}</ul>
        ) : <p className="hint">没有闸门记录。</p>}

        <h4>黑板（{data.artifacts.length}）</h4>
        {data.artifacts.length ? data.artifacts.map((a) => (
          <details key={a.id} className="bb-item">
            <summary>{a.title || a.stage_key} <span className="hint">{a.role_tag || "平台"} · {a.created_at}</span></summary>
            <pre>{a.content}</pre>
          </details>
        )) : <p className="hint">黑板为空。</p>}

        <h4>历史 Run（{data.runs.length}）</h4>
        {data.runs.length ? (
          <table className="fin-table"><thead><tr><th>Run</th><th>任务</th><th>状态</th><th>Tokens</th><th>Trace</th></tr></thead>
            <tbody>{data.runs.map((r) => (
              <tr key={r.run_id}>
                <td className="mono">{r.run_id}</td>
                <td>{r.title || r.task_id}</td>
                <td>{r.state}</td>
                <td>{r.usage_tokens ?? "—"}</td>
                <td className="hint">{r.trace ? `${fmtMs(r.trace.total_ms || 0)} / ${r.trace.total_tokens || 0} tok` : "—"}</td>
              </tr>))}</tbody>
          </table>
        ) : <p className="hint">这个阶段没有真跑过 Run（文档阶段）。</p>}

        <h4>审计（最近 {Math.min(30, data.audit.length)} 条）</h4>
        <div className="timeline">{data.audit.slice(0, 30).map((a, i) => (
          <div key={i} className="tl-row"><span className="tl-act">{a.action}</span>
            <span className="hint">{a.created_at}</span></div>
        ))}</div>
      </section>
    </div>
  );
}

/* ------------------------------------------------ 预算池（v4.3） */
export function BudgetBar({ budget }) {
  if (!budget || !budget.pool) return null;
  const pct = Math.min(100, Math.round((budget.spent / budget.pool) * 100));
  const over = pct >= 80;
  return (
    <div className="budget-bar" role="region" aria-label="项目预算池">
      <div className="budget-top">
        <span>预算池 <b>{fmtTokens(budget.pool)}</b></span>
        <span>已花 <b>{fmtTokens(budget.spent)}</b></span>
        <span className={cls2("budget-left", over && "warn")}>
          剩余 <b>{fmtTokens(budget.remaining)}</b>
        </span>
        <span className="hint">
          弹性 {budget.elastic}{budget.capped ? ` · 已压到全局上限 ${fmtTokens(budget.cap)}` : ""}
          {" · "}80% 暂停线
        </span>
      </div>
      <div className="budget-track"><div className={cls2("budget-fill", over && "warn")}
                                       style={{ width: `${pct}%` }} /></div>
      <div className="budget-stages">
        {(budget.stages || []).map((s) => (
          <div key={s.key} className={cls2("budget-stage", s.pct >= 80 && "warn",
                                           s.state === "awaiting_budget" && "ask")}
               title={`${s.title}：${fmtTokens(s.spent)} / ${fmtTokens(s.quota)} tokens`}>
            <span className="bs-name">{s.title}</span>
            <span className="bs-num">{fmtTokens(s.spent)}/{fmtTokens(s.quota)}</span>
            <span className="bs-pct">{s.pct}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function BudgetAsk({ projectId, onChanged }) {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const load = useCallback(() => {
    api.budgetApprovals()
      .then((d) => setItems((d.approvals || []).filter((a) => a.project_id === projectId)))
      .catch(() => {});
  }, [projectId]);
  useEffect(() => { load(); }, [load]);
  if (!items.length) return null;

  async function decide(ap, approve) {
    setBusy(ap.id); setNote("");
    try {
      const out = await api.decideBudget(ap.id, approve);
      if (out && out.state === "exhausted") setNote("预算池已见底，项目已停摆");
      else if (approve) await api.resumeProject(projectId);
    } catch (e) { setNote(e.message || "审批失败"); }
    finally { setBusy(""); load(); onChanged?.(); }
  }

  return (
    <div className="budget-ask" role="region" aria-label="追加预算审批">
      {items.map((ap) => (
        <div key={ap.id} className="budget-ask-row">
          <div>
            <b>阶段 {ap.stage_key} 额度已用 80%</b>
            <p className="hint">
              申请追加 {fmtTokens(ap.requested_tokens)} tokens · 池内可用 {fmtTokens(ap.remaining)}
              {" · "}{ap.reason || "触发 80% 暂停线"}
            </p>
          </div>
          <div className="budget-ask-actions">
            <button className="btn" disabled={busy === ap.id}
                    onClick={() => decide(ap, true)}>批准并继续</button>
            <button className="btn ghost" disabled={busy === ap.id}
                    onClick={() => decide(ap, false)}>驳回</button>
          </div>
        </div>
      ))}
      {note && <div className="hint" role="alert">{note}</div>}
    </div>
  );
}

/* ------------------------------------------------ 阶段全景 */
export default function ProjectFlow({ projectId, data, onChanged }) {
  const [open, setOpen] = useState(null);
  const stages = data?.stages || [];
  if (!stages.length) return null;
  return (
    <div className="flow">
      <div className="flow-head">
        <b>阶段全景</b>
        <span className="hint">
          {stages.filter((s) => s.state === "done").length}/{stages.length} 完成
          {data?.project?.flow_id ? ` · ${data.project.flow_id}` : ""} · 点阶段看交付物/审计/Trace
        </span>
      </div>
      <BudgetBar budget={data?.budget} />
      <BudgetAsk projectId={projectId} onChanged={onChanged} />
      <ol className="flow-timeline">
        {stages.map((s) => {
          const failed = s.state === "blocked";
          const active = s.state === "active";
          return (
            <li key={s.key}>
              <button className={cls2("flow-node", s.state, failed && "failed")}
                      onClick={() => setOpen(s.key)} title={`${s.title}（${STAGE_STATE[s.state] || s.state}）`}>
                <span className="flow-dot" aria-hidden />
                <span className="flow-title">{s.seq + 1}. {s.title || s.key}</span>
                <span className="flow-role">{s.role_tag || "平台"}</span>
                <span className="flow-state">{STAGE_STATE[s.state] || s.state}</span>
                {failed && <span className="flow-x" aria-label="失败">✕</span>}
                {active && <span className="flow-spin" aria-label="进行中" />}
              </button>
            </li>
          );
        })}
      </ol>
      {open && <StageDetail projectId={projectId} stageKey={open}
                            onClose={() => setOpen(null)} onChanged={onChanged} />}
    </div>
  );
}

function cls2(...args) { return args.filter(Boolean).join(" "); }
