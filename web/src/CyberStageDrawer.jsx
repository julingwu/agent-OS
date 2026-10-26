import React, { useState, useEffect, useRef, useCallback } from "react";
import { api } from "./api.js";

// Terminal-grade Auto-scrolling Live Trace Stream
function CyberLiveStream({ runId, onFinished }) {
  const [lines, setLines] = useState([]);
  const [runState, setRunState] = useState("running");
  const scrollRef = useRef(null);

  useEffect(() => {
    if (!runId) return undefined;
    setLines([]);
    setRunState("running");

    let es;
    try {
      es = new EventSource(api.streamUrl(`/runs/${runId}/live-stream`));
    } catch (e) {
      return undefined;
    }

    const push = (kind, label) => (ev) => {
      let d = {};
      try { d = JSON.parse(ev.data || "{}"); } catch { d = {}; }
      setLines((prev) => [...prev, { kind, label, time: new Date().toLocaleTimeString(), ...d }].slice(-250));
    };

    es.addEventListener("thinking", push("thinking", "思考"));
    es.addEventListener("tool_call", push("tool", "工具调用"));
    es.addEventListener("tool_result", push("result", "工具结果"));
    es.addEventListener("model", push("model", "模型输出"));
    es.addEventListener("span", push("span", "跨度跟踪"));
    es.addEventListener("status", (ev) => {
      let d = {};
      try { d = JSON.parse(ev.data || "{}"); } catch { d = {}; }
      const s = d.state || "";
      setRunState(s);
      if (s === "finished" || s === "completed" || s === "blocked" || s === "failed") {
        es.close();
        onFinished?.();
      }
    });

    es.onerror = () => {
      es.close();
    };

    return () => {
      if (es) es.close();
    };
  }, [runId, onFinished]);

  // Terminal Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines]);

  return (
    <div className="cyber-terminal-card">
      <div className="terminal-header">
        <div className="terminal-dots">
          <span className="dot red" />
          <span className="dot yellow" />
          <span className="dot green" />
        </div>
        <span className="terminal-title">LIVE RUN TRACE // {runId.slice(0, 16)}</span>
        <span className={`terminal-status-tag ${runState}`}>{runState.toUpperCase()}</span>
      </div>
      <div className="terminal-body" ref={scrollRef}>
        {lines.length === 0 ? (
          <div className="terminal-waiting">
            <span className="pulse-dot" /> 正在监听 Agent 执行流与实时思考/工具调用...
          </div>
        ) : (
          lines.map((l, i) => (
            <div key={i} className={`terminal-line ${l.kind}`}>
              <span className="line-time">[{l.time}]</span>
              <span className={`line-label tag-${l.kind}`}>[{l.label || l.kind}]</span>
              <span className="line-content">
                {l.text || l.detail || l.content || (l.tool ? `${l.tool} (${JSON.stringify(l.input || {})})` : "") || JSON.stringify(l)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default function CyberStageDrawer({
  stage,
  projectId,
  projectDetail,
  onClose,
  onOpenChat,
  onOpenExchange,
  triggerCrystalDrop,
  onRefreshProject,
}) {
  const [stageData, setStageData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("overview"); // overview | files | gates | runs
  const [releaseFiles, setReleaseFiles] = useState([]);
  const [previewContent, setPreviewContent] = useState(null);
  const [previewTitle, setPreviewTitle] = useState("");
  const [actionMsg, setActionMsg] = useState("");
  const [resuming, setResuming] = useState(false);

  const stageKey = stage?.key || "intake";

  // Load stage details from backend
  const loadStageDetail = useCallback(async () => {
    if (!projectId || !stageKey) return;
    setLoading(true);
    try {
      const data = await api.stageDetail(projectId, stageKey);
      setStageData(data);
    } catch (e) {
      setStageData({
        project_id: projectId,
        stage: { key: stageKey, name: stage?.name, state: stage?.state || "pending" },
        deliverables: [],
        gates: [],
        runs: [],
        audits: [],
      });
    } finally {
      setLoading(false);
    }
  }, [projectId, stageKey, stage]);

  useEffect(() => {
    loadStageDetail();
  }, [loadStageDetail]);

  // Load release files if release stage
  useEffect(() => {
    if (stageKey === "release" && projectId) {
      api.releaseFiles(projectId)
        .then((res) => {
          if (res.ok) setReleaseFiles(res.files || []);
        })
        .catch(() => {});
    }
  }, [stageKey, projectId]);

  // Resume project if blocked
  const handleResume = async () => {
    setResuming(true);
    try {
      await api.resumeProject(projectId);
      setActionMsg("已触发续跑指令！后台调度器正在接力推进流水线。");
      onRefreshProject?.();
      setTimeout(loadStageDetail, 1000);
      setTimeout(() => setActionMsg(""), 5000);
    } catch (err) {
      alert("恢复失败: " + err.message);
    } finally {
      setResuming(false);
    }
  };

  // Preview file
  const handlePreviewFile = async (deliv) => {
    try {
      const url = api.stageFilePreviewUrl(projectId, stageKey, deliv.id);
      const res = await fetch(url);
      const text = await res.text();
      setPreviewTitle(deliv.file_path || "物料预览");
      setPreviewContent(text);
    } catch (e) {
      alert("预览失败: " + e.message);
    }
  };

  const handlePreviewRelease = async (fileName) => {
    try {
      const url = api.releasePreviewUrl(projectId, fileName);
      const res = await fetch(url);
      const text = await res.text();
      setPreviewTitle(fileName);
      setPreviewContent(text);
    } catch (e) {
      alert("预览失败: " + e.message);
    }
  };

  const activeRunId = stageData?.runs?.[0]?.run_id;

  const stateColor =
    stage?.state === "done"
      ? "#00FF9D"
      : stage?.state === "active"
      ? "#00F0FF"
      : stage?.state === "blocked"
      ? "#FF0055"
      : "#64748B";

  const stateLabel =
    stage?.state === "done"
      ? "✓ 已就绪 / DONE"
      : stage?.state === "active"
      ? "● 运行中 / ACTIVE"
      : stage?.state === "blocked"
      ? "✗ 闸门拦截 / BLOCKED"
      : "○ 等待激活 / PENDING";

  return (
    <div className="cyber-stage-drawer-backdrop" onClick={onClose}>
      <aside className="cyber-stage-drawer-panel" onClick={(e) => e.stopPropagation()}>
        {/* 1. DRAWER TOP HEADER */}
        <header className="drawer-header" style={{ borderBottomColor: stage.color }}>
          <div className="drawer-header-left">
            <span className="drawer-code-tag" style={{ background: `${stage.color}20`, color: stage.color, borderColor: stage.color }}>
              STAGE {stage.code}
            </span>
            <div>
              <h3 className="drawer-stage-title" style={{ color: stage.color }}>
                {stage.name} <span className="drawer-stage-key">({stage.key.toUpperCase()})</span>
              </h3>
              <p className="drawer-stage-desc">{stage.desc}</p>
            </div>
          </div>
          <div className="drawer-header-right">
            <span className="drawer-state-pill" style={{ borderColor: stateColor, color: stateColor }}>
              {stateLabel}
            </span>
            <button className="cyber-close-btn" onClick={onClose} aria-label="关闭抽屉">
              ✕
            </button>
          </div>
        </header>

        {/* 2. TAB NAVIGATION */}
        <nav className="drawer-nav">
          <button
            className={`drawer-nav-item ${activeTab === "overview" ? "active" : ""}`}
            onClick={() => setActiveTab("overview")}
          >
            ⚡ 阶段沙盘与实况
          </button>
          <button
            className={`drawer-nav-item ${activeTab === "files" ? "active" : ""}`}
            onClick={() => setActiveTab("files")}
          >
            📦 物料产出 ({stageData?.deliverables?.length || (stageKey === "release" ? releaseFiles.length : 0)})
          </button>
          <button
            className={`drawer-nav-item ${activeTab === "gates" ? "active" : ""}`}
            onClick={() => setActiveTab("gates")}
          >
            🛡️ 质检与审计 ({stageData?.gates?.length || 0})
          </button>
          <button
            className={`drawer-nav-item ${activeTab === "runs" ? "active" : ""}`}
            onClick={() => setActiveTab("runs")}
          >
            🔥 算力与追踪 ({stageData?.runs?.length || 0})
          </button>
        </nav>

        {/* 3. DRAWER BODY CONTENT */}
        <main className="drawer-body">
          {loading && (
            <div className="drawer-loading-bar">
              <span className="spin" /> 正在从工作空间提取实时阶段数据...
            </div>
          )}

          {actionMsg && (
            <div className="drawer-action-banner">
              <span>⚡</span> {actionMsg}
            </div>
          )}

          {/* Blocked state recovery action */}
          {stage?.state === "blocked" && (
            <div className="drawer-blocked-alert">
              <div>
                <b>🚨 阶段受到闸门阻断 (Gate Blocked)</b>
                <p style={{ margin: "4px 0 0", fontSize: "0.75rem", color: "#FCA5A5" }}>
                  当前阶段已被安全挂起。请检查“质检与审计”中的拦截原因，排除风险后可一键续跑。
                </p>
              </div>
              <button className="cyber-btn" style={{ background: "#FF0055", color: "#FFF", borderColor: "#FF0055" }} disabled={resuming} onClick={handleResume}>
                {resuming ? "正在续跑..." : "⚡ 一键解除阻塞并恢复"}
              </button>
            </div>
          )}

          {/* TAB 1: OVERVIEW & REAL-TIME TRACE */}
          {activeTab === "overview" && (
            <div className="drawer-tab-content">
              {/* REAL LIVE STREAM TRACE */}
              {activeRunId ? (
                <div>
                  <h4 className="drawer-sec-title">📡 实时执行流追踪 (Live Scrolling Trace)</h4>
                  <CyberLiveStream runId={activeRunId} onFinished={loadStageDetail} />
                </div>
              ) : null}

              {/* STAGE METADATA SECTION */}
              <div className="drawer-section">
                <h4 className="drawer-sec-title">📋 阶段规格与运行时上下文</h4>
                <div className="drawer-card">
                  <div className="field-label">所属项目与当前需求</div>
                  <div className="field-code-box">
                    {projectDetail?.project?.requirement || "无需求描述"}
                  </div>
                </div>

                <div className="drawer-metrics-grid" style={{ marginTop: "10px" }}>
                  <div className="drawer-stat-card">
                    <span className="stat-label">阶段编号</span>
                    <span className="stat-value cyan">STAGE {stage.code}</span>
                  </div>
                  <div className="drawer-stat-card">
                    <span className="stat-label">阶段状态</span>
                    <span className="stat-value" style={{ color: stateColor }}>{stage.state?.toUpperCase()}</span>
                  </div>
                  <div className="drawer-stat-card">
                    <span className="stat-label">物料产出数</span>
                    <span className="stat-value">{stageData?.deliverables?.length || (stageKey === "release" ? releaseFiles.length : 0)} 个</span>
                  </div>
                  <div className="drawer-stat-card">
                    <span className="stat-label">质检闸门数</span>
                    <span className="stat-value">{stageData?.gates?.length || 0} 项</span>
                  </div>
                </div>
              </div>

              {/* Special Stage 08 Release Download Port */}
              {stageKey === "release" && (
                <div className="drawer-section">
                  <h4 className="drawer-sec-title" style={{ color: "#00F0FF" }}>📦 出货港 Bundle.zip 交付面板</h4>
                  <div className="drawer-card">
                    <p style={{ margin: "0 0 10px", color: "#CBD5E1" }}>
                      流水线各阶段物料已自动总装打包为完整的 <b>Bundle.zip</b>。包含全量业务代码、测试脚本、环境配置与依赖物料。
                    </p>
                    <a
                      href={`/ui/projects/${projectId}/release/zip`}
                      className="cyber-btn"
                      download
                      style={{ display: "inline-flex", textDecoration: "none", width: "100%", justifyContent: "center" }}
                    >
                      <span>📦</span> 一键下载 {projectId}_release.zip
                    </a>
                  </div>
                </div>
              )}

              {/* Special Stage 09 Retro Crystal Vault */}
              {stageKey === "retro" && (
                <div className="drawer-section">
                  <h4 className="drawer-sec-title" style={{ color: "#A855F7" }}>💎 组织避坑晶石库与神装反哺</h4>
                  <div className="drawer-card" style={{ borderColor: "#A855F7" }}>
                    <p style={{ color: "#E9D5FF", margin: "0 0 8px" }}>
                      任务研讨共识与各阶段质检拦截经验已自动沉淀入知识库，并反哺注入后续 Prompt。
                    </p>
                    <button className="cyber-btn purple" style={{ width: "100%", justifyContent: "center" }} onClick={onOpenExchange}>
                      <span>🌐</span> 打包导出为 .metacorp.json 神装图纸
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* TAB 2: DELIVERABLES */}
          {activeTab === "files" && (
            <div className="drawer-tab-content">
              <h4 className="drawer-sec-title">📦 阶段工作物料清单</h4>

              {/* Normal stage deliverables */}
              {(stageData?.deliverables || []).length > 0 && (
                <div className="deliv-list">
                  {stageData.deliverables.map((deliv) => (
                    <div key={deliv.id} className="deliv-card">
                      <div className="deliv-left">
                        <span className="deliv-icon">📄</span>
                        <div>
                          <div className="deliv-path">{String(deliv.file_path).split(/[\\/]/).pop()}</div>
                          <div className="deliv-meta">
                            {deliv.bytes ? `${(deliv.bytes / 1024).toFixed(1)} KB` : "0 KB"} · {deliv.created_at?.slice(0, 16) || ""}
                          </div>
                        </div>
                      </div>
                      <div className="deliv-actions">
                        <button className="cyber-btn" onClick={() => handlePreviewFile(deliv)}>
                          在线预览
                        </button>
                        <a
                          href={api.stageFileUrl(projectId, stageKey, deliv.id)}
                          className="cyber-btn"
                          download
                          style={{ textDecoration: "none" }}
                        >
                          下载
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Release stage extra files */}
              {stageKey === "release" && releaseFiles.length > 0 && (
                <div className="deliv-list" style={{ marginTop: "12px" }}>
                  <div className="field-label">出厂交付包归档文件列表 (release/)</div>
                  {releaseFiles.map((rf) => (
                    <div key={rf.name} className="deliv-card">
                      <div className="deliv-left">
                        <span className="deliv-icon">{rf.ext === ".html" ? "🌐" : rf.ext === ".py" ? "🐍" : "📦"}</span>
                        <div>
                          <div className="deliv-path">{rf.name}</div>
                          <div className="deliv-meta">{(rf.bytes / 1024).toFixed(1)} KB</div>
                        </div>
                      </div>
                      <div className="deliv-actions">
                        {rf.is_previewable && (
                          <button className="cyber-btn" onClick={() => handlePreviewRelease(rf.name)}>
                            预览
                          </button>
                        )}
                        <a href={api.releasePreviewUrl(projectId, rf.name)} className="cyber-btn" download style={{ textDecoration: "none" }}>
                          下载
                        </a>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {(!stageData?.deliverables || stageData.deliverables.length === 0) &&
                (stageKey !== "release" || releaseFiles.length === 0) && (
                <div className="drawer-card empty-hint">
                  当前阶段暂无产出文件。
                </div>
              )}

              {/* Preview modal/box */}
              {previewContent !== null && (
                <div className="preview-box">
                  <div className="preview-header">
                    <span>预览: {previewTitle}</span>
                    <button className="cyber-close-btn" onClick={() => setPreviewContent(null)}>✕</button>
                  </div>
                  <pre className="preview-pre">{previewContent.slice(0, 5000)}</pre>
                </div>
              )}
            </div>
          )}

          {/* TAB 3: GATES & AUDITS */}
          {activeTab === "gates" && (
            <div className="drawer-tab-content">
              <h4 className="drawer-sec-title">🛡️ 闸门验收断言与审计记录</h4>
              {(stageData?.gates || []).length > 0 ? (
                <div className="drawer-gates-list">
                  {stageData.gates.map((g) => (
                    <div key={g.id} className="drawer-gate-card">
                      <div className="gate-header">
                        <span className={`gate-badge ${g.kind || "l0"}`}>{g.name || "质检闸门"}</span>
                        <span className={`gate-tag ${g.state === "passed" ? "green" : g.state === "failed" ? "danger" : "cyan"}`}>
                          {g.state?.toUpperCase()}
                        </span>
                      </div>
                      {g.note && <div className="gate-body" style={{ color: g.state === "failed" ? "#FF0055" : "#94A3B8" }}>{g.note}</div>}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="drawer-card empty-hint">
                  当前阶段无专属质检闸门或尚未执行检查。
                </div>
              )}

              <h4 className="drawer-sec-title" style={{ marginTop: "16px" }}>📜 审计日志流 (Audit Trail)</h4>
              {(stageData?.audit || []).length > 0 ? (
                <div className="audit-trail-list">
                  {stageData.audit.slice(0, 10).map((a, i) => (
                    <div key={i} className="audit-trail-item">
                      <span className="audit-action">{a.action}</span>
                      <span className="audit-time">{a.created_at?.slice(11, 19)}</span>
                      <span className="audit-payload">{a.payload}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="drawer-card empty-hint">暂无阶段审计流记录。</div>
              )}
            </div>
          )}

          {/* TAB 4: RUNS */}
          {activeTab === "runs" && (
            <div className="drawer-tab-content">
              <h4 className="drawer-sec-title">⏱️ 算力消耗与执行调用跟踪 (Runs)</h4>
              {(stageData?.runs || []).length > 0 ? (
                <div className="runs-list">
                  {stageData.runs.map((r) => (
                    <div key={r.run_id} className="run-card">
                      <div className="run-header">
                        <span className="run-id">#{r.run_id?.slice(0, 14)}</span>
                        <span className={`run-state ${r.state === "finished" || r.state === "completed" ? "green" : r.state === "running" ? "cyan" : "danger"}`}>
                          {r.state?.toUpperCase()}
                        </span>
                      </div>
                      <div className="run-title">{r.title || "任务执行"}</div>
                      <div className="run-meta">
                        <span>Tokens: {(r.usage_tokens || 0).toLocaleString()}</span>
                        <span>耗时: {r.trace?.total_ms ? `${r.trace.total_ms}ms` : "N/A"}</span>
                        <span>状态: {r.state}</span>
                      </div>
                      {r.error && <div className="run-error">错误: {r.error}</div>}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="drawer-card empty-hint">当前阶段尚未产生底层 Run 调用。</div>
              )}
            </div>
          )}
        </main>
      </aside>
    </div>
  );
}
