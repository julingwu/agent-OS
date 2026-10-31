import React, { useState, useEffect, useRef } from "react";
import api from "../api.js";
import { cls, Icon, STATE_LABEL, STATE_CLASS } from "./common.jsx";
import { PREVIEWABLE, PreviewModal } from "./ArtifactsView.jsx";
import ProjectFlow from "../ProjectFlow.jsx";
import { ProjectRecovery } from "../GovernanceView.jsx";

export function TaskCard({ taskId, meta, onAct, onApproval, compact = false }) {
  const [view, setView] = useState(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [pollVersion, setPollVersion] = useState(0);
  const [confirmation, setConfirmation] = useState(null);
  const currentTask = useRef(taskId);
  currentTask.current = taskId;
  const stateLabels = { todo: "待分配", assigned: "已分配", in_progress: "执行中",
    pending_review: "待验收", completed: "已完成", rejected: "已驳回", failed: "执行失败", cancelled: "已中止" };

  useEffect(() => {
    setView(null); setPreview(null); setError(""); setLoadError(""); setBusy(false); setConfirmation(null);
  }, [taskId]);

  useEffect(() => {
    let alive = true, timer = null, pending = false;
    const load = async () => {
      if (!alive || pending) return;
      if (timer) clearTimeout(timer);
      pending = true;
      try {
        const v = await api.task(taskId);
        if (!alive) return;
        setView(v); setLoadError("");
        if (!["completed", "cancelled", "rejected", "failed"].includes(v.task?.state)) {
          timer = setTimeout(load, 3000);
        }
      } catch (e) {
        if (alive) setLoadError(`任务加载失败：${e.message || "请重新加载"}`);
      } finally { pending = false; }
    };
    load();
    window.addEventListener("focus", load);
    window.addEventListener("oa-tasks-changed", load);
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      window.removeEventListener("focus", load);
      window.removeEventListener("oa-tasks-changed", load);
    };
  }, [taskId, pollVersion]);

  const criteria = view?.criteria || meta?.criteria || [];
  const run = view?.runs?.[0];
  const state = view?.task?.state || meta?.state;
  const stream = view?.stream || [];
  const thinking = stream.filter((s) => s.kind === "thinking").map((s) => s.text).join("");
  const tools = stream.filter((s) => s.kind === "tool");
  const artifacts = view?.artifacts === undefined ? (meta?.artifact ? [meta.artifact] : []) : view.artifacts;

  async function act(action, note = "") {
    if (busy) return false;
    setBusy(true); setError("");
    try {
      await (action === "retry" ? api.retryTask(taskId, note) : api.action(taskId, action, note));
      const v = await api.task(taskId);
      if (currentTask.current !== taskId) return false;
      setView(v);
      setPollVersion(value => value + 1);
      onAct && onAct();
      return true;
    } catch (e) {
      if (currentTask.current === taskId) setError(`操作失败：${e.message || "请重试"}`);
      return false;
    } finally { if (currentTask.current === taskId) setBusy(false); }
  }

  function openConfirmation(action) {
    setError("");
    setConfirmation({ action, note: "" });
  }

  function closeConfirmation() {
    if (busy) return;
    setConfirmation(null); setError("");
  }

  async function confirmAction(event) {
    event.preventDefault();
    if (!confirmation || busy) return;
    const note = confirmation.note.trim();
    if (confirmation.action === "reject" && !note) {
      setError("请填写驳回原因，说明需要修改的内容。");
      return;
    }
    if (await act(confirmation.action, note)) setConfirmation(null);
  }

  const waitingForApproval = view?.task?.awaiting_approval ?? view?.awaiting_approval ?? false;
  const pendingApproval = view?.task?.pending_approval ?? view?.pending_approval;
  const running = run && ["spawned", "running"].includes(run.state) && !waitingForApproval;
  const allowedActions = view?.task?.allowed_actions ?? view?.allowed_actions;
  const allowed = (action, fallback) => !!view && (Array.isArray(allowedActions) ? allowedActions.includes(action) : fallback);
  const canAccept = allowed("accept", state === "pending_review");
  const canReject = allowed("reject", state === "pending_review");
  const canAbort = allowed("abort", state === "in_progress");
  const canRetry = allowed("retry", ["rejected", "failed"].includes(state));

  return (
    <div className="card">
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h4 style={{ margin: "0 0 4px", overflowWrap: "anywhere" }}>{compact ? "验收与交付" : view?.task?.title || "任务执行"}</h4>
          {!compact && <div className="hint mono" style={{ textAlign: "left", margin: 0, fontSize: 10.5 }}>{taskId}</div>}
        </div>
        <span className={cls("pill", waitingForApproval ? "warn" : state === "completed" ? "ok" : ["rejected", "failed"].includes(state) ? "bad" :
          ["in_progress", "pending_review"].includes(state) ? "run" : "warn")}>
          {waitingForApproval ? "待审批" : stateLabels[state] || state || "载入中"}
        </span>
      </div>
      {run && <div className="hint" style={{ textAlign: "left", margin: "8px 0" }}>
        {running && <span style={{ marginLeft: 8 }}><span className="spin" /> {run.usage_tokens || 0} tok</span>}
        {!running && run && <span style={{ marginLeft: 8, color: "var(--muted)" }}>
          {run.usage_tokens || 0} tokens
        </span>}
      </div>}

      {waitingForApproval && <div className="note-bar" role="status">任务已暂停，等待操作审批。{pendingApproval && onApproval && <button className="btn ghost" style={{ marginLeft: 8 }} onClick={onApproval}>前往审批中心</button>}</div>}
      {compact && (view?.requirement || view?.task?.requirement) && <details className="task-requirement"><summary>完整需求</summary><p>{view.requirement || view.task.requirement}</p></details>}

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

      {(artifacts || []).map((artifact, i) => (
        <div className="crit" key={artifact.id || i}>
          <span className="state" style={{ color: "var(--accent)" }}>📄</span>
          <span className="txt">{(artifact.file_path || artifact.name || "产物").split(/[\\/]/).pop()} · {artifact.bytes || 0} B</span>
          {artifact.id && PREVIEWABLE.includes(
            ("." + (artifact.file_path || artifact.name || "").split(".").pop()).toLowerCase()) && (
            <button className="btn ghost" style={{ marginLeft: 8 }}
                    onClick={() => setPreview({
                      id: artifact.id, name: (artifact.file_path || artifact.name || "产物").split(/[\\/]/).pop(),
                      bytes: artifact.bytes, task_id: taskId,
                    })}>预览</button>
          )}
          {artifact.id && <a className="btn ghost" href={api.fileUrl(artifact.id)} target="_blank" rel="noreferrer">下载</a>}
        </div>
      ))}
      {preview && <PreviewModal file={preview} onClose={() => setPreview(null)} />}

      {!confirmation && (error || loadError) && <div className="err" role="alert">{error || loadError}</div>}
      {loadError && <button className="btn ghost" onClick={() => setPollVersion(value => value + 1)}>重新加载</button>}
      {(canAccept || canReject || canAbort || canRetry) && (
        <div className="actions">
          {canAccept && <button className="btn primary" disabled={busy} onClick={() => openConfirmation("accept")}>验收通过</button>}
          {canReject && <button className="btn" disabled={busy} onClick={() => openConfirmation("reject")}>驳回</button>}
          {canAbort && <button className="btn ghost" disabled={busy} onClick={() => act("abort")}>中止任务</button>}
          {canRetry && <button className="btn primary" disabled={busy} onClick={() => openConfirmation("retry")}>修改后重试</button>}
        </div>
      )}
      {view && state === "completed" && <div className="hint">验收已通过，任务已完成</div>}
      {confirmation && (
        <div className="mask" onClick={closeConfirmation}>
          <form className="modal" onClick={event => event.stopPropagation()} onSubmit={confirmAction}>
            <h3>{{ accept: "确认验收", reject: "驳回任务", retry: "修改后重试" }[confirmation.action]}</h3>
            <p className="hint">{{
              accept: "确认交付内容符合预期后，完成本任务的验收。",
              reject: "写明需要修改的内容，方便下一次执行修正。",
              retry: "可补充修改要求，系统将使用原任务继续执行。",
            }[confirmation.action]}</p>
            <div className="field">
              <label htmlFor={`task-action-note-${taskId}`}>
                {{ accept: "验收说明（可选）", reject: "驳回原因", retry: "补充修改要求（可选）" }[confirmation.action]}
              </label>
              <textarea id={`task-action-note-${taskId}`} rows={4} autoFocus disabled={busy}
                required={confirmation.action === "reject"} value={confirmation.note}
                onChange={event => setConfirmation(current => ({ ...current, note: event.target.value }))}
                placeholder={confirmation.action === "reject" ? "请说明需要调整的内容…" : "补充说明…"} />
            </div>
            {error && <div className="err" role="alert">{error}</div>}
            <div className="actions">
              <button type="button" className="btn ghost" disabled={busy} onClick={closeConfirmation}>取消</button>
              <button type="submit" className="btn primary" disabled={busy || (confirmation.action === "reject" && !confirmation.note.trim())}>
                {busy ? "处理中…" : { accept: "确认验收", reject: "确认驳回", retry: "开始重试" }[confirmation.action]}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

export function ProjectCard({ projectId }) {
  const [v, setV] = useState(null);
  const [showTl, setShowTl] = useState(false);
  const [configError, setConfigError] = useState("");

  useEffect(() => {
    let alive = true;
    api.project(projectId).then((d) => { if (alive) setV(d); }).catch(() => {});
    return () => { alive = false; };
  }, [projectId]);

  useEffect(() => {
    if (v?.project.state !== "retrying") return;
    const timer = setInterval(() => api.project(projectId).then(setV).catch(() => {}), 2000);
    return () => clearInterval(timer);
  }, [projectId, v?.project.state]);

  if (!v) return <div className="card">项目 {projectId} 载入中…</div>;

  const done = v.stages.filter((s) => s.state === "done").length;
  const blocked = v.project.state === "blocked";

  return (
    <div className={cls("card", "project-card", blocked && "blocked")}>
      <h4>项目 {v.project.id} · {v.project.name}</h4>
      <div className="hint">
        {done}/{v.stages.length} 阶段完成
        {blocked ? " · ⛔ 已被否决停摆" : ""} · 闸门 {v.gates.filter((g) => !g.superseded && g.state === "passed").length}/{v.gates.filter(g => !g.superseded).length} 通过
      </div>
      <ProjectRecovery data={v} onChanged={setV} />
      <div className="stages">
        <ProjectFlow projectId={v.project.id} data={v}
                     onChanged={() => api.project(projectId).then(setV).catch(() => {})} />
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
        {v.allowed_actions?.includes("configure") && <button className="btn ghost" onClick={async () => {
          setConfigError("");
          try {
            const next = v.project.use_llm_docs ? 0 : 1;
            await api.patchProject(v.project.id, { use_llm_docs: next });
            setV(await api.project(v.project.id));
          } catch (error) { setConfigError(error.message); }
        }}>
          文档模式：{v.project.use_llm_docs ? "模型生成" : "模板（省 token）"}
        </button>}
      </div>
      {configError && <p role="alert">{configError}</p>}
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

export function TaskInspectorModal({ task, chats = [], onClose, onApproval, onAct, onOpenChat }) {
  if (!task) return null;
  return (
    <div className="mask" onClick={onClose}>
      <section className="modal wide task-inspector" onClick={(e) => e.stopPropagation()}>
        <div className="task-inspector-heading">
          <span className="eyebrow">TASK DETAILS</span>
          <button className="icon-btn" aria-label="关闭任务详情" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <h3>{task.title}</h3>
        <p className="task-inspector-meta">{task.owner_name || "AI 团队"} · {task.id}</p>
        <TaskCard compact taskId={task.id} meta={{}} onApproval={onApproval} onAct={onAct} />
        <div className="actions">
          <button className="btn danger" onClick={async () => {
            if (!confirm(`确定彻底删除任务「${task.title || task.id}」吗？关联的产物和运行轨迹将一并清理。`)) return;
            try {
              await api.deleteTask(task.id);
              onClose();
              window.dispatchEvent(new Event("oa-tasks-changed"));
              onAct && onAct();
            } catch (e) {
              alert(e.message || "删除任务失败");
            }
          }}>
            删除任务
          </button>
          <button className="btn ghost" onClick={onClose}>关闭</button>
          {chats.some((chat) => chat.id === task.chat_id) && (
            <button className="btn" onClick={() => { onOpenChat(chats.find((chat) => chat.id === task.chat_id)); onClose(); }}>
              <Icon name="chat" /> 打开协作会话
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

export default TaskCard;
