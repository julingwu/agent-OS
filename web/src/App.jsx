import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { marked } from "marked";
import { api, ping } from "./api.js";
import "./styles.css";

// Model output is untrusted. marked drops raw HTML (renderer.html = "") and
// safeHtml() strips script/iframe/style + inline handlers as a second pass, so
// this dangerouslySetInnerHTML never receives markup we did not generate.
marked.use({ renderer: { html: () => "" } });
marked.setOptions({ breaks: true, gfm: true });

function safeHtml(text) {
  const raw = marked.parse(text || "");
  return raw
    .replace(/<(script|style|iframe|object|embed)[\s\S]*?<\/\1>/gi, "")
    .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "")
    .replace(/javascript:/gi, "");
}

const initials = (s = "") => (s || "AI").trim().slice(0, 2);
const cls = (...a) => a.filter(Boolean).join(" ");
function safeJson(s) { try { return s ? JSON.parse(s) : null; } catch { return null; } }

/* ------------------------------------------------------------------ login */
function Login({ onDone }) {
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
        <div className="sub">数字集团控制台 · 用你的身份密钥登录</div>
        <div className="field">
          <label>身份 ID</label>
          <input value={id} onChange={(e) => setId(e.target.value)} placeholder="ID-xxxxxxxxxxxx" autoFocus />
        </div>
        <div className="field">
          <label>密钥（HMAC secret）</label>
          <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="••••••••" />
        </div>
        <button className="btn primary" style={{ width: "100%" }} disabled={busy || !id || !secret}>
          {busy ? "登录中…" : "进入控制台"}
        </button>
        {waiting && <div className="hint" style={{ marginTop: 10 }}>
          若长时间无响应，说明本地 8077 服务还没起来（桌面版会自动拉起，浏览器版请先跑 start_all）。
        </div>}
        {err && <div className="err">{err}</div>}
      </form>
    </div>
  );
}

/* -------------------------------------------------------------- markdown */
function Markdown({ text }) {
  const html = useMemo(() => safeHtml(text), [text]);
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}


/* ------------------------------------------------------------- 图标（v1.3.1） */
const ICONS = {
  chat: "M21 12a8 8 0 0 1-8 8H7l-4 3v-7a8 8 0 0 1 8-8h2a8 8 0 0 1 8 4z",
  org: "M12 3v4M12 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM5 21v-3a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v3",
  files: "M4 5a2 2 0 0 1 2-2h5l2 3h5a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 4.6 15a2 2 0 1 1 0-4 1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 12 4.6a2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 19.4 11a2 2 0 1 1 0 4z",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9a2.5 2.5 0 1 1 3.6 2.3c-.7.4-1.1 1-1.1 1.7M12 17h.01",
  panel: "M4 5h16v14H4zM15 5v14",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  close: "M6 6l12 12M18 6L6 18",
  plus: "M12 5v14M5 12h14",
  play: "M6 4l14 8-14 8z",
  check: "M4 12l5 5L20 6",
  alert: "M12 8v5M12 16h.01M10.3 3.9 2.6 17a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  role: "M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4 21v-1a5 5 0 0 1 5-5h6a5 5 0 0 1 5 5v1M16 3.5l2 2 3-3",
  shield: "M12 21s7-3.5 7-9V6l-7-3-7 3v6c0 5.5 7 9 7 9zM9.5 12l1.8 1.8 3.4-3.6",
  wallet: "M3 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v2h2v6h-2v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM16 12h.01",
  flask: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3M7.5 15h9",
  brain: "M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0 0 6v1a3 3 0 0 0 5 2.2M15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 0 6v1a3 3 0 0 1-5 2.2M12 5v14",
};

function Icon({ name, size = "" }) {
  return (
    <svg className={cls("icon", size)} viewBox="0 0 24 24" aria-hidden="true">
      <path d={ICONS[name] || ICONS.chat} />
    </svg>
  );
}

function Ring({ pct, used, cap }) {
  const r = 26, c = 2 * Math.PI * r;
  const off = c * (1 - Math.min(1, Math.max(0, pct / 100)));
  return (
    <div className="ring-wrap">
      <svg className="ring" width="66" height="66" viewBox="0 0 66 66">
        <defs>
          <linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#5eead4" />
            <stop offset="100%" stopColor="#38bdf8" />
          </linearGradient>
        </defs>
        <circle className="track" cx="33" cy="33" r={r} />
        <circle className="val" cx="33" cy="33" r={r}
                strokeDasharray={c} strokeDashoffset={off} />
      </svg>
      <div>
        <div className="ring-num">{Math.round(pct)}%</div>
        <div className="ring-sub">{(used / 1000).toFixed(1)}k / {(cap / 1000).toFixed(0)}k</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- task card */
const STATE_LABEL = { passed: "通过", failed: "不过", pending: "待定", na: "未启用" };
const STATE_CLASS = { passed: "var(--ok)", failed: "var(--bad)", pending: "var(--warn)", na: "var(--muted)" };

function TaskCard({ taskId, meta, onAct }) {
  const [view, setView] = useState(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const v = await api.task(taskId);
        if (alive) setView(v);
      } catch { /* ignore */ }
    };
    load();
    const t = setInterval(load, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [taskId]);

  const criteria = view?.criteria || meta?.criteria || [];
  const run = view?.runs?.[0];
  const state = view?.task?.state || meta?.state;
  const stream = view?.stream || [];
  const thinking = stream.filter((s) => s.kind === "thinking").map((s) => s.text).join("");
  const tools = stream.filter((s) => s.kind === "tool");

  async function act(action) {
    setBusy(true);
    try {
      await api.action(taskId, action);
      const v = await api.task(taskId);
      setView(v);
      onAct && onAct();
    } finally { setBusy(false); }
  }

  const running = run && ["spawned", "running"].includes(run.state);

  return (
    <div className="card">
      <h4>
        任务 {taskId} · {state}
        {running && <span style={{ marginLeft: 8 }}><span className="spin" /> {run.usage_tokens || 0} tok</span>}
        {!running && run && <span style={{ marginLeft: 8, color: "var(--muted)" }}>
          {run.usage_tokens || 0} tokens
        </span>}
      </h4>

      {(thinking || tools.length > 0) && (
        <details className="think" open={running}>
          <summary>💭 思考与工具 {tools.length ? `· ${tools.length} 次调用` : ""}</summary>
          <div className="body">{thinking.slice(-2000) || "（暂无可读思考）"}</div>
        </details>
      )}
      {tools.slice(-4).map((t, i) => (
        <div className="tool" key={i}><span className="ico">🛠️</span>{t.text}</div>
      ))}

      {criteria.map((c) => (
        <div className="crit" key={c.id}>
          <span className="state" style={{ color: STATE_CLASS[c.state] }}>●</span>
          <span>
            <span>{c.criterion}</span>
            <span className="note"> · {STATE_LABEL[c.state] || c.state}{c.note ? `｜${c.note}` : ""}</span>
          </span>
        </div>
      ))}

      {meta?.artifact && (
        <div className="crit">
          <span className="state" style={{ color: "var(--accent)" }}>📄</span>
          <span className="txt">{meta.artifact.file_path?.split("\\").pop()} · {meta.artifact.bytes} B</span>
          {meta.artifact.id && PREVIEWABLE.includes(
            ("." + (meta.artifact.file_path || "").split(".").pop()).toLowerCase()) && (
            <button className="btn ghost" style={{ marginLeft: 8 }}
                    onClick={() => setPreview({
                      id: meta.artifact.id, name: (meta.artifact.file_path || "").split("\\").pop(),
                      bytes: meta.artifact.bytes, task_id: taskId,
                    })}>预览</button>
          )}
        </div>
      )}
      {preview && <PreviewModal file={preview} onClose={() => setPreview(null)} />}

      {!running && (
        <div className="actions">
          <button className="btn primary" disabled={busy} onClick={() => act("accept")}>验收通过</button>
          <button className="btn" disabled={busy} onClick={() => act("reject")}>驳回</button>
          <button className="btn ghost" disabled={busy} onClick={() => act("abort")}>中止</button>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ chat */
function ChatView({ chat, onSent, onMore, onHelp, onAbout }) {
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [askRole, setAskRole] = useState(false);
  const [roleList, setRoleList] = useState([]);
  const bottom = useRef(null);
  const streamRef = useRef(null);

  const openAskRole = async () => {
    try { setRoleList((await api.roles()).roles); } catch { setRoleList([]); }
    setAskRole(true);
  };

  const load = useCallback(async () => {
    const last = messages.length ? messages[messages.length - 1].id : 0;
    try {
      const r = await api.messages(chat.id, last);
      if (r.messages.length) setMessages((m) => [...m, ...r.messages]);
    } catch { /* ignore */ }
  }, [chat.id, messages]);

  useEffect(() => { setMessages([]); load(); }, [chat.id]);
  useEffect(() => {
    // SSE 优先；连不上再退回轮询（v2.1）
    let es = null, timer = null;
    const last = messages.length ? messages[messages.length - 1].id : 0;
    try {
      es = new EventSource(`${location.origin}/ui/chats/${chat.id}/stream?after=${last}`,
                           { withCredentials: true });
      es.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data);
          setMessages((prev) => (prev.some((x) => x.id === m.id) ? prev : [...prev, m]));
        } catch { /* ignore */ }
      };
      es.onerror = () => { if (es) { es.close(); es = null; } };
    } catch { /* fallback below */ }
    timer = setInterval(() => { if (!es) load(); }, 4000);
    return () => { if (es) es.close(); if (timer) clearInterval(timer); };
  }, [load]);
  useEffect(() => {
    // 只滚内部消息容器，别让 scrollIntoView 把整个窗口滚走（v1.1 踩过：侧边栏被推出视口）
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  async function send() {
    const body = text.trim();
    if (!body || busy) return;
    setText(""); setBusy(true);
    try {
      await api.send(chat.id, body);
      await load();
      onSent && onSent();
    } finally { setBusy(false); }
  }

  return (
    <div className="main">
      <div className="topbar">
        <div className="avatar">{initials(chat.title)}</div>
        <div>
          <div className="who">{chat.title}</div>
          <div className="sub">{chat.kind === "dm" ? "AI 员工 · 直连底座" : "群聊 · 多 Agent 协作"}</div>
        </div>
        <div className="spacer" />
        <button className="btn ghost" onClick={() => { setMessages([]); load(); }}>刷新</button>
        {chat.kind === "group" && (
          <button className="btn ghost" style={{ padding: "4px 8px" }} onClick={openAskRole}>
            调人
          </button>
        )}
        <button className="icon-btn" title="群聊设置" onClick={() => onMore(chat)}>
          <Icon name="gear" />
        </button>
        <button className="icon-btn" title="帮助" onClick={onHelp}><Icon name="help" /></button>
        <button className="icon-btn" title="关于" onClick={onAbout}><Icon name="alert" size="lg" /></button>
      </div>

      <div className="stream" ref={streamRef}>
        <div className="stream-inner">
          {messages.length === 0 && <div className="empty">还没有对话。下一句任务，它会建群、执行、把产物交回来。</div>}
          {messages.map((m) => (
            <div className={cls("msg", m.role === "human" && "human")} key={m.id}>
              <div className="avatar">{initials(m.role === "human" ? "我" : m.author || "AI")}</div>
              <div style={{ maxWidth: "86%" }}>
                <div className="author">{m.role === "human" ? "我" : m.author || "AI"}</div>
                <div className="bubble">
                  {m.kind === "project" && safeJson(m.meta).project_id ? (
                    <ProjectCard projectId={safeJson(m.meta).project_id} />
                  ) : m.kind === "role_request" ? (
                    <RoleRequestCard msg={m} chatId={chat.id} onDone={load} />
                  ) : m.kind === "warn" ? (
                    <div className="card warncard">
                      <h4>⚠️ 全权限执行中</h4>
                      <div>{m.content}</div>
                    </div>
                  ) : m.kind === "confirm" ? (
                    <ConfirmCard taskId={m.task_id} meta={safeJson(m.meta)} onAct={load} />
                  ) : m.kind === "approval" ? (
                    <ApprovalCard meta={safeJson(m.meta)} onAct={load} />
                  ) : m.kind === "card" ? (
                    <>
                      <Markdown text={m.content} />
                      {m.task_id && (
                        <TaskCard taskId={m.task_id} meta={safeJson(m.meta)} onAct={load} />
                      )}
                    </>
                  ) : (
                    <Markdown text={m.content} />
                  )}
                </div>
              </div>
            </div>
          ))}
          <div ref={bottom} />
        </div>
      </div>

      <div className="composer">
        <div className="tools">
          <label className="btn ghost" style={{ padding: "3px 8px", cursor: "pointer" }}>
            ＋ 附件
            <input type="file" style={{ display: "none" }} onChange={async (e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              await api.upload(chat.id, f);
              e.target.value = "";
              await load();
            }} />
          </label>
          <button className="btn ghost" style={{ padding: "3px 8px" }}
                  onClick={async () => {
                    const path = window.prompt("工作目录（产物会自动保存到这里）", chat.workdir || "");
                    if (path === null) return;
                    await api.patchChat(chat.id, { workdir: path.trim() });
                    if (onSent) onSent();
                  }}>
            📁 {chat.workdir ? chat.workdir.split(/[\/]/).pop() : "设定工作目录"}
          </button>
          <span className="spacer" />
          <span className="hint">@ 可提及成员</span>
        </div>
        <div className="composer-inner">
          <textarea
            rows={1}
            value={text}
            placeholder="发任务，或随便聊两句…  (Enter 发送 / Shift+Enter 换行)"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
            }}
          />
          <button className="btn primary" onClick={send} disabled={busy || !text.trim()}>
            {busy ? "发送中…" : "发送"}
          </button>
        </div>
        <div className="hint">模型在沙箱里执行，思考链与工具调用会实时出现在卡片里</div>
      </div>

      {askRole && (
        <div className="mask" onClick={() => setAskRole(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>申请调配角色</h3>
            <div className="hint">发一张调人卡到群里，老板或 CEO 点同意后，新员工立刻进群。</div>
            <div className="role-list" style={{ marginTop: 10 }}>
              {roleList.map((r) => (
                <div className="role-card" key={r.id}>
                  <div className="role-top">
                    <b>{r.name}</b>
                    <span className="pill">{TAG_LABEL[r.tag] || r.tag}</span>
                    <span className="spacer" />
                    <button className="btn" onClick={async () => {
                      await api.requestRole(chat.id, r.id);
                      setAskRole(false);
                      load();
                    }}>发调人卡</button>
                  </div>
                </div>
              ))}
            </div>
            <div className="actions">
              <button className="btn ghost" onClick={() => setAskRole(false)}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------- 通用递归树（v2.3）
   组织架构与资源目录共用一套：节点只描述 childrenOf / renderRow，
   展开状态由根组件统一持有，「全部展开 / 全部收起」对任意层级都生效。 */
const treeKey = (n) => n.__k || n.id || n.key;
const treeChildren = (childrenOf, n) => childrenOf(n) || [];

function collectKeys(nodes, childrenOf, depth = 1, maxDepth = 99, out = []) {
  for (const n of nodes || []) {
    if (depth <= maxDepth) out.push(treeKey(n));
    const kids = treeChildren(childrenOf, n);
    if (kids.length && depth < maxDepth) {
      collectKeys(kids, childrenOf, depth + 1, maxDepth, out);
    }
  }
  return out;
}

function TreeNode({ node, depth, expanded, onToggle, childrenOf, renderRow, childrenClassName }) {
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

function RecursiveTree({ roots, childrenOf, renderRow, defaultExpandDepth = 2,
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

function orgChildren(n) {
  if (n.t === "emp") return [];
  if (n.t === "pos") return (n.employees || []).map((e) => ({ ...e, t: "emp", __k: "emp:" + e.id }));
  return [
    ...(n.children || []).map((c) => ({ ...c, t: "entity", __k: "ent:" + c.id })),
    ...(n.positions || []).map((p) => ({ ...p, t: "pos", __k: "pos:" + p.id })),
  ];
}

const ENTITY_TYPE = { group_hq: "集团总部", subsidiary: "子公司", department: "部门" };

function OrgTree({ org, onPick }) {
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

function OrgView({ org, onPick }) {
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
          <div className="org-card">
            <OrgTree org={org} onPick={onPick} />
          </div>
        </div>
      </div>
    </div>
  );
}

function AddMember({ org, onClose, onDone }) {
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

/* ------------------------------------------------------------- group modal */
function NewGroup({ onClose, onDone }) {
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

/* ------------------------------------------------------- 群聊设置（v1.1） */
function GroupMenu({ chat, onClose, onChanged }) {
  const [members, setMembers] = useState([]);
  const [emps, setEmps] = useState([]);
  const [picked, setPicked] = useState([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    const [m, e] = await Promise.all([api.groupMembers(chat.id), api.employees()]);
    setMembers(m.members);
    setEmps(e.employees.filter((x) => (x.status || "online") !== "fired"));
  }, [chat.id]);

  useEffect(() => { load(); }, [load]);

  const inGroup = new Set(members.map((m) => m.employee_id));
  const candidates = emps.filter((e) => !inGroup.has(e.id));

  async function invite() {
    if (!picked.length) return;
    setBusy(true);
    try {
      await api.invite(chat.id, picked);
      setPicked([]); setNote("已邀请"); await load(); onChanged && onChanged();
    } finally { setBusy(false); }
  }

  async function kick(id) {
    setBusy(true);
    try { await api.kick(chat.id, id); setNote("已移出"); await load(); onChanged && onChanged(); }
    finally { setBusy(false); }
  }

  async function leave() {
    if (!confirm("确认退出？如果你是群主，这会直接解散本群。")) return;
    setBusy(true);
    try {
      await api.leaveGroup(chat.id);
      onChanged && onChanged();
      onClose();
    } finally { setBusy(false); }
  }

  return (
    <div className="mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>群聊设置 · {chat.title}</h3>

        <div className="section-title">当前成员（{members.length}）</div>
        {members.map((m) => (
          <div className="row" key={m.employee_id}>
            <span className="avatar">{initials(m.name)}</span>
            <div>
              <div>{m.name} <span className="tag">{m.role}</span></div>
              <div className="role">{m.title} · {m.model}</div>
            </div>
            <div className="spacer" />
            <button className="btn ghost" disabled={busy} onClick={() => kick(m.employee_id)}>移出</button>
          </div>
        ))}

        <div className="section-title">邀请成员</div>
        {candidates.length === 0 && <div className="hint">没有可邀请的 Agent 了</div>}
        {candidates.map((e) => (
          <label key={e.id} className="pick">
            <input type="checkbox" checked={picked.includes(e.id)}
                   onChange={() => setPicked((p) => p.includes(e.id) ? p.filter((x) => x !== e.id) : [...p, e.id])} />
            <span className="avatar">{initials(e.name)}</span>
            <span>{e.name}</span>
            <span className="role">{e.title} · {e.model}</span>
          </label>
        ))}

        {note && <div className="hint">{note}</div>}
        <div className="actions">
          <button className="btn primary" disabled={busy || !picked.length} onClick={invite}>邀请入群</button>
          <button className="btn danger" disabled={busy} onClick={leave}>退出 / 解散</button>
          <button className="btn ghost" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------- 员工详情（v1.1） */
function EmployeePanel({ emp, onClose, onChanged, onOpenChat }) {
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
      setNote("已保存");
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



/* ------------------------------------------------------- 设计确认卡（v1.2） */
function ConfirmCard({ taskId, meta, onAct }) {
  const [busy, setBusy] = useState(false);
  const [state, setState] = useState(meta?.state || "waiting");
  const [preview, setPreview] = useState(null);

  async function act(kind) {
    setBusy(true);
    try {
      await (kind === "approve" ? api.approveDesign(taskId) : api.rejectDesign(taskId));
      setState(kind === "approve" ? "approved" : "rejected");
      onAct && onAct();
    } finally { setBusy(false); }
  }

  return (
    <div className="card confirm">
      <h4>🎨 设计确认 · {taskId} · {state === "approved" ? "已同意" : state === "rejected" ? "已驳回" : "等待老板确认"}</h4>
      <div className="hint">这是阶段一（设计）。您点「同意方案」后，PM 才会派开发进入实施。</div>
      {meta?.artifact?.id && (
        <button className="btn ghost" onClick={() => setPreview({
          id: meta.artifact.id,
          name: (meta.artifact.file_path || "").split("\\").pop(),
          bytes: meta.artifact.bytes, task_id: taskId,
        })}>预览设计稿</button>
      )}
      {state === "waiting" && (
        <div className="actions">
          <button className="btn primary" disabled={busy} onClick={() => act("approve")}>同意方案</button>
          <button className="btn" disabled={busy} onClick={() => act("reject")}>驳回</button>
        </div>
      )}
      {preview && <PreviewModal file={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

/* --------------------------------------------------------- 产物预览（v1.2） */
const PREVIEWABLE = [".html", ".htm", ".svg", ".png", ".jpg", ".jpeg", ".md", ".txt", ".json"];

function PreviewModal({ file, onClose }) {
  const name = file.name || (file.file_path || "").split("\\").pop();
  const ext = ("." + name.split(".").pop()).toLowerCase();
  const isImage = [".png", ".jpg", ".jpeg", ".svg"].includes(ext);
  return (
    <div className="mask" onClick={onClose}>
      <div className="modal preview" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <div>
            <div className="who">{name}</div>
            <div className="sub">{file.task_id ? `来自 ${file.task_id}` : "沙箱产物"} · {file.bytes || file.size || 0} B</div>
          </div>
          <div className="spacer" />
          <a className="btn ghost" href={api.fileUrl(file.id)} target="_blank" rel="noreferrer">下载</a>
          <button className="icon-btn" onClick={onClose}>×</button>
        </div>
        <iframe
          className="preview-frame"
          title="preview"
          sandbox="allow-scripts allow-popups"
          referrerPolicy="no-referrer"
          src={api.previewUrl(file.id)}
        />
        {isImage && <div className="hint">图片已按原尺寸显示在上方</div>}
      </div>
    </div>
  );
}

/* --------------------------------------------------- 审批卡 / 审批中心（v2.3） */
const APPROVAL_STATE = { pending: "待审批", approved: "已批准", rejected: "已拒绝" };

function ApprovalCard({ meta, onAct }) {
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

function ApprovalsView({ onChanged }) {
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

/* ------------------------------------------------------- 资源目录（v2.3） */
function ResourceSection({ onDrill }) {
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

/* ------------------------------------------------------- FinOps（v2.4） */
const cny = (n) => "¥" + (Number(n) || 0).toLocaleString("zh-CN",
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function FinOpsView() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [note, setNote] = useState("");
  const load = useCallback(async () => {
    try { setData(await api.finops(days)); } catch (e) { setNote(e.message || "加载失败"); }
  }, [days]);
  useEffect(() => { load(); }, [load]);

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

/* ------------------------------------------------------- 离线评测（v2.4） */
function EvalsView() {
  const [data, setData] = useState({ sets: [], batches: [] });
  const [detail, setDetail] = useState(null);
  const [runner, setRunner] = useState("mock_acp");
  const [promptMode, setPromptMode] = useState("stored");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
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

/* ------------------------------------------------------- 组织经验（v2.4） */
const EXP_KIND = { rejection: "驳回经验", consensus: "共识结论", decision: "决策否决" };

function ExperienceView() {
  const [data, setData] = useState({ experiences: [], stats: { layers: {}, by_kind: {} } });
  const [q, setQ] = useState("再做一个小程序，要求金额计算准确、可回滚");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setData(await api.experiences()); } catch { /* ignore */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function runPreview() {
    if (!q.trim()) return;
    setBusy(true);
    try { setPreview(await api.previewExperience(q)); }
    catch { setPreview(null); }
    finally { setBusy(false); }
  }

  const layers = data.stats?.layers || {};
  return (
    <div className="main">
      <div className="topbar">
        <div className="avatar">忆</div>
        <div>
          <div className="who">组织经验 · 分层记忆</div>
          <div className="sub">个人偏好 {layers.preferences || 0} · 项目记忆 {layers.project_memories || 0} ·
            组织经验 {layers.org_experiences || 0}</div>
        </div>
        <div className="spacer" />
        <button className="btn ghost" onClick={load}>刷新</button>
      </div>
      <div className="stream">
        <div className="stream-inner">
          <div className="fin-hero memory-hero">
            <div className="fin-kpi"><span>① 个人偏好</span><b>{layers.preferences || 0}</b>
              <span className="hint">称呼 / 口味（user_preferences）</span></div>
            <div className="fin-kpi"><span>② 项目记忆</span><b>{layers.project_memories || 0}</b>
              <span className="hint">按 entity / pack 隔离</span></div>
            <div className="fin-kpi"><span>③ 组织经验</span><b>{layers.org_experiences || 0}</b>
              <span className="hint">驳回 / 共识 / 否决</span></div>
          </div>

          <div className="fin-card">
            <h5>新项目注入预览</h5>
            <div className="row">
              <input className="search" style={{ flex: 1, width: "auto" }} value={q}
                     onChange={(e) => setQ(e.target.value)}
                     placeholder="输入新需求，看会命中哪些历史经验…" />
              <button className="btn primary" disabled={busy} onClick={runPreview}>
                {busy ? "检索中…" : "预览注入"}
              </button>
            </div>
            {preview && (
              <div className="exp-preview">
                {preview.count > 0 ? (
                  <>
                    <div className="hint">命中 {preview.count} 条 · 标签 {preview.tags.join(" / ") || "—"}</div>
                    <pre>{preview.text}</pre>
                  </>
                ) : <div className="hint">没有命中经验；立项时会跳过注入。</div>}
              </div>
            )}
          </div>

          <div className="fin-card">
            <h5>经验库</h5>
            {data.experiences.length === 0 && (
              <div className="hint">还没有沉淀。任务被驳回、研讨会结束或决策被否决时会自动写入。</div>
            )}
            {data.experiences.map((e) => (
              <div className="exp-row" key={e.id}>
                <div className="exp-head">
                  <span className={cls("pill", e.kind === "rejection" ? "bad" : e.kind === "consensus" ? "ok" : "warn")}>
                    {EXP_KIND[e.kind] || e.kind}
                  </span>
                  <b>{e.title}</b>
                  <span className="spacer" />
                  <span className="hint">用过 {e.uses} 次 · {e.created_at}</span>
                </div>
                <div className="exp-content">{e.content}</div>
                <div className="exp-tags">
                  {(e.tags || "").split(",").filter(Boolean).map((t) => <span className="chip" key={t}>{t}</span>)}
                  {e.project_id && <span className="chip">项目 {e.project_id}</span>}
                  {e.task_id && <span className="chip">任务 {e.task_id}</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- 文件库 */
function FilesTab() {
  const [files, setFiles] = useState([]);
  const [q, setQ] = useState("");
  const [preview, setPreview] = useState(null);
  const load = useCallback(async () => {
    try { const r = await api.files(); setFiles(r.files); } catch { /* ignore */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function del(id) {
    if (!confirm("删除这个文件？只做软删除，审计里仍可查。")) return;
    await api.deleteFile(id); load();
  }

  const shown = files.filter((f) => !q || (f.name + f.title).toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="main">
      <div className="topbar">
        <div className="avatar">文</div>
        <div>
          <div className="who">文件库</div>
          <div className="sub">{files.length} 个产物 · 下载 / 删除</div>
        </div>
        <div className="spacer" />
        <input className="search" placeholder="搜索文件名或任务…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn ghost" onClick={load}>刷新</button>
      </div>
      <div className="stream">
        <div className="stream-inner">
          <ResourceSection />
          {shown.length === 0 && <div className="empty">还没有产物。让某个 Agent 干点活试试。</div>}
          {shown.map((f) => (
            <div className="file-row" key={f.id}>
              <span className="ico">{f.name.endsWith(".html") ? "🌐" : f.name.endsWith(".py") ? "🐍" : "📄"}</span>
              <div>
                <div>{f.name} <span className="tag">{f.size ? `${f.size} B` : "文件缺失"}</span></div>
                <div className="role">{f.title} · {f.task_id} · {f.created_at}</div>
              </div>
              <div className="spacer" />
              {PREVIEWABLE.includes(("." + f.name.split(".").pop()).toLowerCase()) && (
                <button className="btn" onClick={() => setPreview(f)}>预览</button>
              )}
              <a className="btn ghost" href={api.fileUrl(f.id)} target="_blank" rel="noreferrer">下载</a>
              <button className="btn danger" onClick={() => del(f.id)}>删除</button>
            </div>
          ))}
        </div>
      </div>
      {preview && <PreviewModal file={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

/* ------------------------------------------------------------- 设置/账号 */
function SettingsModal({ onClose, theme, setTheme, onChanged }) {
  const [form, setForm] = useState({ default_model: "", default_api_base: "", theme });
  const [jobs, setJobs] = useState([]);
  const [chats, setChats] = useState([]);
  const [job, setJob] = useState({ chat_id: "", title: "每日任务报告", at_time: "09:00" });
  const [kb, setKb] = useState([]);
  const [note, setNote] = useState("");

  useEffect(() => {
    (async () => {
      const [s, j, c, k] = await Promise.all([api.settings(), api.jobs(), api.chats(), api.kb()]);
      setForm({ default_model: s.default_model, default_api_base: s.default_api_base, theme: s.theme });
      setJobs(j.jobs); setChats(c.chats); setKb(k.documents);
      setJob((x) => ({ ...x, chat_id: c.chats.find((y) => y.kind === "group")?.id || c.chats[0]?.id || "" }));
    })();
  }, []);

  async function save() {
    await api.saveSettings(form);
    setTheme(form.theme);
    setNote("设置已保存");
    onChanged && onChanged();
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

function AccountModal({ me, onClose }) {
  const [secret, setSecret] = useState("");
  const [note, setNote] = useState("");
  const [acc, setAcc] = useState(null);
  useEffect(() => { api.account().then(setAcc).catch(() => {}); }, []);

  async function change() {
    if (secret.length < 16) { setNote("至少 16 位"); return; }
    await api.changeSecret(secret);
    setSecret(""); setNote("密钥已更新，请重新登录");
  }

  return (
    <div className="mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>账号</h3>
        <div className="row"><span>身份 ID</span><div className="spacer" /><span className="mono">{me.identity_id}</span></div>
        <div className="row"><span>角色</span><div className="spacer" /><span>{me.role}</span></div>
        <div className="row"><span>所属实体</span><div className="spacer" /><span>{me.entity_name}</span></div>
        <div className="row"><span>已记住的偏好</span><div className="spacer" />
          <span className="mono">{JSON.stringify(acc?.preferences || {})}</span></div>
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

function HelpModal({ onClose }) {
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

function AboutModal({ onClose }) {
  const [info, setInfo] = useState(null);
  useEffect(() => { api.about().then(setInfo).catch(() => {}); }, []);
  return (
    <div className="mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>关于 Agent OS</h3>
        <div className="row"><span>版本</span><div className="spacer" /><span className="mono">v{info?.version || "1.1.0"}</span></div>
        <div className="row"><span>引擎</span><div className="spacer" /><span className="mono">{info?.engine || "DSH (ACP) + FastAPI"}</span></div>
        <div className="row"><span>外壳</span><div className="spacer" /><span className="mono">{info?.shell || "Tauri 2 / React 18"}</span></div>
        <div className="row"><span>冻结基线</span><div className="spacer" /><span className="mono">{info?.frozen_baseline || "v1.0.0"}</span></div>
        <div className="actions"><button className="btn primary" onClick={onClose}>关闭</button></div>
      </div>
    </div>
  );
}

function AlertBanner() {
  const [alerts, setAlerts] = useState([]);
  const load = useCallback(async () => {
    try { const r = await api.alerts(); setAlerts(r.alerts || []); } catch { /* ignore */ }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);
  if (!alerts.length) return null;
  const a = alerts[0];
  return (
    <div className={cls("banner", a.level === "critical" ? "bad" : "warn")}>
      <span>⚠️ {a.message}</span>
      <div className="spacer" />
      <button className="btn ghost" onClick={async () => { await api.ackAlert(a.id); load(); }}>知道了</button>
    </div>
  );
}


/* ------------------------------------------------------- 运行详情（v1.3） */
const SPAN_KIND = {
  parse: { label: "解析", cls: "k-parse" },
  acp_session: { label: "ACP 会话", cls: "k-session" },
  thinking: { label: "思考", cls: "k-think" },
  tool: { label: "工具", cls: "k-tool" },
  model: { label: "模型", cls: "k-model" },
  approval: { label: "审批等待", cls: "k-approval" },
  resume: { label: "续跑", cls: "k-resume" },
  other: { label: "其它", cls: "k-other" },
};

const fmtMs = (ms) => (ms >= 10000 ? (ms / 1000).toFixed(1) + "s" : Math.round(ms) + "ms");

function RunDetail({ chat, show, onToggle }) {
  const [taskId, setTaskId] = useState(null);
  const [view, setView] = useState(null);
  const [members, setMembers] = useState([]);
  const [artifact, setArtifact] = useState(null);
  const [pane, setPane] = useState("detail");
  const [trace, setTrace] = useState(null);
  const [audit, setAudit] = useState(null);
  const [compress, setCompress] = useState(true);
  const [evalNote, setEvalNote] = useState("");

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await api.messages(chat.id, 0);
        const withTask = [...r.messages].reverse().find((m) => m.task_id);
        if (alive) setTaskId(withTask ? withTask.task_id : null);
        const meta = withTask ? safeJson(withTask.meta) : null;
        if (alive && meta?.artifact) setArtifact(meta.artifact);
      } catch { /* ignore */ }
    };
    tick();
    const t = setInterval(tick, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [chat.id]);

  useEffect(() => {
    if (!taskId) { setView(null); return; }
    let alive = true;
    const tick = async () => {
      try { const v = await api.task(taskId); if (alive) setView(v); } catch { /* ignore */ }
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [taskId]);

  useEffect(() => {
    if (chat.kind !== "group") { setMembers([]); return; }
    api.groupMembers(chat.id).then((r) => setMembers(r.members)).catch(() => setMembers([]));
  }, [chat.id]);

  const crit = view?.criteria || [];
  const passed = crit.filter((c) => c.state === "passed").length;
  const pct = crit.length ? Math.round((passed / crit.length) * 100) : 0;
  const run = view?.runs?.[0];
  const state = view?.task?.state || "—";
  const isCordis = view?.owner?.preset === "cordis";
  const running = run && ["running", "spawned"].includes(run.state);

  return (
    <aside className={cls("detail", pane === "trace" && "trace-wide")}>
      <div className="head">
        <span className="who">运行详情</span>
        <span className={cls("pill",
          state === "completed" ? "ok" : state === "rejected" || state === "failed" ? "bad"
          : state === "in_progress" || state === "pending_review" ? "run" : "warn")}>
          {state}
        </span>
        <span className="spacer" />
        <button className="icon-btn" title="收起详情" onClick={onToggle}><Icon name="close" /></button>
      </div>

      <div className="seg">
        <button className={cls("seg-btn", pane === "detail" && "on")}
                onClick={() => setPane("detail")}>详情</button>
        <button className={cls("seg-btn", pane === "trace" && "on")}
                onClick={async () => {
                  setPane("trace");
                  const r = view?.runs?.[0];
                  if (r) { try { setTrace(await api.runTrace(r.id)); } catch { setTrace(null); } }
                }}>链路追踪</button>
      </div>

      {pane === "trace" ? (
        <div className="trace">
          {!trace && <div className="hint">这条 Run 还没有 trace 记录</div>}
          {trace && (() => {
            const raw = trace.spans || [];
            const waits = raw.filter((s) => s.kind === "approval")
              .map((s) => ({ a: s.started_ms, b: s.ended_ms }))
              .sort((x, y) => x.a - y.a);
            const waitsTotal = waits.reduce((a, w) => a + Math.max(0, w.b - w.a), 0);
            const rawTotal = Math.max(1, trace.trace.total_ms ||
              Math.max(...raw.map((s) => s.ended_ms || 0), 1));
            const applied = compress && waits.length > 0;
            // 压缩审批等待：把审批区间从时间轴上剪掉，剩下的执行段首尾相接。
            // 不压缩时审批条会占满整条轴，thinking/tool 的差异看不出来。
            const shift = (ms) => {
              if (!applied) return ms;
              let cut = 0;
              for (const w of waits) {
                if (ms <= w.a) break;
                cut += Math.min(ms, w.b) - w.a;
              }
              return Math.max(0, ms - cut);
            };
            const spans = applied ? raw.filter((s) => s.kind !== "approval") : raw;
            const total = Math.max(1, applied ? shift(rawTotal) : rawTotal);
            const slowest = [...spans]
              .filter((s) => s.kind === "thinking" || s.kind === "tool")
              .sort((a, b) => (b.ended_ms - b.started_ms) - (a.ended_ms - a.started_ms))
              .slice(0, 5);
            return (
              <>
                <div className="wf-head">
                  <span>总耗时 <b>{fmtMs(rawTotal)}</b></span>
                  {waits.length > 0 && (
                    <span>执行 <b>{fmtMs(total)}</b> · 审批等待 <b>{fmtMs(waitsTotal)}</b></span>
                  )}
                  <span><b>{spans.length}</b> 个 span</span>
                  <span><b>{trace.trace.total_tokens ||
                    raw.reduce((a, s) => a + (s.tokens || 0), 0)}</b> tokens</span>
                  <span className={cls("pill", trace.trace.state === "done" ? "ok" : "warn")}>
                    {trace.trace.state}
                  </span>
                  {waits.length > 0 && (
                    <button className="btn ghost wf-toggle" onClick={() => setCompress((v) => !v)}>
                      {compress ? "显示审批等待" : "压缩审批等待"}
                    </button>
                  )}
                </div>
                <div className="wf-legend">
                  {Object.entries(SPAN_KIND).filter(([k]) => k !== "other" && (k !== "approval" || !applied)).map(([k, v]) => (
                    <span className="wf-key" key={k}><i className={v.cls} />{v.label}</span>
                  ))}
                </div>
                {slowest.length > 0 && (
                  <div className="wf-top">
                    <span className="hint">最耗时</span>
                    {slowest.map((s, i) => (
                      <span className="wf-chip" key={s.id}>
                        #{i + 1} {s.name} · {fmtMs(s.ended_ms - s.started_ms)}
                      </span>
                    ))}
                  </div>
                )}
                <div className="wf">
                  {spans.map((sp) => {
                    const a = shift(sp.started_ms), b = shift(sp.ended_ms);
                    const dur = Math.max(0, b - a);
                    const left = Math.min(100, (a / total) * 100);
                    const w = Math.max(0.6, Math.min(100 - left, (dur / total) * 100));
                    const kind = SPAN_KIND[sp.kind] || SPAN_KIND.other;
                    return (
                      <div key={sp.id} className="wf-row" title={`${sp.name}\n${fmtMs(sp.ended_ms - sp.started_ms)} · ${sp.tokens || 0} tok\n${sp.detail || ""}`}
                           onClick={async () => {
                             try { setAudit((await api.spanAudit(sp.id)).audit); }
                             catch { setAudit([]); }
                           }}>
                        <div className="wf-name"><i className={kind.cls} />{sp.name}</div>
                        <div className="wf-track">
                          <div className={cls("wf-bar", kind.cls, sp.status !== "ok" && "bad")}
                               style={{ left: left + "%", width: w + "%" }} />
                        </div>
                        <div className="wf-ms">{fmtMs(dur)}{sp.tokens ? ` · ${sp.tokens}t` : ""}</div>
                      </div>
                    );
                  })}
                </div>
                {audit && (
                  <pre className="audit-pop">{JSON.stringify(audit.slice(0, 3), null, 1)}</pre>
                )}
              </>
            );
          })()}
        </div>
      ) : (
      <>
      <h5>上下文窗口</h5>
      <Ring pct={((run?.usage_tokens || 0) / 128000) * 100}
            used={run?.usage_tokens || 0} cap={128000} />

      <h5>当前任务</h5>
      <div className="glass" style={{ padding: 10 }}>
        <div className="kv"><span>{taskId || "暂无任务"}</span><b>{state}</b></div>
        <div className="bar"><i style={{ width: `${pct}%` }} /></div>
        <div className="kv"><span>判据通过</span><b>{passed}/{crit.length || 0}</b></div>
        <div className="kv"><span>Token</span><b>{(run?.usage_tokens || 0).toLocaleString()}</b></div>
        <div className="kv"><span>运行次数</span><b>{view?.runs?.length || 0}</b></div>
      </div>

      <h5>编排拓扑</h5>
      <div className="topo">
        {(members.length ? members : [{ name: chat.title, role: chat.kind }]).map((m) => (
          <div className="node" key={m.employee_id || m.name}>
            <span className="avatar" style={{ width: 22, height: 22, fontSize: 10 }}>
              {initials(m.name)}
            </span>
            <span>{m.name}</span>
            <span className="role">{m.role || ""}</span>
          </div>
        ))}
      </div>

      <h5>工作产物</h5>
      {artifact ? (
        <div className="art">
          <span>📄</span>
          <span>{(artifact.file_path || "").split("\\").pop()}</span>
          <span className="sz">{artifact.bytes} B</span>
          <span className="spacer" />
          {artifact.id && (
            <a className="btn ghost" href={api.fileUrl(artifact.id)} target="_blank" rel="noreferrer">下载</a>
          )}
        </div>
      ) : (
        <div className="hint">本任务还没有产物</div>
      )}

      {run && run.state === "succeeded" && (
        <button className="btn ghost" style={{ marginTop: 8 }}
          onClick={async () => {
            try {
              const r = await api.evalBaseline(run.id);
              setEvalNote(`已加入评测基准：${r.set.name}`);
            } catch (e) { setEvalNote(e.message || "加入失败"); }
          }}>🧪 设为基准用例</button>
      )}
      {evalNote && (
        <div className={cls("hint", evalNote.startsWith("已加入") && "ok-text")}>{evalNote}</div>
      )}

      {isCordis && (
        <button className="btn danger kill" disabled={!running}
          onClick={async () => {
            if (!run) return;
            if (!confirm(`确定熔断 ${run.id}？会直接杀掉底层 DSH 进程。`)) return;
            await api.killRun(run.id);
          }}>
          ⏻ 紧急熔断（Kill Switch）{running ? ` · pid ${run.pid || "—"}` : " · 无运行中进程"}
        </button>
      )}

      <h5>权限与连接</h5>
      <div className="kv"><span>执行环境</span><b>{isCordis ? "full-access ⚠️" : "sandboxed"}</b></div>
      <div className="kv"><span>工具策略</span><b>写入限任务目录</b></div>
      <div className="kv"><span>预算闸</span><b>按轮结算</b></div>
      </>
      )}
    </aside>
  );
}

/* ------------------------------------------------------------------ shell */
function App() {
  const [me, setMe] = useState(null);
  const [booted, setBooted] = useState(false);
  const [bootMsg, setBootMsg] = useState("载入中…");
  const [tab, setTab] = useState(new URLSearchParams(location.search).get("tab") || "chats");
  const [chats, setChats] = useState([]);
  const [active, setActive] = useState(null);
  const [org, setOrg] = useState(null);
  const [adding, setAdding] = useState(false);
  const [grouping, setGrouping] = useState(false);
  const [groupMenu, setGroupMenu] = useState(null);
  const [empPanel, setEmpPanel] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showAccount, setShowAccount] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [showAbout, setShowAbout] = useState(false);
  const [theme, setTheme] = useState(localStorage.getItem("oa_theme") || "dark");
  const [showDetail, setShowDetail] = useState(localStorage.getItem("oa_detail") !== "0");
  const [ctx, setCtx] = useState(null);          // {x,y,items}
  const [depts, setDepts] = useState([]);
  const [depting, setDepting] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);

  const refreshApprovals = useCallback(async () => {
    try { const r = await api.approvals(); setPendingCount((r.pending || []).length); }
    catch { /* 未登录/离线时静默 */ }
  }, []);

  const openCtx = (e, items) => {
    e.preventDefault(); e.stopPropagation();
    setCtx({ x: e.clientX, y: e.clientY, items });
  };
  useEffect(() => {
    const onCtx = (e) => setCtx({ x: e.detail.x, y: e.detail.y, items: e.detail.items });
    window.addEventListener("oa-ctx", onCtx);
    return () => window.removeEventListener("oa-ctx", onCtx);
  }, []);

  useEffect(() => {
    const close = () => setCtx(null);
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    return () => { window.removeEventListener("click", close); window.removeEventListener("scroll", close, true); };
  }, []);

  async function patchChat(id, body) {
    await api.patchChat(id, body);
    const c = await api.chats(); setChats(c.chats);
  }
  async function renameDept(d) {
    const name = window.prompt("部门新名称", d.name);
    if (!name || name === d.name) return;
    await api.renameDept(d.id, name);
    setDepts((await api.departments()).departments);
  }
  async function removeDept(d) {
    const ok = window.confirm(`删除部门「${d.name}」？
部门下 ${d.headcount} 名员工会转到「未分配」。`);
    if (!ok) return;
    await api.deleteDept(d.id);
    setDepts((await api.departments()).departments);
    setOrg(await api.org());
  }
  async function removeChat(chat) {
    const ok = window.confirm(`删除「${chat.title}」？
会立即停止该会话正在运行的任务，并清理沙箱临时目录。`);
    if (!ok) return;
    await api.deleteChat(chat.id);
    const c = await api.chats(); setChats(c.chats);
    if (active?.id === chat.id) setActive(c.chats[0] || null);
  }

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("oa_theme", theme);
  }, [theme]);

  const boot = useCallback(async () => {
    // 桌面版双击时后端可能是刚被拉起来的，等它 30 秒而不是直接白屏
    for (let i = 0; i < 30; i++) {
      if (await ping()) break;
      setBootMsg(i > 1 ? "正在叫醒本地服务…" : "载入中…");
      await new Promise((r) => setTimeout(r, 1000));
    }
    try {
      const m = await api.me();
      setMe(m);
      const [c, o, dp] = await Promise.all([api.chats(), api.org(), api.departments()]);
      setChats(c.chats); setOrg(o); setDepts(dp.departments);
      if (!active && c.chats.length) setActive(c.chats[0]);
    } catch (e) {
      if (e.code !== 401) console.error(e);
      setMe(null);
    } finally { setBooted(true); }
  }, [active]);

  useEffect(() => { boot(); }, []);

  useEffect(() => {
    if (!me) return;
    refreshApprovals();
    const t = setInterval(refreshApprovals, 5000);
    return () => clearInterval(t);
  }, [me, refreshApprovals]);

  // 注意：所有 hook 必须在 early return 之前，否则 React #310（v1.3 踩过）
  useEffect(() => { localStorage.setItem("oa_detail", showDetail ? "1" : "0"); }, [showDetail]);

  if (!booted) return <div className="empty"><span className="spin" /> {bootMsg}</div>;
  if (!me) return <Login onDone={boot} />;

  return (
    <div className={cls("shell", !showDetail && "no-detail")}>
      <nav className="rail">
        <div className="logo">A</div>
        {[
          ["chats", "chat", "会话"],
          ["org", "org", "组织架构"],
          ["roles", "role", "角色库"],
          ["files", "files", "文件与资源"],
          ["approvals", "shield", "审批中心"],
          ["finops", "wallet", "财务总览"],
          ["evals", "flask", "离线评测"],
          ["experience", "brain", "组织经验"],
        ].map(([k, ic, label]) => (
          <button key={k} title={label}
                  className={cls("rail-btn", tab === k && "active")}
                  onClick={() => setTab(k)}>
            <Icon name={ic} size="lg" />
            {k === "approvals" && pendingCount > 0 && (
              <span className="rail-badge">{pendingCount}</span>
            )}
          </button>
        ))}
        <div className="grow" />
        <button className="rail-btn" title="设置" onClick={() => setShowSettings(true)}>⚙</button>
        <button className="rail-btn" title="账号" onClick={() => setShowAccount(true)}>👤</button>
      </nav>

      <aside className="sidebar">
        <div className="panel-head">
          <span className="ttl">
            {{ chats: "会话", org: "组织架构", roles: "角色库",
               files: "文件与资源", approvals: "审批中心", finops: "财务总览",
               evals: "离线评测", experience: "组织经验" }[tab]}
          </span>
          <span className="spacer" />
          {tab === "chats" && (
            <button className="btn" style={{ padding: "3px 8px" }} onClick={() => setGrouping(true)}>
              <Icon name="plus" />
            </button>
          )}
          {tab === "org" && (
            <button className="btn" style={{ padding: "3px 8px" }} onClick={() => setDepting(true)}>+ 部门</button>
          )}
        </div>

        {tab === "chats" && (
          <>
            <div className="list">
              {chats.map((c) => (
                <div key={c.id}
                     className={cls("chat-item", active?.id === c.id && "active", c.pinned && "pinned")}
                     onClick={() => setActive(c)}
                     onContextMenu={(e) => openCtx(e, [
                       { label: c.pinned ? "取消置顶" : "置顶", onClick: () => patchChat(c.id, { pinned: c.pinned ? 0 : 1 }) },
                       { label: "隐藏", onClick: () => patchChat(c.id, { hidden: 1 }) },
                       { sep: true },
                       { label: "删除会话", danger: true, onClick: () => removeChat(c) },
                     ])}>
                  <div className={cls("avatar", c.kind === "ceo" && "ceo")}>
                    {c.kind === "ceo" ? "CEO" : initials(c.employee_name || c.title)}
                  </div>
                  <div className="meta">
                    <div className="name">
                      {c.employee_name || c.title}
                      {c.kind === "ceo" && <span className="tag-ceo">调度台</span>}
                      <span className="dot" />
                    </div>
                    <div className="preview">{c.last_message || "开始对话"}</div>
                  </div>
                </div>
              ))}
              {chats.length === 0 && <div className="empty">还没有会话</div>}
            </div>
          </>
        )}

        {tab === "org" && (
          <>
            <div className="dept-list">
              {depts.map((d) => (
                <div key={d.id} className="dept-row"
                     onContextMenu={(e) => openCtx(e, d.type === "group_hq" ? [
                       { label: "重命名", onClick: () => renameDept(d) },
                     ] : [
                       { label: "重命名", onClick: () => renameDept(d) },
                       { sep: true },
                       { label: "删除部门", danger: true, onClick: () => removeDept(d) },
                     ])}>
                  <span className="dept-name">{d.name}</span>
                  <span className="hint">{d.headcount} 人</span>
                </div>
              ))}
            </div>
            <div className="hint" style={{ padding: "8px 14px" }}>
              组织树在右侧展开，支持逐级折叠与「全部展开/收起」。
            </div>
          </>
        )}

        {tab === "roles" && (
          <RolesView onChanged={async () => { const o = await api.org(); setOrg(o); }} />
        )}

        {tab === "files" && <div className="empty" style={{ padding: 20 }}>左侧是资源目录（RBAC），右侧是产物</div>}

        {tab === "approvals" && (
          <div className="ap-side">
            <div className="ap-side-num">{pendingCount}</div>
            <div className="hint">条待批 · 批准后按 approval_id + run_id 断点续跑</div>
            <button className="btn ghost" style={{ marginTop: 12 }}
                    onClick={() => setTab("files")}>去资源目录发起演练</button>
          </div>
        )}

        {tab === "finops" && (
          <div className="empty" style={{ padding: 20 }}>
            真金白银按部门/员工/模型拆开算，计价口径在 .env。
          </div>
        )}
        {tab === "evals" && (
          <div className="empty" style={{ padding: 20 }}>
            基准来自已完成 Run；回归用当前 Prompt 与模型重跑。
          </div>
        )}
        {tab === "experience" && (
          <div className="empty" style={{ padding: 20 }}>
            驳回原因、研讨会共识、被否决策自动沉淀，立项时注入。
          </div>
        )}

        <div className="sidebar-footer">
          <button className="icon-btn" title="设置" onClick={() => setShowSettings(true)}>⚙</button>
          <button className="account" onClick={() => setShowAccount(true)}>
            <span className="avatar">{initials(me.name)}</span>
            <span>{me.name}</span>
            <span className="role">{me.role}</span>
          </button>
          <button className="icon-btn" title="主题" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? "☀" : "☾"}
          </button>
        </div>
      </aside>

      <ContextMenu ctx={ctx} />
      {depting && <NewDept onClose={() => setDepting(false)}
                            onDone={async () => { setOrg(await api.org()); }} />}

      <div className="right">
        <AlertBanner />
        {tab === "finops" ? (
          <FinOpsView />
        ) : tab === "evals" ? (
          <EvalsView />
        ) : tab === "experience" ? (
          <ExperienceView />
        ) : tab === "org" ? (
          <OrgView org={org} onPick={(emp) => setEmpPanel(emp)} />
        ) : tab === "approvals" ? (
          <ApprovalsView onChanged={refreshApprovals} />
        ) : tab === "files" ? (
          <FilesTab />
        ) : active ? (
          <ChatView
            chat={active}
            onSent={boot}
            onMore={(c) => setGroupMenu(c)}
            onHelp={() => setShowHelp(true)}
            onAbout={() => setShowAbout(true)}
            onToggleDetail={() => setShowDetail((v) => !v)}
          />
        ) : (
          <div className="main"><div className="empty">选一个会话，或去组织架构里加个 AI 员工</div></div>
        )}
        {empPanel && tab === "org" && (
          <EmployeePanel
            emp={empPanel}
            onClose={() => setEmpPanel(null)}
            onChanged={boot}
            onOpenChat={(emp) => {
              const c = chats.find((x) => x.employee_id === emp.id);
              if (c) { setActive(c); setTab("chats"); setEmpPanel(null); }
            }}
          />
        )}
      </div>

      {active && !["files", "approvals", "org", "finops", "evals", "experience"].includes(tab) && (
        <RunDetail chat={active} show={showDetail} onToggle={() => setShowDetail(false)} />
      )}

      {adding && <AddMember org={org} onClose={() => setAdding(false)} onDone={boot} />}
      {grouping && (
        <NewGroup
          onClose={() => setGrouping(false)}
          onDone={async (chatId) => {
            await boot();
            const c = await api.chats();
            setChats(c.chats);
            const g = c.chats.find((x) => x.id === chatId);
            if (g) setActive(g);
          }}
        />
      )}
      {groupMenu && (
        <GroupMenu
          chat={groupMenu}
          onClose={() => setGroupMenu(null)}
          onChanged={async () => { await boot(); }}
        />
      )}
      {showSettings && (
        <SettingsModal
          theme={theme} setTheme={setTheme}
          onClose={() => setShowSettings(false)}
          onChanged={boot}
        />
      )}
      {showAccount && <AccountModal me={me} onClose={() => setShowAccount(false)} />}
      {showHelp && <HelpModal onClose={() => setShowHelp(false)} />}
      {showAbout && <AboutModal onClose={() => setShowAbout(false)} />}
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);

/* ------------------------------------------------------------- 角色库（M15） */
const TAG_LABEL = { dev: "开发", design: "设计", content: "文案", data: "数据",
                    qa: "测试", translate: "翻译" };

const INDUSTRIES = ["全部", "研发", "金融", "市场", "设计", "法务", "运营", "通用"];

function RolesView({ onChanged }) {
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

function RoleDrawer({ role, onClose, onSaved }) {
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
        <div className="actions">
          <button className="btn primary" disabled={busy} onClick={save}>保存（即改即生效）</button>
          <button className="btn ghost" onClick={onClose}>取消</button>
        </div>
      </div>
    </>
  );
}

/* ------------------------------------------------ 调人卡（M15 模块三） */
function RoleRequestCard({ msg, chatId, onDone }) {
  const meta = safeJson(msg.meta);
  const [busy, setBusy] = useState(false);
  const done = meta.state && meta.state !== "pending";

  async function decide(ok) {
    setBusy(true);
    try { await api.decideRole(chatId, msg.id, ok); await onDone(); }
    finally { setBusy(false); }
  }

  return (
    <div className={cls("card", "role-request", done && "done")}>
      <h4>🙋 申请调配角色</h4>
      <div>
        PM 想拉 <b>{meta.name}</b>（{TAG_LABEL[meta.tag] || meta.tag}）进群协同。
      </div>
      {done ? (
        <div className="hint">状态：{meta.state === "approved" ? "已同意，已进群" : "已拒绝"}</div>
      ) : (
        <div className="actions">
          <button className="btn primary" disabled={busy} onClick={() => decide(true)}>同意</button>
          <button className="btn ghost" disabled={busy} onClick={() => decide(false)}>拒绝</button>
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------- 项目卡（v1.5） */
const STAGE_LABEL = {
  intake: "接需求", charter: "立项", workshop: "研讨", wbs: "拆解",
  build: "实现", test: "测试", accept: "验收", release: "发布", retro: "复盘",
};

function ProjectCard({ projectId }) {
  const [v, setV] = useState(null);
  const [showTl, setShowTl] = useState(false);

  useEffect(() => {
    let alive = true;
    api.project(projectId).then((d) => { if (alive) setV(d); }).catch(() => {});
    return () => { alive = false; };
  }, [projectId]);

  if (!v) return <div className="card">项目 {projectId} 载入中…</div>;

  const done = v.stages.filter((s) => s.state === "done").length;
  const blocked = v.project.state === "blocked";

  return (
    <div className={cls("card", "project-card", blocked && "blocked")}>
      <h4>项目 {v.project.id} · {v.project.name}</h4>
      <div className="hint">
        {done}/{v.stages.length} 阶段完成
        {blocked ? " · ⛔ 已被否决停摆" : ""} · 闸门 {v.gates.filter((g) => g.state === "passed").length}/{v.gates.length} 通过
      </div>
      <div className="stages">
        {v.stages.map((s) => (
          <div key={s.id} className={cls("stage", s.state)}>
            <span className="dot-s" />
            <span className="nm">{s.seq}. {STAGE_LABEL[s.key] || s.key}</span>
            <span className="st">{s.state}</span>
          </div>
        ))}
      </div>
      <div className="files">
        {v.deliverables.map((d) => (
          <div key={d.id} className="fileline">
            <span className="kind">{d.kind}</span>
            <span className="path">{String(d.file_path).split("\\").pop()}</span>
            <span className="hint">{d.bytes} B</span>
          </div>
        ))}
      </div>
      {v.workshop && (
        <div className="hint">
          研讨会：{v.turns.length} 条发言（提案 {v.turns.filter((t) => t.kind === "proposal").length} /
          质询 {v.turns.filter((t) => t.kind === "question").length} /
          风险 {v.turns.filter((t) => t.kind === "risk").length} /
          表决 {v.turns.filter((t) => t.kind === "vote").length}）
        </div>
      )}
      <div className="actions">
        <button className="btn ghost" onClick={() => setShowTl((x) => !x)}>
          {showTl ? "收起审计时间轴" : "查看审计时间轴"}
        </button>
        <button className="btn ghost" onClick={async () => {
          const next = v.project.use_llm_docs ? 0 : 1;
          await api.patchProject(v.project.id, { use_llm_docs: next });
          const d = await api.project(v.project.id);
          setV(d);
        }}>
          文档模式：{v.project.use_llm_docs ? "模型生成" : "模板（省 token）"}
        </button>
      </div>
      {showTl && (
        <div className="timeline">
          {v.timeline.slice(-24).map((t, i) => (
            <div key={i} className="tl-row">
              <span className="tl-act">{t.action}</span>
              <span className="hint">{t.created_at}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------ V2.0 组件 */
function ContextMenu({ ctx }) {
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

function NewDept({ onClose, onDone }) {
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
