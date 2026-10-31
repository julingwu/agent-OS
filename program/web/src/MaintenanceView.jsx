import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

/* 系统维护：只增不减的运行残留，按类别 + 保留期批量清理。
   治理证据（审批、审计）不做单条删除——这里只能整批按保留期清，且清理动作本身留痕。 */

const ORDER = ["run_stream", "deleted_artifacts", "orphan_files", "orphan_traces", "audit_logs", "wecom_outbox"];
const DAY_BASED = new Set(["run_stream", "orphan_files", "audit_logs", "wecom_outbox"]);
const HINT = {
  run_stream: "每个 Run 逐条推送的思考/工具事件，只在执行当下有意义",
  deleted_artifacts: "已删除产物的数据库记录，以及仍躺在磁盘上的残留文件",
  orphan_files: "工作区里超过保留期、且没有任何记录引用的文件（兜底清理，防功能漏删）",
  orphan_traces: "对应 Run 已经不存在的追踪记录与跨度",
  audit_logs: "审计流水：按保留期整批清理，不提供单条删除",
  wecom_outbox: "已发出的企业微信消息副本",
};

export default function MaintenanceView() {
  const [stats, setStats] = useState(null);
  const [picked, setPicked] = useState(["run_stream", "deleted_artifacts", "orphan_files", "orphan_traces"]);
  const [days, setDays] = useState({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.maintenanceStats();
      setStats(r);
      setDays((cur) => ({ ...r.default_days, ...cur }));
      setError("");
    } catch (e) {
      setError(e.message || "读取维护统计失败");
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const counts = stats?.counts || {};
  const total = picked.reduce((n, k) => n + (counts[k] || 0), 0);

  function toggle(key) {
    setConfirming(false);
    setPicked((cur) => (cur.includes(key) ? cur.filter((x) => x !== key) : [...cur, key]));
  }

  async function dryRun() {
    setBusy(true); setNote(""); setError(""); setConfirming(false);
    try {
      const r = await api.maintenancePurge({ categories: picked, dry_run: true, days });
      const parts = Object.entries(r.planned || {}).map(([k, v]) => `${k} ${v}`).join(" · ");
      setNote(`预演完成：将清理 ${parts}；可回收文件 ${r.reclaimable_files} 个。库里没有变动。`);
    } catch (e) { setError(e.message || "预演失败"); }
    finally { setBusy(false); }
  }

  async function purge() {
    setBusy(true); setNote(""); setError("");
    try {
      const r = await api.maintenancePurge({ categories: picked, dry_run: false, days });
      const parts = Object.entries(r.deleted || {}).map(([k, v]) => `${k} ${v}`).join(" · ");
      setNote(`清理完成：删除 ${parts || "0 条"}；回收文件 ${r.files_removed} 个`
              + (r.files_skipped ? `（${r.files_skipped} 个在允许范围外，已跳过）` : ""));
      setConfirming(false);
      await load();
    } catch (e) { setError(e.message || "清理失败"); }
    finally { setBusy(false); }
  }

  return (
    <div className="main">
      <div className="topbar">
        <div className="avatar">维</div>
        <div>
          <div className="who">系统维护</div>
          <div className="sub">清理只增不减的运行残留；治理证据只按保留期整批处理</div>
        </div>
        <span className="spacer" />
        <button className="btn ghost" disabled={busy} onClick={load}>刷新统计</button>
      </div>

      <div className="stream">
        <div className="stream-inner">
          {error && <div className="integration-alert" role="alert">{error}</div>}
          {note && <div className="note-bar" role="status">{note}</div>}

          <div className="fin-card">
            <div style={{ textAlign: "left" }}>
            <h5>可清理项目</h5>
            <p className="hint">
              勾选后先预演，确认数量再执行。磁盘文件只会删除工作区 / data 目录内的残留。
            </p>
            {!stats && <div className="hint">正在读取…</div>}
            {stats && ORDER.filter((k) => k in (stats.categories || {})).map((key) => (
              <div key={key} className="maint-row" style={{
                display: "flex", alignItems: "center", gap: 12, textAlign: "left",
                padding: "10px 0", borderBottom: "1px solid var(--line-soft)",
              }}>
                <input type="checkbox" checked={picked.includes(key)} onChange={() => toggle(key)}
                       aria-label={`选择清理 ${stats.categories[key]}`} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <b style={{ fontSize: 13 }}>{stats.categories[key]}</b>
                  <div className="hint">{HINT[key]}</div>
                </div>
                {DAY_BASED.has(key) && (
                  <label className="hint" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    保留
                    <input type="number" min="0" value={days[key] ?? ""} style={{ width: 64 }}
                           onChange={(e) => setDays({ ...days, [key]: Number(e.target.value) })} />
                    天
                  </label>
                )}
                <b className="money" style={{ minWidth: 56, textAlign: "right" }}>
                  {counts[key] ?? 0}
                </b>
              </div>
            ))}
            {stats && (
              <div className="hint" style={{ marginTop: 10 }}>
                本次勾选合计 {total} 条 · 可回收文件 {stats.reclaimable_files} 个
              </div>
            )}
            <div className="actions" style={{ marginTop: 14, display: "flex", gap: 8 }}>
              <button className="btn ghost" disabled={busy || !picked.length} onClick={dryRun}>
                {busy ? "处理中…" : "预演（不删数据）"}
              </button>
              {confirming
                ? <button className="btn danger" disabled={busy} onClick={purge}>确认清理</button>
                : <button className="btn danger" disabled={busy || !picked.length}
                          onClick={() => setConfirming(true)}>执行清理</button>}
              {confirming && <button className="btn ghost" onClick={() => setConfirming(false)}>取消</button>}
            </div>
            </div>
          </div>

          <div className="hint" style={{ textAlign: "left" }}>
            说明：审批单、决策包等治理证据不在这里删除；组织经验、评测样本、流水线项目请在各自页面删除，
            它们会连带清理自己的关联数据与磁盘交付物。
          </div>
        </div>
      </div>
    </div>
  );
}
