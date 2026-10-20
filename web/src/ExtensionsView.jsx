import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

const emptyServer = { name: "", url: "", api_key: "" };
const emptySmtp = { host: "", port: 587, user: "", password: "", from_addr: "", secure: false };

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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const load = useCallback(async () => {
    const [tools, staff, mail] = await Promise.all([api.mcpServers(), api.employees(), api.smtp()]);
    setServers(tools.servers || []);
    setEmployees(staff.employees || []);
    setEmployee(current => current || staff.employees?.[0]?.id || "");
    setSmtp({ ...emptySmtp, ...mail, password: "" });
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
          <div className="hint">{server.has_key ? "已设置访问凭据" : "未设置访问凭据"} · {server.tools?.length || 0} 个已发现工具</div>
          <div className="actions">
            <button className="btn ghost" disabled={busy} onClick={() => perform(() => api.testMcp(server.id), r => `连接成功，发现 ${r.tools?.length ?? r.tools ?? 0} 个工具。`)}>测试连接</button>
            <button className="btn ghost" disabled={busy} onClick={() => { setEditing(server.id); setForm({ name: server.name, url: server.url, api_key: "" }); }}>编辑</button>
            <button className="btn ghost" disabled={busy} onClick={() => perform(() => api.patchMcp(server.id, { enabled: !server.enabled }), server.enabled ? "连接已停用。" : "连接已启用。")}>{server.enabled ? "停用" : "启用"}</button>
            <button className="btn danger" disabled={busy} onClick={() => perform(() => api.deleteMcp(server.id), "连接已移除。")}>移除</button>
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
