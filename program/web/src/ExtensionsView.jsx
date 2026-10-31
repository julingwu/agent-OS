import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

const emptyServer = { name: "", url: "", api_key: "" };
const emptySmtp = { host: "", port: 587, user: "", password: "", from_addr: "", secure: false };
const emptyRobot = { webhook: "", enabled: true, on_project_done: true, on_redline: true,
  webhook_set: false, webhook_hint: "", source: "none", mock: false };

export default function ExtensionsView() {
  const [servers, setServers] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [employee, setEmployee] = useState("");
  const [bindings, setBindings] = useState([]);
  const [bindingsLoading, setBindingsLoading] = useState(false);
  const [bindingsError, setBindingsError] = useState("");
  const [loadedEmployee, setLoadedEmployee] = useState("");
  const [bindingsRefresh, setBindingsRefresh] = useState(0);
  const [form, setForm] = useState(emptyServer);
  const [editing, setEditing] = useState(null);
  const [smtp, setSmtp] = useState(emptySmtp);
  const [recipient, setRecipient] = useState("");
  const [robotCfg, setRobotCfg] = useState(emptyRobot);
  const [notices, setNotices] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [confirmDeleteServerId, setConfirmDeleteServerId] = useState("");
  const [expandedServerTools, setExpandedServerTools] = useState(new Set());
  const [testingServerId, setTestingServerId] = useState("");

  function toggleServerTools(id) {
    const next = new Set(expandedServerTools);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setExpandedServerTools(next);
  }
  const load = useCallback(async () => {
    const [tools, staff, mail, robot, ledger] = await Promise.all([
      api.mcpServers(), api.employees(), api.smtp(), api.wecomRobot(), api.notifications()]);
    setServers(tools.servers || []);
    setEmployees(staff.employees || []);
    setEmployee(current => current || staff.employees?.[0]?.id || "");
    setSmtp({ ...emptySmtp, ...mail, password: "" });
    setRobotCfg({ ...emptyRobot, ...robot, webhook: "" });
    setNotices(ledger.notifications || []);
  }, []);
  useEffect(() => { load().catch(e => setError(e.message)); }, [load]);
  useEffect(() => {
    let live = true;
    setBindings([]);
    setLoadedEmployee("");
    setBindingsError("");
    setBindingsLoading(Boolean(employee));
    if (employee) api.mcpBindings(employee).then(r => {
      if (!live) return;
      if (!Array.isArray(r.server_ids)) throw new Error("员工授权数据不完整，请重新读取。");
      setBindings(r.server_ids);
      setLoadedEmployee(employee);
    }).catch(e => {
      if (live) setBindingsError(e.message);
    }).finally(() => {
      if (live) setBindingsLoading(false);
    });
    return () => { live = false; };
  }, [employee, bindingsRefresh]);
  const bindingsReady = Boolean(employee) && loadedEmployee === employee && !bindingsLoading && !bindingsError;
  async function perform(fn, success, reload = true) {
    setBusy(true); setError(""); setNote("");
    try {
      const result = await fn();
      if (result?.ok === false) throw new Error(result.error || "操作未完成，请检查配置。");
      if (reload) await load();
      setNote(typeof success === "function" ? success(result) : success);
      return true;
    } catch (e) { setError(e.message); return false; }
    finally { setBusy(false); }
  }
  async function saveServer(event) {
    event.preventDefault();
    const body = { name: form.name, url: form.url };
    if (form.api_key || !editing) body.api_key = form.api_key;
    const ok = await perform(() => editing ? api.patchMcp(editing, body) : api.addMcp(body), "工具连接已保存。");
    if (ok) { setForm(emptyServer); setEditing(null); }
  }
  async function saveBindings() {
    if (busy || !bindingsReady) return;
    const targetEmployee = employee;
    const serverIds = [...bindings];
    await perform(() => api.bindMcp(targetEmployee, serverIds), "员工工具授权已保存。", false);
  }
  async function saveRobot(event) {
    event.preventDefault();
    if (busy) return;
    const body = { enabled: robotCfg.enabled, on_project_done: robotCfg.on_project_done,
      on_redline: robotCfg.on_redline };
    if (robotCfg.webhook.trim()) body.webhook = robotCfg.webhook.trim();
    const ok = await perform(() => api.saveWecomRobot(body), "通知设置已保存。");
    if (ok) setRobotCfg(current => ({ ...current, webhook: "" }));
  }
  return <div className="main"><div className="topbar"><div>
    <div className="who">工具与集成</div><div className="sub">为 AI 员工配置可信工具，统一管理邮件服务</div>
  </div><span className="spacer" /><button className="btn ghost" disabled={busy} onClick={() => perform(load, "已刷新。", false)}>刷新</button></div>
    <div className="stream"><div className="stream-inner integration-page">
      {error && <div className="integration-alert" role="alert">{error}</div>}
      {note && <div className="integration-notice" role="status">{note}</div>}
      <section className="fin-card"><div className="integration-heading"><div><h3>外部工具</h3>
        <p className="hint">只有管理员可以管理连接。绑定后，员工可在任务中使用该服务提供的工具。</p></div>
        <span className="pill">{servers.length} 个连接</span></div>
        <div className="integration-list">{servers.map(server => <article className="integration-item" key={server.id}>
          <div className="integration-heading"><strong>{server.name}</strong><span className={`pill ${server.enabled ? "ok" : ""}`}>{server.enabled ? "已启用" : "已停用"}</span></div>
          <p className="integration-url">{server.url}</p>
          <div className="hint" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px" }}>
            <span>{server.has_key ? "已设置访问凭据" : "未设置访问凭据"} · {server.tools?.length || 0} 个已发现工具</span>
            {Boolean(server.tools?.length) && (
              <button type="button" className="btn ghost" style={{ height: "22px", padding: "1px 8px", fontSize: "11px" }}
                      onClick={() => toggleServerTools(server.id)}>
                {expandedServerTools.has(server.id) ? "收起工具清单" : `查看工具清单 (${server.tools.length})`}
              </button>
            )}
          </div>
          {expandedServerTools.has(server.id) && Boolean(server.tools?.length) && (
            <div style={{ marginTop: "10px", padding: "10px", background: "var(--panel)", borderRadius: "8px", border: "1px solid var(--border)" }}>
              <div style={{ fontSize: "11px", fontWeight: "600", marginBottom: "6px", color: "var(--text)" }}>已发现工具能力清单：</div>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {server.tools.map((t, idx) => (
                  <div key={t.name || idx} style={{ padding: "6px 8px", background: "var(--bg)", borderRadius: "6px", border: "1px solid var(--border)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <code style={{ fontSize: "12px", fontWeight: "600", color: "var(--accent)" }}>{t.name}</code>
                    </div>
                    {t.description && <div style={{ fontSize: "11px", color: "var(--muted)", marginTop: "2px" }}>{t.description}</div>}
                    {t.inputSchema?.properties && Object.keys(t.inputSchema.properties).length > 0 && (
                      <div style={{ fontSize: "10px", color: "var(--muted)", marginTop: "4px", display: "flex", flexWrap: "wrap", gap: "4px", alignItems: "center" }}>
                        <span>入参:</span>
                        {Object.keys(t.inputSchema.properties).map(k => (
                          <span key={k} className="pill" style={{ fontSize: "10px", padding: "1px 5px" }}>{k}</span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="actions">
            <button className="btn ghost" disabled={busy || testingServerId === server.id}
                    onClick={async () => {
                      setTestingServerId(server.id);
                      await perform(() => api.testMcp(server.id), r => `连接成功，发现 ${r.tools?.length ?? r.tools ?? 0} 个工具。`);
                      setTestingServerId("");
                    }}>
              {testingServerId === server.id ? "测试中…" : "测试连接"}
            </button>
            <button className="btn ghost" disabled={busy} onClick={() => { setEditing(server.id); setForm({ name: server.name, url: server.url, api_key: "" }); }}>编辑</button>
            <button className="btn ghost" disabled={busy} onClick={() => perform(() => api.patchMcp(server.id, { enabled: !server.enabled }), server.enabled ? "连接已停用。" : "连接已启用。")}>{server.enabled ? "停用" : "启用"}</button>
            {confirmDeleteServerId === server.id ? (
              <>
                <button className="btn danger" disabled={busy} onClick={async () => {
                  setConfirmDeleteServerId("");
                  await perform(() => api.deleteMcp(server.id), "连接已移除。");
                }}>确认移除？</button>
                <button className="btn ghost" disabled={busy} onClick={() => setConfirmDeleteServerId("")}>取消</button>
              </>
            ) : (
              <button className="btn danger" disabled={busy} onClick={() => setConfirmDeleteServerId(server.id)}>移除</button>
            )}
          </div>
        </article>)}</div>
        {!servers.length && <p className="hint">尚未添加工具。填写服务地址后，再选择使用它的员工。</p>}
        <form onSubmit={saveServer} className="integration-form">
          <h4>{editing ? "编辑连接" : "添加工具连接"}</h4>
          <div className="grid2">
            <label className="field">连接名称<input required maxLength={80} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
            <label className="field">服务地址<input required type="url" placeholder="https://tools.example.com/mcp" value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} /></label>
          </div>
          <label className="field">访问密钥<input type="password" autoComplete="new-password" value={form.api_key} onChange={e => setForm({ ...form, api_key: e.target.value })} placeholder={editing ? "留空保留；更换地址时会清除原密钥" : "可选，仅用于连接此服务"} /></label>
          <div className="actions"><button className="btn primary" disabled={busy}>{editing ? "保存连接" : "添加连接"}</button>
            {editing && <button type="button" className="btn ghost" onClick={() => { setEditing(null); setForm(emptyServer); }}>取消编辑</button>}</div>
        </form>
      </section>
      <section className="fin-card"><h3>员工工具授权</h3><p className="hint">只给员工分配完成任务所需的连接。停用或修改授权后，旧会话需要重新检查。</p>
        <label className="field">选择员工<select value={employee} disabled={busy} onChange={e => setEmployee(e.target.value)}>
          <option value="">请选择员工</option>{employees.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
        </select></label>
        {bindingsLoading && <p className="hint" role="status">正在读取员工授权…</p>}
        {bindingsError && <div className="integration-alert" role="alert">{bindingsError}
          <button className="btn ghost" disabled={busy} onClick={() => setBindingsRefresh(current => current + 1)}>重试读取授权</button>
        </div>}
        <div className="integration-checks">{servers.map(s => <label key={s.id}>
          <input type="checkbox" checked={bindings.includes(s.id)} disabled={busy || !bindingsReady} onChange={e => setBindings(e.target.checked ? [...bindings, s.id] : bindings.filter(id => id !== s.id))} />
          {s.name}{!s.enabled && "（已停用）"}</label>)}</div>
        <button className="btn primary" disabled={busy || !bindingsReady} onClick={saveBindings}>保存员工授权</button>
      </section>
      <section className="fin-card"><h3>企微群机器人</h3>
        <div className="integration-heading"><div>
          <p className="hint">9/9 商业闭环达成、或红线 L0 拦截时，自动把结论推送到企微群。群机器人只用一条 webhook，不依赖 corp 应用的审批通道；推送失败只记在下面的台账里，不影响任务与验收。</p>
        </div><span className={`pill ${robotCfg.webhook_set ? "ok" : ""}`}>{robotCfg.webhook_set ? `已配置 ${robotCfg.webhook_hint}` : "未配置"}</span></div>
        <form onSubmit={saveRobot}>
          <label className="field">Webhook 地址
            <input type="password" autoComplete="new-password" value={robotCfg.webhook}
              onChange={e => setRobotCfg({ ...robotCfg, webhook: e.target.value })}
              placeholder={robotCfg.webhook_set ? "已设置，留空保留" : "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…"} />
          </label>
          <div className="integration-checks">
            <label><input type="checkbox" checked={robotCfg.enabled} onChange={e => setRobotCfg({ ...robotCfg, enabled: e.target.checked })} />启用群推送</label>
            <label><input type="checkbox" checked={robotCfg.on_project_done} onChange={e => setRobotCfg({ ...robotCfg, on_project_done: e.target.checked })} />9/9 商业闭环</label>
            <label><input type="checkbox" checked={robotCfg.on_redline} onChange={e => setRobotCfg({ ...robotCfg, on_redline: e.target.checked })} />红线 L0 拦截</label>
          </div>
          <div className="actions">
            <button className="btn primary" disabled={busy}>保存通知设置</button>
            <button type="button" className="btn ghost" disabled={busy || !robotCfg.webhook_set}
              onClick={() => perform(() => api.testWecomRobot(),
                r => r.transport === "outbox" ? "离线模式：测试消息已写入本地 outbox。" : `测试消息已发送（${r.transport}）。`, false)}>发送测试</button>
          </div>
        </form>
        <h4 style={{ marginTop: 18 }}>最近通知</h4>
        {notices.length ? <table className="fin-table"><thead><tr><th>时间</th><th>事件</th><th>标题</th><th>结果</th></tr></thead>
          <tbody>{notices.map(n => <tr key={n.id}><td>{n.created_at}</td>
            <td>{n.event === "project_closed" ? "商业闭环" : n.event === "redline_blocked" ? "红线拦截" : n.event}</td>
            <td>{n.subject}</td>
            <td><span className={`pill ${n.state === "sent" ? "ok" : ""}`}>{n.state === "sent" ? "已推送" : n.state === "failed" ? "失败" : "待推送"}</span></td></tr>)}</tbody></table>
          : <p className="hint">还没有推送记录。9/9 闭环或红线拦截发生后会出现在这里。</p>}
      </section>
      <section className="fin-card"><h3>邮件服务</h3>
        <p className="hint">保存邮件服务器配置后，可主动发送一封测试邮件。AI 邮件工具目前只生成待发送草稿，任务重试不会自动发信。</p>
        <form onSubmit={e => { e.preventDefault(); const body = { host: smtp.host, port: smtp.port, user: smtp.user, from_addr: smtp.from_addr, secure: smtp.secure }; if (smtp.password) body.password = smtp.password; perform(() => api.saveSmtp(body), "邮件配置已保存。"); }}>
          <div className="grid2">
            <label className="field">SMTP 服务器<input value={smtp.host} onChange={e => setSmtp({ ...smtp, host: e.target.value })} /></label>
            <label className="field">端口<input type="number" min="1" max="65535" value={smtp.port} onChange={e => setSmtp({ ...smtp, port: Number(e.target.value) })} /></label>
            <label className="field">登录账号<input value={smtp.user} onChange={e => setSmtp({ ...smtp, user: e.target.value })} /></label>
            <label className="field">发件人地址<input type="email" value={smtp.from_addr} onChange={e => setSmtp({ ...smtp, from_addr: e.target.value })} /></label>
          </div>
          <label className="field">邮箱授权码<input type="password" autoComplete="new-password" placeholder={smtp.pass_set ? "已设置，留空保留" : "请输入邮箱授权码"} value={smtp.password} onChange={e => setSmtp({ ...smtp, password: e.target.value })} /></label>
          <label className="integration-checkbox"><input type="checkbox" checked={smtp.secure} onChange={e => setSmtp({ ...smtp, secure: e.target.checked })} />使用隐式 TLS（通常为 465 端口）</label>
          <button className="btn primary" disabled={busy}>保存邮件配置</button>
        </form>
        <div className="integration-form"><label className="field">测试收件人<input type="email" value={recipient} onChange={e => setRecipient(e.target.value)} placeholder="填写您自己的邮箱" /></label>
          <button className="btn" disabled={busy || !recipient || !smtp.host} onClick={() => perform(() => api.testSmtp(recipient), "测试邮件已交给邮件服务器。", false)}>发送测试邮件</button></div>
      </section>
    </div></div>
  </div>;
}
