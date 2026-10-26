import React, { useState, useEffect, useCallback } from "react";
import api from "../api.js";
import { initials, cls, Icon } from "./common.jsx";

/* --------------------------------------------------- 通用递归树（v2.3） */
export const treeKey = (n) => n.__k || n.id || n.key;
export const treeChildren = (childrenOf, n) => childrenOf(n) || [];

export function collectKeys(nodes, childrenOf, depth = 1, maxDepth = 99, out = []) {
  for (const n of nodes || []) {
    if (depth <= maxDepth) out.push(treeKey(n));
    const kids = treeChildren(childrenOf, n);
    if (kids.length && depth < maxDepth) {
      collectKeys(kids, childrenOf, depth + 1, maxDepth, out);
    }
  }
  return out;
}

export function TreeNode({ node, depth, expanded, onToggle, childrenOf, renderRow, childrenClassName }) {
  const kids = treeChildren(childrenOf, node);
  const key = treeKey(node);
  const open = expanded.has(key);
  return (
    <div className="rtree-node">
      <div className={cls("rtree-row", kids.length && "has-kids")}
           style={{ paddingLeft: 6 + depth * 14 }}
           onClick={() => kids.length && onToggle(key)}>
        <span className={cls("rtree-caret", !kids.length && "leaf")}>
          {kids.length ? (open ? "▾" : "▸") : "·"}
        </span>
        <div className="rtree-body">
          {renderRow(node, { depth, open, hasKids: kids.length, toggle: () => onToggle(key) })}
        </div>
      </div>
      {kids.length > 0 && open && (
        <div className={cls("rtree-children", childrenClassName)}>
          {kids.map((k) => (
            <TreeNode key={treeKey(k)} node={k} depth={depth + 1} expanded={expanded}
                      onToggle={onToggle} childrenOf={childrenOf} renderRow={renderRow}
                      childrenClassName={childrenClassName} />
          ))}
        </div>
      )}
    </div>
  );
}

export function RecursiveTree({ roots, childrenOf, renderRow, defaultExpandDepth = 2,
                         childrenClassName, toolbar = true, empty = "暂无数据" }) {
  const [expanded, setExpanded] = useState(
    () => new Set(collectKeys(roots, childrenOf, 1, defaultExpandDepth)));
  const signature = (roots || []).map(treeKey).join("|");
  useEffect(() => { setExpanded(new Set(collectKeys(roots, childrenOf, 1, defaultExpandDepth))); },
            [signature, defaultExpandDepth]);
  const toggle = (key) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  return (
    <div className="rtree">
      {toolbar && (
        <div className="rtree-tools">
          <button className="btn ghost" onClick={() =>
            setExpanded(new Set(collectKeys(roots, childrenOf, 1, 99)))}>全部展开</button>
          <button className="btn ghost" onClick={() => setExpanded(new Set())}>全部收起</button>
        </div>
      )}
      {(roots || []).length === 0 && <div className="hint" style={{ padding: 10 }}>{empty}</div>}
      {(roots || []).map((n) => (
        <TreeNode key={treeKey(n)} node={n} depth={0} expanded={expanded} onToggle={toggle}
                  childrenOf={childrenOf} renderRow={renderRow}
                  childrenClassName={childrenClassName} />
      ))}
    </div>
  );
}

export function orgChildren(n) {
  if (n.t === "emp") return [];
  if (n.t === "pos") return (n.employees || []).map((e) => ({ ...e, t: "emp", __k: "emp:" + e.id }));
  return [
    ...(n.children || []).map((c) => ({ ...c, t: "entity", __k: "ent:" + c.id })),
    ...(n.positions || []).map((p) => ({ ...p, t: "pos", __k: "pos:" + p.id })),
  ];
}

export const ENTITY_TYPE = { group_hq: "集团总部", subsidiary: "子公司", department: "部门" };

export function OrgTree({ org, onPick }) {
  const roots = (org?.roots || []).map((e) => ({ ...e, t: "entity", __k: "ent:" + e.id }));
  return (
    <RecursiveTree roots={roots} childrenOf={orgChildren} defaultExpandDepth={2}
      empty="还没有组织节点"
      renderRow={(n) => {
        if (n.t === "emp") {
          return (
            <div className="rtree-emp" onClick={(e) => { e.stopPropagation(); onPick(n); }}>
              <div className="avatar">{initials(n.name)}</div>
              <div className="rtree-emp-meta">
                <div>{n.name}</div>
                <div className="role">{n.title} · {n.model}</div>
              </div>
              <span className="spacer" />
              <span className={cls("dot", (n.status || "online") === "fired" && "off")} />
            </div>
          );
        }
        if (n.t === "pos") {
          return <span className="rtree-pos">🗂 {n.title} <span className="hint">{(n.employees || []).length} 人</span></span>;
        }
        return (
          <>
            <span className="rtree-title">{n.name}</span>
            {ENTITY_TYPE[n.type] !== n.name && (
              <span className="tag">{ENTITY_TYPE[n.type] || n.type || "实体"}</span>
            )}
            <span className="spacer" />
            <span className="hint">{(n.children || []).length ? `${n.children.length} 下级 · ` : ""}{(n.positions || []).length} 岗位</span>
          </>
        );
      }} />
  );
}

export function OrgView({ org, onPick, departments = [], onRenameDept, onDeleteDept }) {
  let people = 0, positions = 0, entities = 0;
  const walk = (xs) => (xs || []).forEach((e) => {
    entities += 1;
    positions += (e.positions || []).length;
    (e.positions || []).forEach((p) => { people += (p.employees || []).length; });
    walk(e.children);
  });
  walk(org?.roots);
  return (
    <div className="main">
      <div className="topbar">
        <div className="avatar">组</div>
        <div>
          <div className="who">组织架构</div>
          <div className="sub">{entities} 个实体 · {positions} 个岗位 · {people} 名 AI 员工 · 可逐级折叠</div>
        </div>
        <div className="spacer" />
        <span className="hint">点行展开/收起 · 点员工看详情</span>
      </div>
      <div className="stream">
        <div className="stream-inner">
          <details className="department-management">
            <summary>{onRenameDept ? "管理部门" : "部门列表"} <span className="count-label">{departments.length}</span></summary>
            {departments.map((department) => <div className="department-management-row" key={department.id}><b>{department.name}</b><span>{department.headcount} 人</span>{onRenameDept && <button className="btn ghost" onClick={() => onRenameDept(department)}>重命名</button>}{onDeleteDept && department.type !== "group_hq" && <button className="btn ghost danger" onClick={() => onDeleteDept(department)}>删除部门</button>}</div>)}
          </details>
          <div className="org-card">
            <OrgTree org={org} onPick={onPick} />
          </div>
        </div>
      </div>
    </div>
  );
}

export function AddMember({ org, onClose, onDone }) {
  const entities = [];
  const walk = (ns) => ns.forEach((n) => { entities.push({ id: n.id, name: n.name }); walk(n.children || []); });
  walk(org?.roots || []);

  const [form, setForm] = useState({
    name: "", title: "AI 员工", persona: "", model: "deepseek-chat",
    entity_id: entities[0]?.id || "LE-GROUP-HQ", api_key: "", base_url: "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit() {
    setBusy(true); setErr("");
    try {
      await api.addMember(form);
      onDone();
      onClose();
    } catch (e) {
      setErr(e.message);
    } finally { setBusy(false); }
  }

  return (
    <div className="mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>+ 添加成员</h3>
        <div className="grid2">
          <div className="field"><label>姓名</label><input value={form.name} onChange={set("name")} placeholder="小测" /></div>
          <div className="field"><label>职位</label><input value={form.title} onChange={set("title")} placeholder="测试工程师" /></div>
        </div>
        <div className="field">
          <label>人设（System Prompt）</label>
          <textarea rows={4} value={form.persona} onChange={set("persona")}
            placeholder="你是一名严格的测试工程师，只按判据验收，发现问题直接指出。" />
        </div>
        <div className="grid2">
          <div className="field">
            <label>模型</label>
            <select value={form.model} onChange={set("model")}>
              <option value="deepseek-chat">DeepSeek · chat</option>
              <option value="deepseek-reasoner">DeepSeek · reasoner</option>
              <option value="gpt-4o">OpenAI · gpt-4o</option>
              <option value="claude-sonnet-4">Claude · sonnet</option>
            </select>
          </div>
          <div className="field">
            <label>所属实体</label>
            <select value={form.entity_id} onChange={set("entity_id")}>
              {entities.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </select>
          </div>
        </div>
        <div className="grid2">
          <div className="field"><label>API Key（可选）</label><input value={form.api_key} onChange={set("api_key")} placeholder="sk-…" /></div>
          <div className="field"><label>Base URL（可选）</label><input value={form.base_url} onChange={set("base_url")} placeholder="https://api.deepseek.com/v1" /></div>
        </div>
        {err && <div className="err">{err}</div>}
        <div className="actions">
          <button className="btn primary" onClick={submit} disabled={busy || !form.name}>保存并上线</button>
          <button className="btn ghost" onClick={onClose}>取消</button>
        </div>
      </div>
    </div>
  );
}

export function NewGroup({ onClose, onDone }) {
  const [emps, setEmps] = useState([]);
  const [picked, setPicked] = useState([]);
  const [name, setName] = useState("协作群");
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.employees().then((r) => setEmps(r.employees)); }, []);
  const toggle = (id) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  async function submit() {
    setBusy(true);
    try {
      const r = await api.createGroup(name, picked);
      onDone(r.chat_id);
      onClose();
    } finally { setBusy(false); }
  }

  return (
    <div className="mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>+ 创建群聊</h3>
        <div className="field"><label>群名称</label>
          <input value={name} onChange={(e) => setName(e.target.value)} /></div>
        <div className="field">
          <label>选择成员（第一个是 PM，负责拆解与分派）</label>
          {emps.filter((e) => (e.status || "online") !== "fired").map((e) => (
            <label key={e.id} className="pick">
              <input type="checkbox" checked={picked.includes(e.id)}
                     onChange={() => toggle(e.id)} />
              <span className="avatar">{initials(e.name)}</span>
              <span>{e.name}</span>
              <span className="role">{e.title} · {e.model}</span>
            </label>
          ))}
        </div>
        <div className="actions">
          <button className="btn primary" onClick={submit} disabled={busy || !picked.length}>
            创建群聊
          </button>
          <button className="btn ghost" onClick={onClose}>取消</button>
        </div>
      </div>
    </div>
  );
}

export function EmployeePanel({ emp, onClose, onChanged, onOpenChat }) {
  const [form, setForm] = useState({
    name: emp.name || "", soul: emp.soul || "", model: emp.model || "deepseek-chat",
    api_key: "", api_base: "",
  });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [askAuth, setAskAuth] = useState(false);
  const [authText, setAuthText] = useState("");
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function save() {
    setBusy(true); setNote("");
    try {
      await api.editEmployee(emp.id, {
        name: form.name, soul: form.soul, model: form.model,
        api_key: form.api_key || undefined, api_base: form.api_base || undefined,
      });
      setNote("已成功保存！");
      onChanged && onChanged();
    } catch (e) { setNote(e.message); } finally { setBusy(false); }
  }

  async function fire() {
    if (!confirm(`确认解雇 ${emp.name}？将软删除并释放其预算。`)) return;
    setBusy(true);
    try {
      const r = await api.fireEmployee(emp.id);
      setNote(`已解雇；释放预算 ${r.budgets_released} 项，停止运行 ${r.runs_stopped} 个`);
      onChanged && onChanged();
    } catch (e) { setNote(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="panel">
      <div className="panel-head">
        <div className="avatar">{initials(emp.name)}</div>
        <div>
          <div className="who">{emp.name}</div>
          <div className="sub">{emp.title} · {emp.id}</div>
        </div>
        <div className="spacer" />
        <button className="icon-btn" onClick={onClose}>×</button>
      </div>

      <div className="field"><label>姓名</label><input value={form.name} onChange={set("name")} /></div>
      <div className="field"><label>人设（System Prompt）</label>
        <textarea rows={5} value={form.soul} onChange={set("soul")} placeholder="你是一名…" /></div>
      <div className="row">
        <span>运行预设</span>
        <span className={cls("pill", emp.preset === "cordis" ? "warn" : "")}>
          {emp.preset || "standard"}
        </span>
        <div className="spacer" />
        <button className="btn" onClick={async () => {
          if (emp.preset === "cordis") {
            await api.editEmployee(emp.id, { preset: "standard" });
            onChanged && onChanged();
          } else {
            setAskAuth(true);
          }
        }}>
          {emp.preset === "cordis" ? "关闭创造模式" : "开启创造模式"}
        </button>
      </div>
      <div className="grid2">
        <div className="field"><label>模型</label>
          <select value={form.model} onChange={set("model")}>
            <option value="deepseek-chat">DeepSeek · chat</option>
            <option value="deepseek-reasoner">DeepSeek · reasoner</option>
            <option value="gpt-4o">OpenAI · gpt-4o</option>
            <option value="claude-sonnet-4">Claude · sonnet</option>
          </select>
        </div>
        <div className="field"><label>状态</label>
          <input value={emp.status || "online"} readOnly /></div>
      </div>
      <div className="grid2">
        <div className="field"><label>API Key（留空不改）</label>
          <input value={form.api_key} onChange={set("api_key")} placeholder="sk-…" /></div>
        <div className="field"><label>Base URL</label>
          <input value={form.api_base} onChange={set("api_base")} placeholder="https://api.deepseek.com/v1" /></div>
      </div>
      {note && <div className="hint">{note}</div>}
      {askAuth && (
        <div className="authbox">
          <div className="warn-title">⚠️ 高危操作</div>
          <div className="hint">
            开启后，该 Agent 将拥有本机完全访问权（任意写文件、装依赖、联网），且不经审批。
            请输入「确认授权」四个字继续。
          </div>
          <input value={authText} onChange={(e) => setAuthText(e.target.value)}
                 placeholder="确认授权" />
          <div className="actions">
            <button className="btn danger" disabled={authText !== "确认授权" || busy}
                    onClick={async () => {
                      await api.editEmployee(emp.id, { preset: "cordis" });
                      setAskAuth(false); setAuthText("");
                      onChanged && onChanged();
                    }}>确认开启全权限</button>
            <button className="btn ghost" onClick={() => { setAskAuth(false); setAuthText(""); }}>取消</button>
          </div>
        </div>
      )}
      <div className="actions">
        <button className="btn primary" disabled={busy} onClick={save}>保存</button>
        <button className="btn" disabled={busy} onClick={() => onOpenChat && onOpenChat(emp)}>打开对话</button>
        <button className="btn danger" disabled={busy} onClick={fire}>停用 / 解雇</button>
      </div>
    </div>
  );
}

export const TAG_LABEL = { dev: "开发", design: "设计", content: "文案", data: "数据",
                    qa: "测试", translate: "翻译" };

export const INDUSTRIES = ["全部", "研发", "金融", "市场", "设计", "法务", "运营", "通用"];

export function RolesView({ onChanged }) {
  const [rows, setRows] = useState([]);
  const [ind, setInd] = useState("全部");
  const [edit, setEdit] = useState(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", tag: "dev", prompt: "", model: "deepseek-chat", skills: "" });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    try { setRows((await api.roles()).roles); } catch { /* ignore */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function submit() {
    if (!form.name.trim()) return;
    setBusy(true); setNote("");
    try {
      const r = await api.createRole({ ...form });
      setNote(`已创建 ${r.role.name} → 员工 ${r.employee_id || "（未实例化）"}`);
      setForm({ name: "", tag: "dev", prompt: "", model: "deepseek-chat", skills: "" });
      setOpen(false);
      await load();
      onChanged && onChanged();
    } catch (e) {
      setNote(e.message || "创建失败");
    } finally { setBusy(false); }
  }

  return (
    <div className="roles">
      <div className="roles-head">
        <span>共 {rows.length} 个角色</span>
        <span className="spacer" />
        <button className="btn" onClick={() => setOpen((v) => !v)}>
          <Icon name="plus" /> 新建角色
        </button>
      </div>

      {open && (
        <div className="role-form">
          <div className="grid2">
            <div className="field"><label>角色名称</label>
              <input value={form.name} placeholder="Python极客"
                     onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
            <div className="field"><label>标签（决定被谁调度）</label>
              <select value={form.tag} onChange={(e) => setForm({ ...form, tag: e.target.value })}>
                {Object.entries(TAG_LABEL).map(([k, v]) => <option key={k} value={k}>{v}（{k}）</option>)}
              </select></div>
          </div>
          <div className="field"><label>专属 System Prompt</label>
            <textarea rows={3} value={form.prompt}
                      placeholder="你是资深 Python 工程师……"
                      onChange={(e) => setForm({ ...form, prompt: e.target.value })} /></div>
          <div className="grid2">
            <div className="field"><label>默认模型</label>
              <input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} /></div>
            <div className="field"><label>技能 / 权限（逗号分隔）</label>
              <input value={form.skills} placeholder="写代码,调试,重构"
                     onChange={(e) => setForm({ ...form, skills: e.target.value })} /></div>
          </div>
          <div className="actions">
            <button className="btn primary" disabled={busy || !form.name.trim()} onClick={submit}>创建并加入组织</button>
            <button className="btn ghost" onClick={() => setOpen(false)}>取消</button>
          </div>
        </div>
      )}
      {note && <div className="hint">{note}</div>}

      <div className="tag-pills">
        {INDUSTRIES.map((x) => (
          <button key={x} className={cls(ind === x && "on")} onClick={() => setInd(x)}>{x}</button>
        ))}
      </div>
      <div className="role-list">
        {rows.filter((r) => ind === "全部" || (r.industry || "通用") === ind).map((r) => (
          <div className="role-card" key={r.id} onClick={() => setEdit(r)}
               onContextMenu={(e) => {
                 e.preventDefault();
                 window.dispatchEvent(new CustomEvent("oa-ctx", { detail: {
                   x: e.clientX, y: e.clientY, items: [
                     { label: "重命名", onClick: async () => {
                         const n = window.prompt("角色名称", r.name);
                         if (n && n !== r.name) { await api.patchRole(r.id, { name: n }); load(); }
                       } },
                     { sep: true },
                     { label: "删除角色", danger: true, onClick: async () => {
                         if (!window.confirm(`删除角色「${r.name}」？已实例化的员工会保留。`)) return;
                         await api.deleteRole(r.id); load();
                       } },
                   ] } }));
               }}>
            <div className="role-top">
              <b>{r.name}</b>
              <span className="pill">{TAG_LABEL[r.tag] || r.tag}</span>
              <span className="spacer" />
              <span className="hint">{r.headcount || 0} 人</span>
              <button
                className="icon-btn"
                title="删除角色"
                style={{ width: 22, height: 22, padding: 0, opacity: 0.6, marginLeft: 6 }}
                onClick={async (e) => {
                  e.stopPropagation();
                  if (!window.confirm(`确定删除角色「${r.name}」？已实例化的员工会保留。`)) return;
                  try {
                    await api.deleteRole(r.id);
                    await load();
                    onChanged && onChanged();
                  } catch (err) {
                    alert(err.message || "删除失败");
                  }
                }}
              >
                ✕
              </button>
            </div>
            <div className="role-prompt">{r.prompt || "（未设置提示词）"}</div>
            <div className="role-foot">
              <span className="kv-mini">模型 {r.model}</span>
              {r.skills && <span className="kv-mini">技能 {r.skills}</span>}
            </div>
          </div>
        ))}
        {rows.length === 0 && <div className="empty">还没有角色</div>}
      </div>
      {edit && <RoleDrawer role={edit} onClose={() => setEdit(null)}
                           onSaved={async () => { setEdit(null); await load(); onChanged && onChanged(); }} />}
    </div>
  );
}

export function RoleDrawer({ role, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: role.name, prompt: role.prompt || "", model: role.model || "deepseek-chat",
    tools: (role.tools || "").split(",").filter(Boolean), deliverable: role.deliverable || "",
  });
  const [busy, setBusy] = useState(false);
  const ALL_TOOLS = ["code_exec", "file_write", "db_query", "web_fetch"];
  const toggle = (t) => setForm((f) => ({
    ...f, tools: f.tools.includes(t) ? f.tools.filter((x) => x !== t) : [...f.tools, t],
  }));

  async function save() {
    setBusy(true);
    try {
      await api.patchRole(role.id, { ...form, tools: form.tools.join(",") });
      await onSaved();
    } finally { setBusy(false); }
  }

  return (
    <>
      <div className="drawer-mask" onClick={onClose} />
      <div className="drawer">
        <h3>{role.name} · 编辑</h3>
        <div className="field"><label>角色名称</label>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div className="field"><label>System Prompt（下次派活立即生效）</label>
          <textarea rows={7} value={form.prompt}
                    onChange={(e) => setForm({ ...form, prompt: e.target.value })} /></div>
        <div className="field"><label>绑定模型</label>
          <select value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })}>
            <option value="deepseek-chat">DeepSeek chat</option>
            <option value="deepseek-reasoner">DeepSeek reasoner</option>
            <option value="gpt-4o">OpenAI gpt-4o</option>
            <option value="claude-sonnet-4">Claude sonnet</option>
          </select></div>
        <div className="field"><label>技能 / 工具（执行期拦截）</label>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {ALL_TOOLS.map((t) => (
              <label key={t} className="pick" style={{ padding: "4px 8px" }}>
                <input type="checkbox" checked={form.tools.includes(t)} onChange={() => toggle(t)} />
                <span>{t}</span>
              </label>
            ))}
          </div></div>
        <div className="field"><label>默认交付物</label>
          <input value={form.deliverable}
                 onChange={(e) => setForm({ ...form, deliverable: e.target.value })} /></div>
        <div className="actions" style={{ display: "flex", justifyContent: "space-between" }}>
          <button className="btn danger" disabled={busy} onClick={async () => {
            if (!window.confirm(`确定删除角色「${role.name}」？已实例化的员工会保留。`)) return;
            try {
              await api.deleteRole(role.id);
              onClose();
              await onSaved();
            } catch (err) {
              alert(err.message || "删除角色失败");
            }
          }}>删除角色</button>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn ghost" onClick={onClose}>取消</button>
            <button className="btn primary" disabled={busy} onClick={save}>保存（即改即生效）</button>
          </div>
        </div>
      </div>
    </>
  );
}

export function NewDept({ onClose, onDone }) {
  const [name, setName] = useState("");
  return (
    <div className="mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>新建部门</h3>
        <div className="field"><label>部门名称</label>
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="如：增长部" /></div>
        <div className="actions">
          <button className="btn primary" disabled={!name.trim()}
                  onClick={async () => { await api.createDept(name.trim()); onDone(); onClose(); }}>创建</button>
          <button className="btn ghost" onClick={onClose}>取消</button>
        </div>
      </div>
    </div>
  );
}

export default OrgView;
