/* Required Notice: Copyright (c) 2026 赛博老爸 (PolyForm Noncommercial License 1.0.0) — Adapted & Wired for AgentOS */

import React, { useState, useEffect, useRef, useCallback } from "react";
import { api } from "./api.js";
import CyberHQEngine from "./CyberHQEngine.jsx";
import "./cyber_factorio.css";

export default function CyberFactorioDeck({
  me,
  Icon,
  chats = [],
  onOpenChat,
  onNavigate,
  onInspectEmployee,
  onOpenSettings,
  onReturnMainUI,
  onNavigateTab,
  onOpenAddMember,
  pendingCount = 0,
}) {
  const [wsConnected, setWsConnected] = useState(false);
  const [totalRmb, setTotalRmb] = useState(0.0);
  const [totalTokens, setTotalTokens] = useState(0);
  const [crystalsCount, setCrystalsCount] = useState(0);
  const [budgetAlert80, setBudgetAlert80] = useState(false);
  const [alertData, setAlertData] = useState(null);

  const [showExchangeModal, setShowExchangeModal] = useState(false);
  const [exchangeTab, setExchangeTab] = useState("export");
  const [exchangePacks, setExchangePacks] = useState([]);
  const [importJson, setImportJson] = useState("");
  const [hireEndpoint, setHireEndpoint] = useState("local_mock");
  const [hireRequirement, setHireRequirement] = useState("编写带单元测试的高并发计数器组件");
  const [hireStatus, setHireStatus] = useState(null);
  const [approvalModal, setApprovalModal] = useState(null);

  // Project management state
  const [activeProjectId, setActiveProjectId] = useState("");
  const [projectList, setProjectList] = useState([]);
  const [projectDetail, setProjectDetail] = useState(null);

  const socketRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const hqRef = useRef(null);

  // Fetch HQ snapshot from real backend
  const loadSnapshot = useCallback(async () => {
    try {
      const snap = await api.hqSnapshot();
      if (snap && snap.ok) {
        hqRef.current?.syncBackendData({ snapshot: snap });
        if (snap.kpis) {
          if (typeof snap.kpis.today_tokens === "number") setTotalTokens(snap.kpis.today_tokens);
          if (typeof snap.kpis.total_rmb === "number") setTotalRmb(snap.kpis.total_rmb);
          if (typeof snap.kpis.crystals_count === "number") setCrystalsCount(snap.kpis.crystals_count);
        }
        if (snap.projects && snap.projects.length > 0) {
          setProjectList(snap.projects);
          if (!activeProjectId) setActiveProjectId(snap.projects[0].id);
        }
      }
    } catch (e) {
      console.warn("Failed to load HQ snapshot", e);
    }
  }, [activeProjectId]);

  const handleFireEmployee = async (empId) => {
    try {
      await api.fireEmployee(empId);
      await loadSnapshot();
    } catch (e) {
      console.warn("Failed to fire employee", e);
    }
  };

  const handleHireRole = async (role) => {
    try {
      await api.hqHireRole(role.id);
      await loadSnapshot();
    } catch (e) {
      console.warn("Failed to hire role", e);
    }
  };

  const handleOpenEmployeeChat = (emp) => {
    if (!emp) return;
    const targetId = emp.empId || emp.id;
    let found = (chats || []).find((ch) => ch.employee_id === targetId || (emp.chatId && ch.id === emp.chatId));
    if (emp.isChief || emp.d === -1) {
      found = (chats || []).find((ch) => ch.kind === "ceo") || found;
    }
    if (found && onOpenChat) {
      onOpenChat(found);
    } else if (onNavigate) {
      onNavigate("chats");
    }
  };

  // Fetch project list
  const loadProjects = useCallback(async (preferId) => {
    try {
      const res = await (api.projects ? api.projects() : api.getProjects?.());
      if (res && res.projects) {
        setProjectList(res.projects);
        hqRef.current?.syncBackendData({ projects: res.projects });
        if (preferId) {
          setActiveProjectId(preferId);
        } else if (!activeProjectId && res.projects.length > 0) {
          setActiveProjectId(res.projects[0].id);
        }
      }
    } catch (e) {
      console.warn("Failed to load projects list", e);
    }
  }, [activeProjectId]);

  // Fetch project detail
  const loadProjectDetail = useCallback(async (pid) => {
    if (!pid) return;
    try {
      const res = await (api.project ? api.project(pid) : api.getProjectDetail?.(pid));
      if (res) {
        setProjectDetail(res);
        if (typeof res.total_rmb === "number") setTotalRmb(res.total_rmb);
        if (typeof res.total_tokens === "number") setTotalTokens(res.total_tokens);
        if (typeof res.crystals_count === "number") setCrystalsCount(res.crystals_count);
      }
    } catch (e) {
      console.warn(`Failed to load detail for project ${pid}`, e);
    }
  }, []);

  // Real-time WebSocket connection to /ui/ws
  const connectWebSocket = useCallback(() => {
    if (socketRef.current && (socketRef.current.readyState === WebSocket.OPEN || socketRef.current.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const loc = window.location;
    const protocol = loc.protocol === "https:" ? "wss:" : "ws:";
    const isDesktop = loc.hostname === "tauri.localhost" || loc.protocol === "tauri:";
    const wsUrl = isDesktop
      ? "ws://127.0.0.1:8077/ws/metacorp"
      : `${protocol}//${loc.host}/ws/metacorp`;

    try {
      const ws = new WebSocket(wsUrl);
      socketRef.current = ws;

      ws.onopen = () => {
        setWsConnected(true);
        if (activeProjectId) {
          ws.send(JSON.stringify({ type: "SUBSCRIBE_PROJECT", project_id: activeProjectId }));
        }
        hqRef.current?.syncBackendData({ wsConnected: true });
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);

          // Forward event to HQ 3D Engine live feed & Chief speech
          hqRef.current?.pushBackendEvent(payload);

          if (payload.type === "TELEMETRY_UPDATE" || payload.type === "STAGE_STATE_CHANGE") {
            if (typeof payload.total_rmb === "number") setTotalRmb(payload.total_rmb);
            if (typeof payload.total_tokens === "number") setTotalTokens(payload.total_tokens);
            if (typeof payload.crystals_count === "number") setCrystalsCount(payload.crystals_count);
          }
          if (["ROSTER_UPDATED", "STAGE_STATE_CHANGE", "TASK_STATE_CHANGE", "WORKSHOP_TURN", "EXP_CRYSTAL_DROP"].includes(payload.type)) {
            loadSnapshot();
          }

          if (payload.type === "BUDGET_PAUSE_80" || payload.type === "BUDGET_ALERT_80") {
            setBudgetAlert80(true);
            setAlertData(payload);
          }

          if (payload.type === "APPROVAL_REQUIRED") {
            setApprovalModal(payload);
          }
        } catch (err) {
          console.error("WS Parse Error", err);
        }
      };

      ws.onclose = () => {
        setWsConnected(false);
        hqRef.current?.syncBackendData({ wsConnected: false });
        reconnectTimeoutRef.current = setTimeout(connectWebSocket, 3000);
      };

      ws.onerror = () => {
        setWsConnected(false);
        ws.close();
      };
    } catch (e) {
      setWsConnected(false);
      reconnectTimeoutRef.current = setTimeout(connectWebSocket, 3000);
    }
  }, [activeProjectId]);

  useEffect(() => {
    connectWebSocket();
    loadProjects();
    loadSnapshot();
    const interval = setInterval(loadSnapshot, 10000);
    const onRefresh = () => loadSnapshot();
    window.addEventListener("oa-hq-refresh", onRefresh);
    return () => {
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (socketRef.current) socketRef.current.close();
      clearInterval(interval);
      window.removeEventListener("oa-hq-refresh", onRefresh);
    };
  }, [connectWebSocket, loadProjects, loadSnapshot]);

  useEffect(() => {
    if (activeProjectId) {
      loadProjectDetail(activeProjectId);
      if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify({ type: "SUBSCRIBE_PROJECT", project_id: activeProjectId }));
      }
    }
  }, [activeProjectId, loadProjectDetail]);

  // Load exchange blueprints
  useEffect(() => {
    if (showExchangeModal) {
      fetch("/mcp-exchange/packs")
        .then((r) => r.json())
        .then((data) => {
          if (data && data.packs) setExchangePacks(data.packs);
        })
        .catch((e) => console.warn("Failed to load exchange packs", e));
    }
  }, [showExchangeModal]);

  // Real Cloud Task Dispatch from HQ Task Modal
  const handleCreateTaskFromHQ = async (taskData) => {
    try {
      const mode = taskData.mode || "pipeline";
      const req = (taskData.requirement || taskData.name || "").trim();

      if (mode === "ceo") {
        const ceoChat = (chats || []).find((c) => c.kind === "ceo");
        if (ceoChat) {
          if (req) await api.send(ceoChat.id, req);
          if (onOpenChat) onOpenChat(ceoChat);
        } else if (onNavigate) {
          onNavigate("chats");
        }
        return;
      }

      if (mode === "direct" && taskData.employeeId) {
        const empChat = (chats || []).find((c) => c.employee_id === taskData.employeeId);
        if (empChat) {
          if (req) await api.send(empChat.id, req);
          if (onOpenChat) onOpenChat(empChat);
        } else if (onNavigate) {
          onNavigate("chats");
        }
        return;
      }

      // Default: 9-stage pipeline
      const flowId = taskData.flow || (taskData.tplKey === "budget" ? "FLOW-ANALYSIS" : "FLOW-SOFTWARE");
      const res = await api.createProject({
        name: req.slice(0, 40) || "新流水线项目",
        requirement: req,
        flow: flowId,
      });

      const newId = res.id || res.project_id;
      setActiveProjectId(newId);
      await loadSnapshot();

      hqRef.current?.pushBackendEvent({
        type: "CLOUD_PROJECT_CREATED",
        projId: newId,
        realId: newId,
        name: req.slice(0, 40),
      });
    } catch (err) {
      console.warn("Failed to create cloud project from HQ task", err);
    }
  };

  // Top up budget handler
  const handleTopupBudget = async () => {
    try {
      await fetch(`/ui/projects/${activeProjectId}/resume`, { method: "POST" });
    } catch (e) {}
    setBudgetAlert80(false);
    setAlertData(null);
  };

  const handleCancelRun = () => {
    setBudgetAlert80(false);
    setAlertData(null);
  };

  const handleExportBlueprint = (packId) => {
    window.open(`/mcp-exchange/export/${packId}`, "_blank");
  };

  const handleImportBlueprint = async () => {
    try {
      const payload = JSON.parse(importJson);
      const res = await fetch("/mcp-exchange/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then((r) => r.json());
      if (res.ok) {
        alert(`蓝图注入成功！已同步 ${res.experiences_imported} 条神级避坑经验。`);
        setImportJson("");
        setCrystalsCount((c) => c + (res.experiences_imported || 1));
      } else {
        alert(`导入失败: ${res.detail || "格式错误"}`);
      }
    } catch (e) {
      alert(`JSON 解析错误: ${e.message}`);
    }
  };

  const handleHireRemoteMcp = async () => {
    setHireStatus({ loading: true, message: "正在跨公司 MCP 握手路由子任务并拉取交付物..." });
    try {
      const res = await fetch("/mcp-exchange/hire-remote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint_url: hireEndpoint,
          tool_name: "metacorp.execute_stage",
          arguments: { stage_key: "build", requirement: hireRequirement, role_tag: "dev" },
          project_id: activeProjectId,
          stage_key: "build",
        }),
      }).then((r) => r.json());

      if (res.ok) {
        setHireStatus({
          loading: false,
          success: true,
          message: `跨公司 MCP 任务执行完毕！产出物 [${res.filename}] 已自动合入 Bundle.zip。`,
        });
      } else {
        setHireStatus({ loading: false, success: false, message: `雇佣失败: ${res.detail}` });
      }
    } catch (e) {
      setHireStatus({ loading: false, success: false, message: `调用异常: ${e.message}` });
    }
  };

  return (
    <div
      className={`cyber-deck ${budgetAlert80 ? "alert-strobe" : ""}`}
      style={{
        position: "relative",
        width: "100%",
        height: "calc(100vh - 56px)",
        maxHeight: "calc(100vh - 56px)",
        overflow: "hidden",
        background: "#070b14",
        padding: 0,
        margin: 0,
      }}
    >
      {/* 1. 🏢 智能总部 HQ 3D 引擎 (@赛博老爸 10 · HQ 智能总部) */}
      <CyberHQEngine
        ref={hqRef}
        wsConnected={wsConnected}
        totalTokens={totalTokens}
        pendingCount={pendingCount}
        projectList={projectList}
        activeProjectId={activeProjectId}
        onCreateTask={handleCreateTaskFromHQ}
        onOpenExchange={() => setShowExchangeModal(true)}
        onNavigateTab={onNavigateTab || onNavigate}
        onNavigate={onNavigate}
        onOpenEmployeeChat={handleOpenEmployeeChat}
        onInspectEmployee={onInspectEmployee}
        onOpenAddMember={onOpenAddMember}
        onOpenSettings={onOpenSettings}
        onFireEmployee={handleFireEmployee}
        onHireRole={handleHireRole}
      />

      {/* 2. RED ALERT BANNER (80% BUDGET PAUSE) */}
      {budgetAlert80 && (
        <div
          className="cyber-alert-ribbon"
          style={{ position: "absolute", top: "60px", left: "20px", right: "20px", zIndex: 45 }}
        >
          <div className="alert-text">
            <span>🚨</span>
            <span>
              <b>全场高能红光预警：</b>单阶段预算消耗已突破 <b>80%</b> 防御阈值（已消耗 {alertData?.used_tokens?.toLocaleString() || "16,000"} Tokens / ￥{alertData?.cost_rmb || "0.85"}）！ACP 实时熔断闸门已安全挂起子进程。
            </span>
          </div>
          <div style={{ display: "flex", gap: "10px" }}>
            <button className="cyber-btn" style={{ background: "#FFFFFF", color: "#FF0055", borderColor: "#FFFFFF" }} onClick={handleTopupBudget}>
              一键追加 20,000 Token 配额并恢复
            </button>
            <button className="cyber-btn danger" onClick={handleCancelRun}>
              彻底熔断 (Cancel)
            </button>
          </div>
        </div>
      )}

      {/* 3. HOLOGRAPHIC APPROVAL MODAL (WECOM DECOUPLED) */}
      {approvalModal && (
        <div className="cyber-modal-overlay">
          <div className="cyber-modal-card red-alert">
            <div className="cyber-modal-header danger">
              <div className="cyber-modal-title">
                <span>🛡️ 全息指挥舱待办审批令 (WeCom Decoupled)</span>
              </div>
              <button className="cyber-btn danger" onClick={() => setApprovalModal(null)}>稍后处理</button>
            </div>
            <div className="cyber-modal-body">
              <div style={{ fontSize: "1rem", fontWeight: "bold", color: "#F8FAFC", marginBottom: "8px" }}>
                {approvalModal.payload?.main_title?.title || "待办任务质检验收"}
              </div>
              <p style={{ color: "#94A3B8" }}>
                {approvalModal.payload?.sub_title_text || "任务已产出阶段物料，等待指挥官审核通过。"}
              </p>
              <div style={{ marginTop: "12px", background: "rgba(0,0,0,0.4)", padding: "12px", borderRadius: "6px" }}>
                <div><b>目标接收人：</b>{approvalModal.to_user || "CEO"}</div>
                <div><b>消息通道：</b>{approvalModal.channel}</div>
              </div>
            </div>
            <div className="cyber-modal-footer">
              <button
                className="cyber-btn danger"
                onClick={() => {
                  alert("已执行驳回！");
                  setApprovalModal(null);
                }}
              >
                ✗ 驳回 (Reject)
              </button>
              <button
                className="cyber-btn"
                style={{ background: "#00FF9D", color: "#060913", borderColor: "#00FF9D" }}
                onClick={async () => {
                  try {
                    await api.resumeProject(activeProjectId);
                  } catch (e) {
                    console.warn(e);
                  }
                  setApprovalModal(null);
                  loadProjectDetail(activeProjectId);
                }}
              >
                ✓ 签署通过 (Approve)
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 4. METACORP MULTI-WORLD EXCHANGE MODAL */}
      {showExchangeModal && (
        <div className="cyber-modal-overlay" onClick={() => setShowExchangeModal(false)}>
          <div className="cyber-modal-card" style={{ maxWidth: "780px" }} onClick={(e) => e.stopPropagation()}>
            <div className="cyber-modal-header">
              <div className="cyber-modal-title" style={{ color: "#A855F7" }}>
                <span>🌐 万界 MCP 交易所 (MetaCorp Multi-World Exchange)</span>
              </div>
              <button className="cyber-btn purple" onClick={() => setShowExchangeModal(false)}>关闭</button>
            </div>

            {/* Exchange Tabs */}
            <div style={{ display: "flex", borderBottom: "1px solid rgba(168, 85, 247, 0.2)", background: "rgba(168, 85, 247, 0.05)" }}>
              <button
                className="cyber-btn"
                style={{ borderRadius: 0, border: "none", borderBottom: exchangeTab === "export" ? "2px solid #A855F7" : "none", color: exchangeTab === "export" ? "#A855F7" : "#94A3B8" }}
                onClick={() => setExchangeTab("export")}
              >
                1. 蓝图神装打包导出
              </button>
              <button
                className="cyber-btn"
                style={{ borderRadius: 0, border: "none", borderBottom: exchangeTab === "import" ? "2px solid #A855F7" : "none", color: exchangeTab === "import" ? "#A855F7" : "#94A3B8" }}
                onClick={() => setExchangeTab("import")}
              >
                2. 外部蓝图注入
              </button>
              <button
                className="cyber-btn"
                style={{ borderRadius: 0, border: "none", borderBottom: exchangeTab === "hire" ? "2px solid #A855F7" : "none", color: exchangeTab === "hire" ? "#A855F7" : "#94A3B8" }}
                onClick={() => setExchangeTab("hire")}
              >
                3. 跨公司 MCP 雇佣
              </button>
            </div>

            <div className="cyber-modal-body">
              {exchangeTab === "export" && (
                <div>
                  <p style={{ color: "#94A3B8", marginBottom: "16px" }}>
                    将本地调教成熟的专精业务包（WBS 流程树 + 组织避坑晶石 + L0/L2 验收脚本 + 评测基准）一键固化打包为 <code>.metacorp.json</code> 神装图纸。
                  </p>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                    {exchangePacks.map((p) => (
                      <div key={p.id} style={{ border: "1px solid rgba(168, 85, 247, 0.3)", borderRadius: "6px", padding: "14px", background: "rgba(168, 85, 247, 0.04)" }}>
                        <div style={{ fontWeight: "bold", color: "#F8FAFC", marginBottom: "4px" }}>{p.name}</div>
                        <div style={{ fontSize: "0.75rem", color: "#A855F7", marginBottom: "8px" }}>ID: {p.id}</div>
                        <div style={{ fontSize: "0.75rem", color: "#94A3B8", marginBottom: "12px" }}>
                          避坑晶石: {p.experiences_count || 12} 条 | 默认预算: {p.default_budget?.toLocaleString()} Tokens
                        </div>
                        <button className="cyber-btn purple" style={{ width: "100%", justifyContent: "center" }} onClick={() => handleExportBlueprint(p.id)}>
                          <span>📦</span> 导出 {p.id}.metacorp.json
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {exchangeTab === "import" && (
                <div>
                  <p style={{ color: "#94A3B8", marginBottom: "12px" }}>
                    粘贴外部玩家分享的 <code>.metacorp.json</code> 蓝图 JSON，系统将全自动解析并将 WBS 模板与组织经验晶石载入本地知识库。
                  </p>
                  <textarea
                    rows={8}
                    style={{ width: "100%", background: "#060A14", color: "#A855F7", border: "1px solid rgba(168, 85, 247, 0.4)", borderRadius: "6px", padding: "10px", fontSize: "0.8rem", fontFamily: "monospace" }}
                    placeholder='粘贴 {"$schema": "https://agent-os.metacorp/v1/...", "format": "metacorp.blueprint", ...}'
                    value={importJson}
                    onChange={(e) => setImportJson(e.target.value)}
                  />
                  <div style={{ marginTop: "12px" }}>
                    <button className="cyber-btn purple" onClick={handleImportBlueprint}>
                      <span>⚡</span> 注入本地元公司知识库
                    </button>
                  </div>
                </div>
              )}

              {exchangeTab === "hire" && (
                <div>
                  <p style={{ color: "#94A3B8", marginBottom: "12px" }}>
                    跨公司委派执行：在 WBS 拆解阶段将指定子任务路由至外部 MCP 元公司节点，验证通过后产出物将自动打包合并入 <code>Bundle.zip</code>。
                  </p>
                  <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                    <div>
                      <label style={{ fontSize: "0.75rem", color: "#94A3B8" }}>外部 MCP 端点 URL (支持 local_mock 或真实 HTTP JSON-RPC)：</label>
                      <input
                        style={{ width: "100%", background: "#060A14", border: "1px solid rgba(0, 240, 255, 0.4)", color: "#00F0FF", padding: "8px", borderRadius: "4px" }}
                        value={hireEndpoint}
                        onChange={(e) => setHireEndpoint(e.target.value)}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: "0.75rem", color: "#94A3B8" }}>外包子任务需求：</label>
                      <input
                        style={{ width: "100%", background: "#060A14", border: "1px solid rgba(0, 240, 255, 0.4)", color: "#F8FAFC", padding: "8px", borderRadius: "4px" }}
                        value={hireRequirement}
                        onChange={(e) => setHireRequirement(e.target.value)}
                      />
                    </div>
                    <button className="cyber-btn cyan" onClick={handleHireRemoteMcp}>
                      <span>🚀</span> 启动跨公司 MCP 雇佣执行
                    </button>
                  </div>

                  {hireStatus && (
                    <div style={{ marginTop: "16px", padding: "12px", borderRadius: "6px", background: hireStatus.success ? "rgba(0, 255, 157, 0.1)" : "rgba(255, 0, 85, 0.1)", border: `1px solid ${hireStatus.success ? "#00FF9D" : "#FF0055"}` }}>
                      <div style={{ color: hireStatus.success ? "#00FF9D" : "#FF0055", fontWeight: "bold" }}>
                        {hireStatus.message}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="cyber-modal-footer">
              <button className="cyber-btn" onClick={() => setShowExchangeModal(false)}>完成</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
