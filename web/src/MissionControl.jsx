import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { LiveStream, StageDetail } from "./ProjectFlow.jsx";
import "./mission.css";

/* ============================================================
 * 总装观测台（Mission Control）
 *
 * 设计原则（第一性）：
 *   1. 一个项目 = 一条可见的流水线。阶段轨道永远在视野中央。
 *   2. 空间语法固定：左边"有哪些项目" → 中间"这条流水线发生什么"
 *      → 右边"此刻正在发生什么"（实时流 / 闸门 / 时间线）。
 *   3. 只消费既有 /ui 接口，不新增后端契约。
 * ============================================================ */

const PROJECT_STATE = {
  active: "进行中", blocked: "已阻塞", done: "已完成", halted: "已停摆",
};

const STAGE_STATE = {
  done: "已完成", active: "进行中", pending: "待开始",
  blocked: "已失败", awaiting_budget: "待批预算", halted: "已停摆",
};

const ACTION_LABEL = {
  project_created: "项目创建",
  experience_injected: "注入组织经验",
  stage_enter: "进入阶段",
  stage_done: "阶段完成",
  stage_delivery: "阶段交付",
  gate_passed: "闸门通过",
  gate_failed: "闸门未过",
  run_started: "开始执行",
  run_finished: "执行结束",
  task_created: "派发任务",
  task_completed: "任务完成",
  budget_allocated: "预算分配",
  budget_approved: "预算追加批准",
  budget_rejected: "预算驳回",
  project_blocked: "项目阻塞",
  project_resumed: "项目恢复",
  redline_blocked: "红线拦截",
  workshop_closed: "研讨结束",
};

const basename = (p) => String(p || "").split(/[\\/]/).pop() || "";
const fmt = (n) => Number(n || 0).toLocaleString("zh-CN");
const when = (t) => String(t || "").replace("T", " ").slice(5, 16);

function actionLabel(a) {
  return ACTION_LABEL[a] || a;
}

function payloadSummary(raw) {
  try {
    const d = JSON.parse(raw || "{}");
    const bits = [];
    if (d.stage) bits.push(`阶段 ${d.stage}`);
    if (d.seq != null) bits.push(`#${d.seq}`);
    if (d.run_id) bits.push(String(d.run_id).slice(0, 16));
    if (d.note) bits.push(String(d.note).slice(0, 60));
    if (d.reason) bits.push(String(d.reason).slice(0, 60));
    if (d.name && !bits.length) bits.push(String(d.name).slice(0, 60));
    return bits.join(" · ");
  } catch {
    return "";
  }
}

/* --------------------------------------------------- 小部件 */

function Dot({ state }) {
  return <span className={`mc-dot mc-dot-${state || "pending"}`} aria-hidden />;
}

function Pill({ state, children }) {
  return <span className={`mc-pill mc-pill-${state || "pending"}`}>{children || STAGE_STATE[state] || state}</span>;
}

/* 横向阶段轨道：整页的视觉主轴 */
function StageRail({ stages, focusKey, onPick }) {
  return (
    <ol className="mc-rail" aria-label="阶段轨道">
      {stages.map((s, i) => {
        const state = s.state === "blocked" ? "blocked" : s.state;
        const isFocus = s.key === focusKey;
        return (
          <li key={s.key} className={`mc-rail-item is-${state}${isFocus ? " is-focus" : ""}`}>
            <button className="mc-node" onClick={() => onPick(s.key)}
                    title={`${s.title}（${STAGE_STATE[s.state] || s.state}）`}>
              <span className="mc-node-idx">
                {state === "done" ? "✓" : i + 1}
              </span>
              <span className="mc-node-body">
                <span className="mc-node-title">{s.title || s.key}</span>
                <span className="mc-node-meta">
                  <Dot state={state} />
                  {STAGE_STATE[s.state] || s.state}
                  {s.role_tag ? ` · ${s.role_tag}` : ""}
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/* 阶段进度条（细线，替代大卡片） */
function Meter({ label, value, total, tone }) {
  const pct = total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0;
  return (
    <div className="mc-meter">
      <div className="mc-meter-top">
        <span className="mc-meter-label">{label}</span>
        <span className="mc-meter-num">{fmt(value)}<i>/{fmt(total)}</i></span>
      </div>
      <div className="mc-meter-track">
        <div className={`mc-meter-fill tone-${tone || "accent"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Empty({ title, hint, action }) {
  return (
    <div className="mc-empty">
      <div className="mc-empty-mark" aria-hidden>◎</div>
      <h3>{title}</h3>
      {hint && <p>{hint}</p>}
      {action}
    </div>
  );
}

/* --------------------------------------------------- 主视图 */

export default function MissionControl({ Icon, onOpenChat }) {
  const [list, setList] = useState(null);          // {projects: [...]}
  const [listErr, setListErr] = useState("");
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("all");
  const [activeId, setActiveId] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailErr, setDetailErr] = useState("");
  const [stageOpen, setStageOpen] = useState("");  // 打开的阶段详情弹窗
  const [runId, setRunId] = useState("");          // 当前活跃 Run（实时流）
  const [tab, setTab] = useState("gates");         // 右侧面板：gates | timeline | files
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [releaseData, setReleaseData] = useState(null);
  const [previewFile, setPreviewFile] = useState(null);
  const alive = useRef(true);

  /* -- 项目列表 -- */
  const loadList = useCallback(async (keepActive = true) => {
    try {
      const d = await api.projects();
      if (!alive.current) return;
      const items = d.projects || [];
      setList({ projects: items });
      setListErr("");
      if (!keepActive || !activeId) {
        // 默认落在"最有内容"的项目上：阶段推进最多的那个，其次才是进行中的
        const scored = [...items].sort((a, b) => {
          const rank = (p) => (p.done_stages || 0) * 10 + (p.state === "active" ? 5 : p.state === "blocked" ? 3 : 1);
          return rank(b) - rank(a);
        });
        const first = scored[0];
        if (first) setActiveId((cur) => cur || first.id);
      }
    } catch (e) {
      if (alive.current) setListErr(e.message || "项目列表加载失败");
    }
  }, [activeId]);

  /* -- 项目详情 -- */
  const loadDetail = useCallback(async (id) => {
    if (!id) return;
    try {
      const d = await api.project(id);
      if (!alive.current) return;
      setDetail(d);
      setDetailErr("");
    } catch (e) {
      if (alive.current) { setDetail(null); setDetailErr(e.message || "项目详情加载失败"); }
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    loadList(false);
    const onChanged = () => loadList(true);
    window.addEventListener("oa-tasks-changed", onChanged);
    return () => {
      alive.current = false;
      window.removeEventListener("oa-tasks-changed", onChanged);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { if (activeId) loadDetail(activeId); }, [activeId, loadDetail]);

  useEffect(() => {
    if (!activeId) { setReleaseData(null); return; }
    api.releaseFiles(activeId).then(setReleaseData).catch(() => setReleaseData(null));
  }, [activeId, detail]);

  /* 进行中的项目每 6s 拉一次详情，保证轨道状态不落后 */
  useEffect(() => {
    if (!activeId || !detail) return undefined;
    if (detail.project?.state !== "active") return undefined;
    const t = setInterval(() => loadDetail(activeId), 6000);
    return () => clearInterval(t);
  }, [activeId, detail?.project?.state, loadDetail]);

  const stages = detail?.stages || [];
  const gates = detail?.gates || [];
  const deliverables = detail?.deliverables || [];
  const timeline = detail?.timeline || [];
  const budget = detail?.budget;

  const activeStage = useMemo(() => stages.find((s) => s.state === "active"), [stages]);
  const focusStage = activeStage
    || [...stages].reverse().find((s) => s.state === "blocked")
    || [...stages].reverse().find((s) => s.state === "done")
    || stages[0];

  /* 活跃阶段 → 它的 Run → 实时流 */
  useEffect(() => {
    if (!activeId || !activeStage) { setRunId(""); return undefined; }
    let stop = false;
    api.stageDetail(activeId, activeStage.key)
      .then((d) => { if (!stop) setRunId(d.runs?.[0]?.run_id || ""); })
      .catch(() => { if (!stop) setRunId(""); });
    return () => { stop = true; };
  }, [activeId, activeStage?.key, activeStage?.state]);

  const projects = list?.projects || [];
  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return projects.filter((p) => {
      if (filter !== "all" && p.state !== filter) return false;
      if (!kw) return true;
      return `${p.name} ${p.requirement} ${p.id}`.toLowerCase().includes(kw);
    });
  }, [projects, q, filter]);

  const doneStages = stages.filter((s) => s.state === "done").length;
  const passedGates = gates.filter((g) => g.state === "passed" && !g.superseded).length;
  const blockedGates = gates.filter((g) => g.state !== "passed" && !g.superseded).length;

  async function act(kind) {
    if (!detail || busy) return;
    const pid = detail.project.id;
    setBusy(true); setToast("");
    try {
      if (kind === "retry") {
        const firstBlocked = stages.find((s) => s.state === "blocked");
        const wbsTask = (detail.wbs || []).find((w) => w.task_id && w.state !== "done");
        const tid = wbsTask?.task_id;
        if (tid) { await api.retryTask(tid, "观测台重试"); setToast("已提交重试"); }
        else setToast(firstBlocked ? `阶段 ${firstBlocked.title} 无关联任务，请在阶段详情内处理` : "没有可重试的任务");
      } else if (kind === "resume") {
        await api.resumeProject(pid);
        setToast("已提交恢复");
      }
      await loadDetail(pid);
      await loadList(true);
      window.dispatchEvent(new Event("oa-tasks-changed"));
    } catch (e) {
      setToast(e.message || "操作失败");
    } finally {
      setBusy(false);
      setTimeout(() => setToast(""), 4000);
    }
  }

  return (
    <section className="main mc-root" aria-label="总装观测台">
      {/* ---------- 顶部：一句话说清现在有多少活在跑 ---------- */}
      <header className="mc-hero">
        <div className="mc-hero-main">
          <span className="mc-eyebrow">MISSION CONTROL</span>
          <h1>总装流水线</h1>
          <p>
            {projects.length} 个项目 ·{" "}
            {projects.filter((p) => p.state === "active").length} 个正在跑 ·{" "}
            {projects.filter((p) => p.state === "blocked").length} 个卡住
          </p>
        </div>
        <div className="mc-hero-stats">
          <div className="mc-stat">
            <span className="mc-stat-num">{projects.filter((p) => p.state === "active").length}</span>
            <span className="mc-stat-label">进行中</span>
          </div>
          <div className="mc-stat mc-stat-warn">
            <span className="mc-stat-num">{projects.filter((p) => p.state === "blocked").length}</span>
            <span className="mc-stat-label">待处理阻塞</span>
          </div>
          <div className="mc-stat mc-stat-ok">
            <span className="mc-stat-num">{projects.filter((p) => p.state === "done").length}</span>
            <span className="mc-stat-label">已交付</span>
          </div>
        </div>
      </header>

      <div className="mc-grid">
        {/* ---------- 左：项目列表 ---------- */}
        <aside className="mc-col mc-list" aria-label="项目列表">
          <div className="mc-search">
            <Icon name="search" />
            <input value={q} onChange={(e) => setQ(e.target.value)}
                   placeholder="搜索项目、需求或编号" aria-label="搜索项目" />
            {q && <button className="mc-search-x" onClick={() => setQ("")} aria-label="清空">✕</button>}
          </div>
          <div className="mc-filters" role="tablist" aria-label="按状态筛选">
            {[["all", "全部"], ["active", "进行中"], ["blocked", "阻塞"], ["done", "已完成"]].map(([k, label]) => (
              <button key={k} role="tab" aria-selected={filter === k}
                      className={filter === k ? "on" : ""} onClick={() => setFilter(k)}>
                {label}
              </button>
            ))}
          </div>

          {listErr && <div className="mc-alert" role="alert">{listErr}</div>}
          {!list && !listErr && <div className="mc-skel-list" aria-hidden>
            {[0, 1, 2, 3].map((i) => <div className="mc-skel" key={i} />)}
          </div>}

          <ul className="mc-projects">
            {filtered.map((p) => {
              const total = 9;
              const pct = Math.round(((p.done_stages || 0) / total) * 100);
              return (
                <li key={p.id}>
                  <button className={`mc-project${p.id === activeId ? " is-active" : ""}`}
                          onClick={() => setActiveId(p.id)}>
                    <span className="mc-project-top">
                      <Dot state={p.state === "done" ? "done" : p.state === "blocked" ? "blocked" : "active"} />
                      <span className="mc-project-name">{p.name}</span>
                    </span>
                    <span className="mc-project-sub">
                      {p.id} · {PROJECT_STATE[p.state] || p.state}
                      {p.flow_id ? ` · ${p.flow_id}` : ""}
                    </span>
                    <span className="mc-project-bar" aria-hidden>
                      <span style={{ width: `${pct}%` }} />
                    </span>
                    <span className="mc-project-foot">
                      <span>{p.done_stages || 0}/9 阶段</span>
                      <span>{when(p.created_at)}</span>
                    </span>
                  </button>
                </li>
              );
            })}
            {list && !filtered.length && (
              <li className="mc-project-none">没有匹配的项目</li>
            )}
          </ul>
        </aside>

        {/* ---------- 中：流水线画布 ---------- */}
        <main className="mc-col mc-canvas" aria-label="流水线画布">
          {detailErr && <div className="mc-alert" role="alert">{detailErr}</div>}
          {!detail && !detailErr && <div className="mc-skel-canvas" aria-hidden />}
          {detail && (
            <>
              <div className="mc-proj-head">
                <div className="mc-proj-title">
                  <div className="mc-proj-meta">
                    <Pill state={detail.project.state === "done" ? "done" : detail.project.state === "blocked" ? "blocked" : "active"}>
                      {PROJECT_STATE[detail.project.state] || detail.project.state}
                    </Pill>
                    <span className="mc-mono">{detail.project.id}</span>
                    {detail.project.flow_id && <span className="mc-tag">{detail.project.flow_id}</span>}
                  </div>
                  <h2>{detail.project.name}</h2>
                  <p className="mc-requirement">{detail.project.requirement}</p>
                </div>
                <div className="mc-proj-actions">
                  {(detail.allowed_actions || []).includes("retry") && (
                    <button className="mc-btn" disabled={busy} onClick={() => act("retry")}>
                      <Icon name="refresh" /> 重试
                    </button>
                  )}
                  {(detail.allowed_actions || []).includes("resume") && (
                    <button className="mc-btn primary" disabled={busy} onClick={() => act("resume")}>
                      <Icon name="play" /> 恢复
                    </button>
                  )}
                </div>
              </div>

              {toast && <div className="mc-toast" role="status">{toast}</div>}

              {/* 阶段轨道 —— 全页视觉主轴 */}
              <div className="mc-rail-wrap">
                <div className="mc-rail-head">
                  <span className="mc-rail-title">阶段轨道</span>
                  <span className="mc-rail-note">
                    {doneStages}/{stages.length} 阶段完成 · {passedGates} 闸门通过
                    {blockedGates ? ` · ${blockedGates} 待解` : ""} · 点击阶段看交付物与审计
                  </span>
                </div>
                {stages.length ? (
                  <StageRail stages={stages} focusKey={focusStage?.key} onPick={setStageOpen} />
                ) : (
                  <div className="mc-rail-empty">
                    这个项目还没有进入流水线（没有阶段记录）。
                    {detail.project.flow_id ? ` 流程模板 ${detail.project.flow_id} 尚未实例化。` : " 它还没有绑定流程模板。"}
                  </div>
                )}
              </div>

              {/* 三条细进度，替掉一排大卡片 */}
              <div className="mc-meters">
                <Meter label="阶段" value={doneStages} total={stages.length || 9} tone="accent" />
                <Meter label="闸门" value={passedGates} total={gates.length || 12} tone="ok" />
                <Meter label="交付物" value={deliverables.length} total={Math.max(deliverables.length, stages.length)} tone="blue" />
              </div>

              {/* 当前焦点阶段 */}
              {focusStage && (
                <div className="mc-focus">
                  <div className="mc-focus-head">
                    <span className="mc-focus-idx">{focusStage.seq + 1}</span>
                    <div>
                      <h3>{focusStage.title || focusStage.key}</h3>
                      <p>
                        <Pill state={focusStage.state} />
                        {focusStage.role_tag ? <span className="mc-tag">{focusStage.role_tag}</span> : null}
                        {focusStage.started_at ? <span className="mc-hint">开始 {when(focusStage.started_at)}</span> : null}
                        {focusStage.ended_at ? <span className="mc-hint">结束 {when(focusStage.ended_at)}</span> : null}
                      </p>
                    </div>
                    <button className="mc-btn ghost" onClick={() => setStageOpen(focusStage.key)}>
                      阶段详情 <Icon name="chevron" />
                    </button>
                  </div>
                  {activeStage && runId && (
                    <LiveStream runId={runId} onFinished={() => { loadDetail(activeId); loadList(true); }} />
                  )}
                  {!activeStage && (
                    <p className="mc-focus-idle">
                      当前没有正在执行的阶段。轨道上点任意阶段，可查看它的交付物、闸门、黑板与历史 Run。
                    </p>
                  )}
                </div>
              )}

              {/* 任务分解（WBS） */}
              <div className="mc-section">
                <div className="mc-section-head">
                  <h4>任务分解</h4>
                  <span className="mc-hint">{(detail.wbs || []).length} 项</span>
                </div>
                {(detail.wbs || []).length ? (
                  <ul className="mc-wbs">
                    {detail.wbs.map((w) => (
                      <li key={w.id} className={`is-${w.state}`}>
                        <span className="mc-wbs-code">{w.code}</span>
                        <span className="mc-wbs-title">{w.title}</span>
                        {w.role_tag && <span className="mc-tag">{w.role_tag}</span>}
                        {w.pack_id && <span className="mc-tag muted">{w.pack_id}</span>}
                        <span className={`mc-wbs-state is-${w.state}`}>{w.state}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mc-hint">这个项目还没有任务分解（WBS）记录。</p>
                )}
              </div>

              {/* 出厂交付包（Release Bundle） */}
              <ReleaseCard pid={activeId} releaseData={releaseData} onPreview={setPreviewFile} />
            </>
          )}
        </main>

        {/* ---------- 右：此刻正在发生什么 ---------- */}
        <aside className="mc-col mc-side" aria-label="项目观测面板">
          {!detail && <div className="mc-skel-side" aria-hidden />}
          {detail && (
            <>
              <div className="mc-side-head">
                <div className="mc-tabs" role="tablist">
                  {[["gates", `闸门 ${passedGates}/${gates.length}`],
                    ["files", `交付物 ${deliverables.length}`],
                    ["timeline", "时间线"]].map(([k, label]) => (
                    <button key={k} role="tab" aria-selected={tab === k}
                            className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="mc-side-body">
                {tab === "gates" && (
                  <>
                    {gates.length ? (
                      <ul className="mc-gates">
                        {gates.map((g) => (
                          <li key={g.id} className={`is-${g.state}${g.superseded ? " is-superseded" : ""}`}>
                            <span className={`mc-gate-mark is-${g.state}`} aria-hidden>
                              {g.state === "passed" ? "✓" : "!"}
                            </span>
                            <span className="mc-gate-body">
                              <span className="mc-gate-name">{g.name}</span>
                              <span className="mc-gate-meta">
                                {g.stage_key} · {g.kind === "auto" ? "自动" : g.kind}
                                {g.required_role ? ` · 需 ${g.required_role}` : ""}
                              </span>
                              {g.note && <span className="mc-gate-note">{g.note}</span>}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : <p className="mc-hint">还没有闸门记录。</p>}
                  </>
                )}

                {tab === "files" && (
                  <>
                    {deliverables.length ? (
                      <ul className="mc-files">
                        {deliverables.map((d) => (
                          <li key={d.id}>
                            <span className="mc-file-kind">{d.kind}</span>
                            <span className="mc-file-name" title={d.file_path}>{basename(d.file_path)}</span>
                            <span className="mc-file-size">{fmt(d.bytes)} B</span>
                            <a className="mc-file-dl" href={api.stageFileUrl(detail.project.id, d.stage_key, d.id)}>
                              下载
                            </a>
                          </li>
                        ))}
                      </ul>
                    ) : <p className="mc-hint">还没有交付物。</p>}
                  </>
                )}

                {tab === "timeline" && (
                  <>
                    {timeline.length ? (
                      <ol className="mc-timeline">
                        {[...timeline].reverse().slice(0, 40).map((t, i) => (
                          <li key={i}>
                            <span className={`mc-tl-mark${t.action.includes("block") || t.action.includes("failed") ? " bad" : ""}`} aria-hidden />
                            <span className="mc-tl-body">
                              <span className="mc-tl-act">{actionLabel(t.action)}</span>
                              <span className="mc-tl-time">{when(t.created_at)}</span>
                              {payloadSummary(t.payload) && (
                                <span className="mc-tl-note">{payloadSummary(t.payload)}</span>
                              )}
                            </span>
                          </li>
                        ))}
                      </ol>
                    ) : <p className="mc-hint">还没有时间线记录。</p>}
                  </>
                )}
              </div>

              {budget && budget.pool > 0 && (
                <div className="mc-budget">
                  <div className="mc-budget-head">
                    <span>预算池</span>
                    <b>{fmt(budget.spent)} / {fmt(budget.pool)}</b>
                  </div>
                  <div className="mc-budget-track">
                    <div className={budget.spent / budget.pool >= 0.8 ? "warn" : ""}
                         style={{ width: `${Math.min(100, Math.round((budget.spent / budget.pool) * 100))}%` }} />
                  </div>
                  <div className="mc-budget-foot">
                    剩余 {fmt(budget.remaining)} · 80% 暂停线 · 弹性 {budget.elastic}
                  </div>
                </div>
              )}

              {onOpenChat && detail.project.chat_id && (
                <button className="mc-side-link" onClick={() => onOpenChat(detail.project.chat_id)}>
                  打开这个项目的协作会话 <Icon name="chat" />
                </button>
              )}
            </>
          )}
        </aside>
      </div>

      {stageOpen && detail && (
        <StageDetail projectId={detail.project.id} stageKey={stageOpen}
                     onClose={() => setStageOpen("")}
                     onChanged={() => { loadDetail(activeId); loadList(true); }} />
      )}
    </section>
  );
}
