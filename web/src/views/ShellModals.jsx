import React, { useState, useEffect, useCallback } from "react";
import api from "../api";
import { cls, formatTokensCN } from "./common";

/* ------------------------------------------------------------------ login */
export function Login({ onDone }) {
  const [id, setId] = useState(localStorage.getItem("oa_id") || "");
  const [secret, setSecret] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setWaiting(true), 2500);
    return () => clearTimeout(t);
  }, []);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      await api.login(id.trim(), secret.trim());
      localStorage.setItem("oa_id", id.trim());
      onDone();
    } catch (e2) {
      setErr(e2.message === "BadCredentials" ? "身份 ID 或密钥不对" : e2.message);
    } finally { setBusy(false); }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h2>Agent OS</h2>
        <div className="sub">登录你的 AI 工作空间，开始与团队协作。</div>
        <div className="field">
          <label>身份 ID</label>
          <input value={id} onChange={(e) => setId(e.target.value)} placeholder="ID-xxxxxxxxxxxx" autoFocus />
        </div>
        <div className="field">
          <label>登录密钥</label>
          <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="••••••••" />
        </div>
        <button className="btn primary" style={{ width: "100%" }} disabled={busy || !id || !secret}>
          {busy ? "登录中…" : "进入控制台"}
        </button>
        {waiting && <div className="hint" style={{ marginTop: 10 }}>
          首次启动可能需要片刻；若长时间无响应，请确认工作空间服务已启动。
        </div>}
        {err && <div className="err">{err}</div>}
      </form>
    </div>
  );
}

/* ------------------------------------------------------------------ settings */
export function SettingsModal({ onClose, theme, setTheme, onChanged }) {
  const [form, setForm] = useState({ default_model: "", default_api_base: "", theme,
                                     flow_stage_budget: 20000, budget_elastic: 1.5,
                                     project_budget_cap: 1000000, budget_pause_ratio: 0.8 });
  const [jobs, setJobs] = useState([]);
  const [chats, setChats] = useState([]);
  const [job, setJob] = useState({ chat_id: "", title: "每日任务报告", at_time: "09:00" });
  const [kb, setKb] = useState([]);
  const [note, setNote] = useState("");

  useEffect(() => {
    (async () => {
      const [s, j, c, k] = await Promise.all([api.settings(), api.jobs(), api.chats(), api.kb()]);
      setForm({ default_model: s.default_model, default_api_base: s.default_api_base,
                theme: s.theme, flow_stage_budget: s.flow_stage_budget ?? 20000,
                budget_elastic: s.budget_elastic ?? 1.5,
                project_budget_cap: s.project_budget_cap ?? 1000000,
                budget_pause_ratio: s.budget_pause_ratio ?? 0.8 });
      setJobs(j.jobs); setChats(c.chats); setKb(k.documents);
      setJob((x) => ({ ...x, chat_id: c.chats.find((y) => y.kind === "group")?.id || c.chats[0]?.id || "" }));
    })();
  }, []);

  async function save() {
    setNote("");
    try {
      await api.saveSettings(form);
      setTheme(form.theme);
      setNote("设置已保存");
      onChanged && onChanged();
    } catch (err) {
      setNote(err.message || "保存失败，请检查输入参数");
    }
  }

  async function addJob() {
    if (!job.chat_id) return;
    await api.createJob({ ...job, prompt: "昨天的任务产出报告" });
    const j = await api.jobs(); setJobs(j.jobs);
  }

  async function upload(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    await api.uploadKb("LE-GROUP-HQ", f);
    const k = await api.kb(); setKb(k.documents);
  }

  return (
    <div className="mask" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <h3>设置</h3>

        <div className="section-title">模型全局配置</div>
        <div className="grid2">
          <div className="field"><label>默认模型</label>
            <input value={form.default_model} onChange={(e) => setForm({ ...form, default_model: e.target.value })} /></div>
          <div className="field"><label>默认 Base URL</label>
            <input value={form.default_api_base} onChange={(e) => setForm({ ...form, default_api_base: e.target.value })} /></div>
        </div>

        <div className="section-title">成本控制（动态预算）</div>
        <div className="grid2">
          <div className="field"><label>默认弹性系数（试错缓冲）</label>
            <input type="number" min="1" max="5" step="0.1"
                   value={form.budget_elastic ?? 1.5}
                   onChange={(e) => setForm({ ...form, budget_elastic: Number(e.target.value) })} />
          </div>
          <div className="field">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
              <label style={{ margin: 0 }}>项目全局最高预算</label>
              <span className="pill ok" style={{ fontSize: 11, fontWeight: 600 }}>
                {formatTokensCN(form.project_budget_cap)}
              </span>
            </div>
            <input type="number" min="50000" max="100000000000" step="50000"
                   value={form.project_budget_cap ?? 1000000}
                   onChange={(e) => setForm({ ...form, project_budget_cap: Number(e.target.value) })} />
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
              {[
                [1_000_000, "100万"],
                [10_000_000, "1000万"],
                [50_000_000, "5000万"],
                [100_000_000, "1亿"],
                [1_000_000_000, "10亿"],
                [10_000_000_000, "100亿"],
                [100_000_000_000, "1000亿"],
              ].map(([val, label]) => (
                <button
                  type="button"
                  key={val}
                  className={`btn ghost ${form.project_budget_cap === val ? "primary" : ""}`}
                  style={{ padding: "2px 8px", fontSize: 11, height: "auto" }}
                  onClick={() => setForm({ ...form, project_budget_cap: val })}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <p className="hint" style={{ marginTop: -4 }}>
          财务 Agent 按阶段估基础额度 × 弹性系数 = 初始额度；项目池超上限时等比压缩。
          {" "}阶段花到 {Math.round((form.budget_pause_ratio ?? 0.8) * 100)}% 自动暂停并要老板批钱。
        </p>

        <div className="section-title">外观</div>
        <div className="row">
          <span>主题</span><div className="spacer" />
          <button className={cls("btn", form.theme === "dark" && "primary")}
                  onClick={() => setForm({ ...form, theme: "dark" })}>深色</button>
          <button className={cls("btn", form.theme === "light" && "primary")}
                  onClick={() => setForm({ ...form, theme: "light" })}>浅色</button>
        </div>

        <div className="section-title">定时任务（每天定时在群里发报告）</div>
        {jobs.map((j) => (
          <div className="row" key={j.id}>
            <div>
              <div>{j.title} <span className="tag">{j.at_time}</span></div>
              <div className="role">{j.chat_id}</div>
            </div>
            <div className="spacer" />
            <button className="btn ghost" onClick={async () => { await api.runJob(j.id); setNote("已立即执行一次"); }}>立即执行</button>
            <button className="btn danger" onClick={async () => { await api.deleteJob(j.id); const x = await api.jobs(); setJobs(x.jobs); }}>停用</button>
          </div>
        ))}
        <div className="row">
          <input style={{ flex: 1 }} value={job.title} onChange={(e) => setJob({ ...job, title: e.target.value })} />
          <input style={{ width: 90 }} value={job.at_time} onChange={(e) => setJob({ ...job, at_time: e.target.value })} placeholder="09:00" />
          <select value={job.chat_id} onChange={(e) => setJob({ ...job, chat_id: e.target.value })}>
            {chats.map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
          </select>
          <button className="btn" onClick={addJob}>添加</button>
        </div>

        <div className="section-title">知识库（部门参考文档）</div>
        {kb.map((d) => (
          <div className="row" key={d.id}>
            <div>{d.name} <span className="tag">{d.bytes} B</span></div>
            <div className="spacer" />
            <button className="btn danger" onClick={async () => { await api.deleteKb(d.id); const k = await api.kb(); setKb(k.documents); }}>删除</button>
          </div>
        ))}
        <div className="row">
          <input type="file" onChange={upload} accept=".pdf,.docx,.txt,.md" />
          <span className="hint">上传后，该部门 Agent 干活时会自动带上</span>
        </div>

        {note && <div className="hint">{note}</div>}
        <div className="actions">
          <button className="btn primary" onClick={save}>保存设置</button>
          <button className="btn ghost" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ account */
export function AccountModal({ me, onClose }) {
  const [secret, setSecret] = useState("");
  const [note, setNote] = useState("");
  const [prefNote, setPrefNote] = useState("");
  const [acc, setAcc] = useState(null);
  const [prefs, setPrefs] = useState({});
  const [newKey, setNewKey] = useState("");
  const [newVal, setNewVal] = useState("");
  const [savingPrefs, setSavingPrefs] = useState(false);

  useEffect(() => {
    api.account().then((res) => {
      setAcc(res);
      setPrefs(res?.preferences || {});
    }).catch(() => {});
  }, []);

  async function change() {
    if (secret.length < 16) { setNote("至少 16 位"); return; }
    await api.changeSecret(secret);
    setSecret(""); setNote("密钥已更新，请重新登录");
  }

  async function savePrefs(nextPrefs) {
    const toSave = nextPrefs || prefs;
    setSavingPrefs(true);
    setPrefNote("");
    try {
      const res = await api.updatePreferences(toSave);
      setPrefs(res.preferences || toSave);
      setPrefNote("偏好已更新");
    } catch (e) {
      setPrefNote(e.message || "更新失败");
    } finally {
      setSavingPrefs(false);
      setTimeout(() => setPrefNote(""), 3500);
    }
  }

  function setPrefItem(k, v) {
    const next = { ...prefs };
    if (!v || !v.trim()) {
      delete next[k];
    } else {
      next[k] = v.trim();
    }
    setPrefs(next);
  }

  function removePrefItem(k) {
    const next = { ...prefs };
    delete next[k];
    setPrefs(next);
    savePrefs(next);
  }

  function addCustomPref() {
    if (!newKey.trim() || !newVal.trim()) return;
    const next = { ...prefs, [newKey.trim()]: newVal.trim() };
    setPrefs(next);
    setNewKey("");
    setNewVal("");
    savePrefs(next);
  }

  return (
    <div className="mask" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <h3>账号与个性化偏好</h3>
        <div className="row"><span>身份 ID</span><div className="spacer" /><span className="mono">{me.identity_id}</span></div>
        <div className="row"><span>角色</span><div className="spacer" /><span>{me.role}</span></div>
        <div className="row"><span>所属实体</span><div className="spacer" /><span>{me.entity_name}</span></div>

        <div className="section-title">AI 已记住的偏好（全链路交互遵循）</div>
        <p className="hint" style={{ marginTop: -4 }}>
          AI 团队在各阶段任务拆解、回复提示词中均会自动注入此偏好；日常会话中直接说「以后叫我老板」也会自动同步。
        </p>

        <div className="grid2" style={{ marginTop: 8 }}>
          <div className="field">
            <label>尊称 / 称谓偏好 (boss_title)</label>
            <input
              placeholder="例如：老板、总、队长、老师"
              value={prefs.boss_title || ""}
              onChange={(e) => setPrefItem("boss_title", e.target.value)}
            />
          </div>
          <div className="field">
            <label>交互风格偏好 (response_style)</label>
            <select
              value={prefs.response_style || ""}
              onChange={(e) => setPrefItem("response_style", e.target.value)}
            >
              <option value="">默认（严谨务实）</option>
              <option value="简洁专业">简洁专业（直出核心结论，少寒暄）</option>
              <option value="详尽深入">详尽深入（包含上下文及推演步骤）</option>
              <option value="商业实战">商业实战（强调商业收益与交付规格）</option>
            </select>
          </div>
        </div>

        <div className="field" style={{ marginTop: 8 }}>
          <label>当前偏好项清单</label>
          <div className="chips-wrap" style={{ display: "flex", flexWrap: "wrap", gap: 6, minHeight: 36, padding: "8px 10px", background: "var(--panel)", borderRadius: 8, border: "1px solid var(--border)" }}>
            {Object.keys(prefs).length === 0 && (
              <span className="hint">暂无记录（可直接在上方设置，或在下方添加自定义偏好键值）</span>
            )}
            {Object.entries(prefs).map(([k, v]) => (
              <span className="chip" key={k} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <b>{k}:</b> {v}
                <button
                  type="button"
                  style={{ border: "none", background: "transparent", cursor: "pointer", color: "inherit", opacity: 0.7, padding: "0 4px", fontSize: 13 }}
                  onClick={() => removePrefItem(k)}
                  title="删除此项偏好"
                >×</button>
              </span>
            ))}
          </div>
        </div>

        <div className="row" style={{ marginTop: 8, gap: 8 }}>
          <input
            style={{ width: 140 }}
            placeholder="自定义偏好键 (key)"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
          />
          <input
            style={{ flex: 1 }}
            placeholder="偏好内容 (value)"
            value={newVal}
            onChange={(e) => setNewVal(e.target.value)}
          />
          <button className="btn ghost" disabled={!newKey.trim() || !newVal.trim()} onClick={addCustomPref}>添加项</button>
          <button className="btn primary" disabled={savingPrefs} onClick={() => savePrefs()}>
            {savingPrefs ? "保存中…" : "保存偏好"}
          </button>
        </div>
        {prefNote && <div className={cls("hint", prefNote.includes("失败") ? "err" : "ok")} style={{ marginTop: 4 }}>{prefNote}</div>}

        <div className="section-title">修改密钥</div>
        <div className="field">
          <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="新的 HMAC secret（≥16 位）" />
        </div>
        {note && <div className="hint">{note}</div>}
        <div className="actions">
          <button className="btn primary" onClick={change}>更新密钥</button>
          <button className="btn ghost" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ help */
export function HelpModal({ onClose }) {
  return (
    <div className="mask" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <h3>怎么用</h3>
        <ol className="help-list">
          <li><b>派活</b>：在会话里直接说「写一篇 500 字的秋季护肤短文」。PM 会拆解、@ 相关 Agent、跑任务。</li>
          <li><b>闲聊</b>：「你好」「以后叫我老板」这类不会建任务；后者会被记成偏好，之后所有回复都这么叫你。</li>
          <li><b>看验收卡</b>：卡片里逐条判据，绿=通过、红=不通过、黄=待定（人工）。字数不达标会自动扩写一次，不用你管。</li>
          <li><b>人工签字</b>：可以点卡片上的「验收通过」，也可以等企微审批推送，在手机上点同意。</li>
          <li><b>群管理</b>：群聊右上角「⋯」→ 邀请 / 移出 / 退出（群主退出即解散）。</li>
          <li><b>员工管理</b>：组织架构里点任意 Agent，右侧可改人设、模型、API，或停用（软删除，审计留痕）。</li>
          <li><b>文件库</b>：所有产物集中在这里，可下载、可删除（同样软删除）。</li>
          <li><b>定时任务</b>：设置里加一条「每天 09:00 发昨日报告」，到点会自动在群里发。</li>
        </ol>
        <div className="actions"><button className="btn primary" onClick={onClose}>知道了</button></div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ about */
export function AboutModal({ onClose }) {
  const [info, setInfo] = useState(null);
  useEffect(() => { api.about().then(setInfo).catch(() => {}); }, []);
  return (
    <div className="mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>关于 Agent OS</h3>
        <div className="row"><span>版本</span><div className="spacer" /><span className="mono">v{info?.version || "5.1.0"}</span></div>
        <div className="row"><span>引擎</span><div className="spacer" /><span className="mono">{info?.engine || "DSH (ACP) + FastAPI"}</span></div>
        <div className="row"><span>外壳</span><div className="spacer" /><span className="mono">{info?.shell || "Tauri 2 / React 18"}</span></div>
        <div className="row"><span>冻结基线</span><div className="spacer" /><span className="mono">{info?.frozen_baseline || "v1.0.0"}</span></div>
        <div className="actions"><button className="btn primary" onClick={onClose}>关闭</button></div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ alert banner */
export function AlertBanner({ tenantId = "LE-GROUP-HQ" }) {
  const [alerts, setAlerts] = useState([]);
  const [dismissed, setDismissed] = useState(() => {
    try {
      const s = sessionStorage.getItem(`oa_dismissed_alerts_${tenantId}`) || localStorage.getItem(`oa_dismissed_alerts_${tenantId}`);
      return s ? JSON.parse(s) : [];
    } catch {
      return [];
    }
  });

  const load = useCallback(async () => {
    try {
      const r = await api.alerts();
      setAlerts(r.alerts || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const activeAlerts = alerts.filter(
    (a) => !a.acked && !dismissed.includes(a.id) && !dismissed.includes(a.message) && !dismissed.includes(a.kind)
  );

  if (!activeAlerts.length) return null;
  const a = activeAlerts[0];

  const handleDismiss = async () => {
    const next = [...dismissed, a.id, a.message, a.kind, 'budget_low'].filter(Boolean);
    setDismissed(next);
    try {
      sessionStorage.setItem("oa_dismissed_alerts_" + tenantId, JSON.stringify(next));
      localStorage.setItem("oa_dismissed_alerts_" + tenantId, JSON.stringify(next));
    } catch { /* ignore */ }
    setAlerts((prev) => prev.filter((item) => item.id !== a.id));
    try {
      await api.ackAlert(a.id);
    } catch { /* ignore */ }
    load();
  };

  return (
    <div className={cls("banner", a.level === "critical" ? "bad" : "warn")}>
      <span>⚠️ {a.message}</span>
      <div className="spacer" />
      <button className="btn ghost" onClick={handleDismiss}>知道了</button>
    </div>
  );
}

/* ------------------------------------------------------------------ context menu */
export function ContextMenu({ ctx }) {
  if (!ctx) return null;
  return (
    <div className="ctx-menu" style={{ left: ctx.x, top: ctx.y }}
         onClick={(e) => e.stopPropagation()}>
      {ctx.items.map((it, i) => it.sep ? <div key={i} className="ctx-sep" /> : (
        <div key={i} className={cls("ctx-item", it.danger && "danger")}
             onClick={() => { it.onClick(); }}>
          {it.label}
        </div>
      ))}
    </div>
  );
}
