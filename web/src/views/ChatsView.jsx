import React, { useState, useEffect, useRef, useCallback } from "react";
import api from "../api.js";
import { initials, cls, safeJson, Icon, Markdown } from "./common.jsx";
import { TAG_LABEL } from "./OrgRolesView.jsx";
import { TaskCard, ProjectCard } from "./TasksView.jsx";
import { ApprovalCard } from "./ApprovalsView.jsx";
import { PreviewModal, PREVIEWABLE } from "./ArtifactsView.jsx";
import { FailureDiagnosis } from "../ProjectFlow.jsx";

export const SPAN_KIND = {
  parse: { label: "解析", cls: "k-parse" },
  acp_session: { label: "ACP 会话", cls: "k-session" },
  thinking: { label: "思考", cls: "k-think" },
  tool: { label: "工具", cls: "k-tool" },
  model: { label: "模型", cls: "k-model" },
  approval: { label: "审批等待", cls: "k-approval" },
  resume: { label: "续跑", cls: "k-resume" },
  other: { label: "其它", cls: "k-other" },
};

export const fmtMs = (ms) => (ms >= 10000 ? (ms / 1000).toFixed(1) + "s" : Math.round(ms) + "ms");

export function ConfirmCard({ taskId, meta, onAct }) {
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

export function GroupMenu({ chat, onClose, onChanged }) {
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

export function RoleRequestCard({ msg, chatId, onDone }) {
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

export function RunDetail({ chat, show, onToggle, onApproval }) {
  const [compact, setCompact] = useState(window.innerWidth <= 1200);
  useEffect(() => {
    const resize = () => setCompact(window.innerWidth <= 1200);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  const [taskId, setTaskId] = useState(null);
  const [view, setView] = useState(null);
  const [members, setMembers] = useState([]);
  const [artifact, setArtifact] = useState(null);
  const [pane, setPane] = useState("detail");
  const [trace, setTrace] = useState(null);
  const [audit, setAudit] = useState(null);
  const [compress, setCompress] = useState(true);
  const [evalNote, setEvalNote] = useState("");
  const [preview, setPreview] = useState(null);
  const chatScope = useRef(chat.id);
  chatScope.current = chat.id;
  const runScope = useRef(null);
  const stateLabels = { todo: "待分配", assigned: "已分配", in_progress: "执行中",
    pending_review: "待验收", completed: "已完成", rejected: "已驳回", failed: "执行失败", cancelled: "已中止" };

  useEffect(() => {
    let alive = true;
    setTaskId(null); setView(null); setArtifact(null); setTrace(null); setAudit(null);
    setPane("detail"); setEvalNote(""); setPreview(null); setMembers([]);
    const tick = async () => {
      try {
        const r = await api.messages(chat.id, 0);
        const withTask = [...r.messages].reverse().find((m) => m.task_id);
        if (!alive) return;
        const nextId = withTask ? withTask.task_id : null;
        setTaskId(nextId);
        const withArtifact = nextId ? [...r.messages].reverse().find((m) =>
          m.task_id === nextId && safeJson(m.meta)?.artifact) : null;
        setArtifact(withArtifact ? safeJson(withArtifact.meta).artifact : null);
      } catch { /* ignore */ }
    };
    tick();
    const t = setInterval(tick, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [chat.id]);

  useEffect(() => {
    setView(null); setTrace(null); setAudit(null); setPreview(null); setEvalNote("");
    if (!taskId) { setView(null); return; }
    let alive = true;
    const tick = async () => {
      try { const v = await api.task(taskId); if (alive) setView(v); } catch { /* ignore */ }
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [taskId, chat.id]);

  useEffect(() => {
    if (chat.kind !== "group") { setMembers([]); return; }
    let alive = true;
    api.groupMembers(chat.id).then((r) => { if (alive) setMembers(r.members); })
      .catch(() => { if (alive) setMembers([]); });
    return () => { alive = false; };
  }, [chat.id, chat.kind]);

  const crit = view?.criteria || [];
  const passed = crit.filter((c) => c.state === "passed").length;
  const pct = crit.length ? Math.round((passed / crit.length) * 100) : 0;
  const run = view?.runs?.[0];
  runScope.current = run?.id || null;
  const state = view?.task?.state || "—";
  const waitingForApproval = view?.task?.awaiting_approval ?? view?.awaiting_approval ?? false;
  const pendingApproval = view?.task?.pending_approval ?? view?.pending_approval;
  const isCordis = view?.owner?.preset === "cordis";
  const running = run && ["running", "spawned"].includes(run.state);
  const artifacts = view?.artifacts === undefined ? (artifact ? [artifact] : []) : view.artifacts;

  useEffect(() => {
    let alive = true;
    setTrace(null); setAudit(null);
    if (pane === "trace" && run?.id) {
      api.runTrace(run.id).then((value) => { if (alive) setTrace(value); })
        .catch(() => { if (alive) setTrace(null); });
    }
    return () => { alive = false; };
  }, [pane, run?.id, chat.id]);

  return (
    <aside className={cls("detail", pane === "trace" && "trace-wide")} role={compact ? "dialog" : undefined} aria-label="运行详情">
      <div className="head">
        <span className="who">运行详情</span>
        <span className={cls("pill", waitingForApproval ? "warn" :
          state === "completed" ? "ok" : state === "rejected" || state === "failed" ? "bad"
          : state === "in_progress" || state === "pending_review" ? "run" : "warn")}>
          {waitingForApproval ? "待审批" : stateLabels[state] || state}
        </span>
        <span className="spacer" />
        <button className="icon-btn" title="收起详情" onClick={onToggle}><Icon name="close" /></button>
      </div>

      <div className="seg">
        <button className={cls("seg-btn", pane === "detail" && "on")}
                onClick={() => setPane("detail")}>详情</button>
        <button className={cls("seg-btn", pane === "trace" && "on")}
                onClick={() => setPane("trace")}>链路追踪</button>
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
                             const chatId = chat.id, runId = run?.id;
                             try {
                               const value = (await api.spanAudit(sp.id)).audit;
                               if (chatScope.current === chatId && runScope.current === runId) setAudit(value);
                             } catch {
                               if (chatScope.current === chatId && runScope.current === runId) setAudit([]);
                             }
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
      <h5>执行用量</h5>
      <div className="usage-summary"><b>{(run?.usage_tokens || 0).toLocaleString()}</b><span>tokens · 本次执行累计</span></div>

      <h5>当前任务</h5>
      <div className="glass" style={{ padding: 10 }}>
        <div className="kv"><span>{taskId || "暂无任务"}</span><b>{waitingForApproval ? "待审批" : stateLabels[state] || state}</b></div>
        {waitingForApproval && <div className="hint" style={{ textAlign: "left" }}>任务已暂停，等待操作审批。{pendingApproval && onApproval && <button className="btn ghost" onClick={onApproval}>前往审批中心</button>}</div>}
        <div className="bar"><i style={{ width: `${pct}%` }} /></div>
        <div className="kv"><span>判据通过</span><b>{passed}/{crit.length || 0}</b></div>
        <div className="kv"><span>Token</span><b>{(run?.usage_tokens || 0).toLocaleString()}</b></div>
        <div className="kv"><span>运行次数</span><b>{view?.runs?.length || 0}</b></div>
      </div>

      {(state === "failed" || state === "rejected") && run?.id && (
        <FailureDiagnosis runId={run.id} taskId={taskId} onRetry={() => onApproval?.()} />
      )}

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
      {(artifacts || []).length ? artifacts.map((item, i) => (
        <div className="art" key={item.id || i}>
          <span>📄</span>
          <span>{(item.file_path || item.name || "产物").split(/[\\/]/).pop()}</span>
          <span className="sz">{item.bytes || 0} B</span>
          <span className="spacer" />
          {item.id && PREVIEWABLE.includes(("." + (item.file_path || item.name || "").split(".").pop()).toLowerCase()) && (
            <button className="btn ghost" onClick={() => setPreview({
              ...item, name: (item.file_path || item.name || "产物").split(/[\\/]/).pop(), task_id: taskId,
            })}>预览</button>
          )}
          {item.id && (
            <a className="btn ghost" href={api.fileUrl(item.id)} target="_blank" rel="noreferrer">下载</a>
          )}
        </div>
      )) : (
        <div className="hint">本任务还没有产物</div>
      )}
      {preview && <PreviewModal file={preview} onClose={() => setPreview(null)} />}

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

export function ChatView({ chat, onSent, onMore, onHelp, onAbout, onApproval, onToggleDetail, showDetail }) {
  const [messageState, setMessageState] = useState({ chatId: chat.id, rows: [] });
  const messages = messageState.chatId === chat.id ? messageState.rows : [];
  const latestTaskMessage = [...messages].reverse().find(message => message.task_id);
  const liveTaskId = latestTaskMessage?.task_id && !messages.some(message =>
    message.task_id === latestTaskMessage.task_id && message.kind === "card") ? latestTaskMessage.task_id : null;
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [askRole, setAskRole] = useState(false);
  const [roleList, setRoleList] = useState([]);
  const bottom = useRef(null);
  const streamRef = useRef(null);
  const attachmentInput = useRef(null);
  const stickToBottom = useRef(true);
  const scopeRef = useRef({ chatId: chat.id, rows: [], cursor: 0 });
  if (scopeRef.current.chatId !== chat.id) {
    scopeRef.current = { chatId: chat.id, rows: [], cursor: 0 };
  }
  const mergeMessages = useCallback((scope, incoming) => {
    if (scopeRef.current !== scope || scope.active === false || !incoming?.length) return;
    const rows = new Map(scope.rows.map((m) => [m.id, m]));
    incoming.forEach((m) => rows.set(m.id, m));
    scope.rows = [...rows.values()].sort((a, b) => Number(a.id) - Number(b.id));
    scope.cursor = scope.rows.reduce((last, m) => Math.max(last, Number(m.id) || 0), 0);
    setMessageState({ chatId: scope.chatId, rows: scope.rows });
  }, []);

  const openAskRole = async () => {
    try { setRoleList((await api.roles()).roles); } catch { setRoleList([]); }
    setAskRole(true);
  };

  const load = useCallback(async (full = false) => {
    const scope = scopeRef.current;
    if (scope.chatId !== chat.id || scope.active === false) return;
    try {
      const r = await api.messages(chat.id, full ? 0 : scope.cursor);
      if (scopeRef.current !== scope || scope.active === false) return;
      mergeMessages(scope, r.messages);
      setLoadError("");
    } catch (e) {
      if (scopeRef.current === scope && scope.active !== false) setLoadError(`消息加载失败：${e.message || "请刷新重试"}`);
    }
  }, [chat.id, mergeMessages]);

  useEffect(() => {
    const scope = scopeRef.current;
    let alive = true, es = null, reconnect = null;
    scope.active = true;
    stickToBottom.current = true;
    setMessageState({ chatId: chat.id, rows: scope.rows });
    setText(""); setBusy(false); setUploading(false); setError(""); setLoadError("");
    setAskRole(false);
    const connect = () => {
      if (!alive || scopeRef.current !== scope) return;
      try {
        const source = new EventSource(api.messageStreamUrl(chat.id, scope.cursor), { withCredentials: true });
        es = source;
        source.onmessage = (ev) => {
          if (!alive || scopeRef.current !== scope) return;
          try { mergeMessages(scope, [JSON.parse(ev.data)]); setLoadError(""); } catch { /* ignore */ }
        };
        source.onerror = () => {
          source.close();
          if (es === source) es = null;
          if (alive) reconnect = setTimeout(connect, 5000);
        };
      } catch {
        if (alive) reconnect = setTimeout(connect, 5000);
      }
    };
    load(true).then(connect);
    const timer = setInterval(() => {
      if (!es || es.readyState !== EventSource.OPEN) load();
    }, 4000);
    return () => {
      alive = false;
      scope.active = false;
      if (es) es.close();
      if (reconnect) clearTimeout(reconnect);
      clearInterval(timer);
    };
  }, [chat.id, load, mergeMessages]);

  useEffect(() => {
    const el = streamRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  async function send() {
    const draft = text;
    const body = draft.trim();
    if (!body || busy) return;
    const scope = scopeRef.current;
    setBusy(true); setError("");
    try {
      await api.send(chat.id, body);
      if (scopeRef.current !== scope || scope.active === false) return;
      stickToBottom.current = true;
      setText((current) => current === draft ? "" : current);
      await load();
      onSent && onSent();
    } catch (e) {
      if (scopeRef.current === scope) setError(`发送失败，内容已保留：${e.message || "请重试"}`);
    } finally { if (scopeRef.current === scope) setBusy(false); }
  }

  return (
    <div className="main">
      <div className="topbar">
        <div className="avatar">{initials(chat.title)}</div>
        <div>
          <div className="who">{chat.title}</div>
          <div className="sub">{chat.kind === "dm" ? "AI 员工 · 直接执行任务" : chat.kind === "ceo" ? "调度台 · 自动组建项目" : "协作群 · 团队分工"}</div>
        </div>
        <div className="spacer" />
        <button className="btn ghost" onClick={() => load(true)}>刷新</button>
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
        <button className="icon-btn" title={showDetail ? "收起运行详情" : "显示运行详情"}
                aria-label={showDetail ? "收起运行详情" : "显示运行详情"}
                aria-expanded={!!showDetail} onClick={onToggleDetail}><Icon name="panel" /></button>
      </div>

      <div className="stream" ref={streamRef} onScroll={() => {
        const el = streamRef.current;
        if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 96;
      }}>
        <div className="stream-inner">
          {messages.length === 0 && <div className="empty">还没有对话。下一句任务，它会建群、执行、把产物交回来。</div>}
          {messages.map((m) => (
            <div className={cls("msg", m.role === "human" && "human")} key={m.id}>
              <div className="avatar">{initials(m.role === "human" ? "我" : m.author || "AI")}</div>
              <div style={{ maxWidth: "86%" }}>
                <div className="author">{m.role === "human" ? "我" : m.author || "AI"}</div>
                <div className="bubble">
                  {m.kind === "project" && safeJson(m.meta)?.project_id ? (
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
                        <TaskCard taskId={m.task_id} meta={safeJson(m.meta)} onAct={load} onApproval={onApproval} />
                      )}
                    </>
                  ) : (
                    <Markdown text={m.content} />
                  )}
                </div>
              </div>
            </div>
          ))}
          {liveTaskId && (
            <section aria-label="当前任务执行" style={{ margin: "20px 0" }}>
              <TaskCard key={liveTaskId} taskId={liveTaskId} meta={safeJson(latestTaskMessage.meta)} onAct={load} onApproval={onApproval} />
            </section>
          )}
          <div ref={bottom} />
        </div>
      </div>

      <div className="composer">
        {(error || loadError) && <div className="err" role="alert">{error || loadError}</div>}
        <div className="tools">
          <button type="button" className="btn ghost" aria-label="上传附件" aria-haspopup="dialog" disabled={uploading} style={{ padding: "3px 8px" }} onClick={() => attachmentInput.current?.click()}>
            {uploading ? "上传中…" : "＋ 附件"}
          </button>
          <input ref={attachmentInput} type="file" aria-label="附件文件" disabled={uploading} style={{ display: "none" }} onChange={async (e) => {
            const f = e.target.files?.[0];
            if (!f) return;
            const scope = scopeRef.current;
            e.target.value = "";
            setUploading(true); setError("");
            try {
              await api.upload(chat.id, f);
              if (scopeRef.current === scope) await load();
            } catch (err) {
              if (scopeRef.current === scope) setError(`上传失败：${err.message || "请重试"}`);
            } finally { if (scopeRef.current === scope) setUploading(false); }
          }} />
          <button className="btn ghost" style={{ padding: "3px 8px" }}
                  onClick={async () => {
                    const path = window.prompt("工作目录（产物会自动保存到这里）", chat.workdir || "");
                    if (path === null) return;
                    try {
                      await api.patchChat(chat.id, { workdir: path.trim() });
                      setError("");
                      if (onSent) onSent();
                    } catch (err) { setError(`工作目录保存失败：${err.message || "请重试"}`); }
                  }}>
            📁 {chat.workdir ? chat.workdir.split(/[\\/]/).pop() : "设定工作目录"}
          </button>
          <span className="spacer" />
          <span className="hint">@ 可提及成员</span>
        </div>
        <div className="composer-inner">
          <textarea
            aria-label="任务内容"
            rows={1}
            value={text}
            placeholder="发任务，或随便聊两句…  (Enter 发送 / Shift+Enter 换行)"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
                e.preventDefault(); send();
              }
            }}
          />
          <button className="btn primary" onClick={send} disabled={busy || !text.trim()}>
            {busy ? "发送中…" : "发送"}
          </button>
        </div>
        <div className="hint">描述目标与交付要求，执行进度会自动更新。</div>
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

export default ChatView;
