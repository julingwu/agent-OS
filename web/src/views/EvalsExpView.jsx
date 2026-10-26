import React, { useState, useEffect, useCallback } from "react";
import api from "../api.js";
import { cls } from "./common.jsx";

export function EvalsView() {
  const [data, setData] = useState({ sets: [], batches: [] });
  const [detail, setDetail] = useState(null);
  const [runner, setRunner] = useState("mock_acp");
  const [promptMode, setPromptMode] = useState("stored");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [pending, setPending] = useState("");   // 删除二次确认的 id
  const load = useCallback(async () => {
    try {
      const r = await api.evals();
      setData(r);
      const running = (r.batches || []).find((b) => b.state === "running");
      if (running && !detail) {
        try { setDetail(await api.evalBatch(running.id)); } catch { /* ignore */ }
      }
    } catch { /* ignore */ }
  }, [detail]);
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [load]);

  async function runAll() {
    setBusy("all"); setNote("");
    try {
      const r = await api.evalRegress({ runner, prompt_mode: promptMode });
      setNote(`回归批次 ${r.batch_id} 已启动（${r.sets} 条 · ${r.runner} · Prompt ${r.prompt_mode}）`);
      await load();
    } catch (e) { setNote(e.message || "启动失败"); }
    finally { setBusy(""); }
  }
  async function runOne(s) {
    setBusy(s.id); setNote("");
    try {
      const r = await api.evalRunOne(s.id, { runner, prompt_mode: promptMode });
      setNote(`「${s.name}」回归已启动：${r.batch_id}`);
      await load();
    } catch (e) { setNote(e.message || "启动失败"); }
    finally { setBusy(""); }
  }
  async function openBatch(b) {
    try { setDetail(await api.evalBatch(b.id)); } catch { setDetail(null); }
  }
  async function removeSet(s) {
    if (pending !== s.id) { setPending(s.id); return; }
    setBusy(s.id); setNote(""); setPending("");
    try {
      const r = await api.deleteEvalSet(s.id);
      setNote(`已删除基准「${s.name}」及其 ${r.results_removed || 0} 条评测结果`);
      if (detail?.results?.some((x) => x.set_id === s.id)) setDetail(null);
      await load();
    } catch (e) { setNote(e.message || "删除失败"); }
    finally { setBusy(""); }
  }
  async function removeBatch(b) {
    if (pending !== b.id) { setPending(b.id); return; }
    setBusy(b.id); setNote(""); setPending("");
    try {
      const r = await api.deleteEvalBatch(b.id);
      setNote(`已删除批次「${b.name}」及其 ${r.results_removed || 0} 条结果`);
      if (detail?.batch?.id === b.id) setDetail(null);
      await load();
    } catch (e) { setNote(e.message || "删除失败"); }
    finally { setBusy(""); }
  }

  const sets = data.sets || [];
  const batches = data.batches || [];
  return (
    <div className="main">
      <div className="topbar">
        <div className="avatar">测</div>
        <div>
          <div className="who">离线评测与回归</div>
          <div className="sub">基准 {sets.length} 条 · 批次 {batches.length} 个 · 当前 runner {runner}</div>
        </div>
        <div className="spacer" />
        <select value={promptMode} onChange={(e) => setPromptMode(e.target.value)} className="fin-select">
          <option value="stored">基准原文 Prompt</option>
          <option value="current">当前模板 Prompt</option>
        </select>
        <select value={runner} onChange={(e) => setRunner(e.target.value)} className="fin-select">
          <option value="mock_acp">零成本 mock</option>
          <option value="acp">真实 ACP（消耗 token）</option>
        </select>
        <button className="btn primary" disabled={busy === "all" || sets.length === 0} onClick={runAll}>
          {busy === "all" ? "启动中…" : "一键跑全部回归"}
        </button>
      </div>
      <div className="stream">
        <div className="stream-inner">
          {note && <div className="note-bar">{note}</div>}
          <div className="fin-cols">
            <div className="fin-card">
              <h5>基准用例</h5>
              {sets.length === 0 && <div className="hint">还没有基准。去任意已完成 Run 的「运行详情 → 工作产物」点【设为基准用例】。</div>}
              {sets.map((s) => (
                <div className="eval-set" key={s.id}>
                  <div className="eval-set-main">
                    <b>{s.name}</b>
                    <span className="pill">{s.model || "默认模型"}</span>
                    {s.last_score != null && <span className="pill ok">上次 {s.last_score}</span>}
                    <div className="hint">{s.task_title} · {s.output_chars} 字</div>
                  </div>
                  <button className="btn ghost" disabled={busy === s.id}
                          onClick={() => runOne(s)}>
                    {busy === s.id ? "启动中…" : "跑这条"}
                  </button>
                  <button className={cls("btn", pending === s.id ? "danger" : "ghost")}
                          disabled={busy === s.id} onClick={() => removeSet(s)}>
                    {pending === s.id ? "确认删除" : "删除"}
                  </button>
                </div>
              ))}
            </div>
            <div className="fin-card">
              <h5>回归批次</h5>
              {batches.length === 0 && <div className="hint">还没有回归记录</div>}
              {batches.map((b) => (
                <div className={cls("eval-batch", b.state === "running" && "running")} key={b.id}
                     onClick={() => openBatch(b)}>
                  <div>
                    <b>{b.name}</b>
                    <div className="hint">{b.set_count} 条 · {b.runner} · Prompt {b.mode} · {b.created_at}</div>
                  </div>
                  <span className="spacer" />
                  <span className={cls("pill", b.state === "done" ? "ok" : b.state === "failed" ? "bad" : "warn")}>
                    {b.state}
                  </span>
                  <b className="money">{b.avg_score ? b.avg_score.toFixed(1) : "—"}</b>
                  <button className={cls("btn", pending === b.id ? "danger" : "ghost")}
                          disabled={busy === b.id}
                          onClick={(e) => { e.stopPropagation(); removeBatch(b); }}>
                    {pending === b.id ? "确认删除" : "删除"}
                  </button>
                </div>
              ))}
            </div>
          </div>

          {detail && (
            <div className="fin-card">
              <h5>批次详情 · {detail.batch?.name}</h5>
              <table className="fin-table">
                <thead><tr><th>基准</th><th>相似度</th><th>大模型分</th><th>综合分</th><th>状态</th></tr></thead>
                <tbody>
                  {(detail.results || []).map((r) => (
                    <tr key={r.id}>
                      <td>{r.set_name || r.set_id}<div className="hint">{r.judge_reason || ""}</div></td>
                      <td className="mono">{r.similarity?.toFixed(1)}</td>
                      <td className="mono">{r.judge_score != null ? r.judge_score.toFixed(1) : "—"}</td>
                      <td><div className="score-bar"><i style={{ width: Math.min(100, r.score) + "%" }} /></div>
                        <span className="mono">{r.score?.toFixed(1)}</span></td>
                      <td><span className={cls("pill", r.state === "ok" ? "ok" : "bad")}>{r.state}</span></td>
                    </tr>
                  ))}
                  {(!detail.results || detail.results.length === 0) && (
                    <tr><td colSpan="5" className="hint">批次还没产出结果</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          <div className="hint">回归用当前 Prompt 模板与当前员工模型重跑，再与基准产出对比；大模型打分可在 .env 用 EVAL_JUDGE=0 关闭。</div>
        </div>
      </div>
    </div>
  );
}

export default EvalsView;
