/* Required Notice: Copyright (c) 2026 赛博老爸 (PolyForm Noncommercial License 1.0.0) — Adapted & Wired for AgentOS */

import React, { useState, useEffect, useRef, useCallback } from "react";
import { api } from "./api.js";
import CyberStageDrawer from "./CyberStageDrawer.jsx";
import CyberHQEngine from "./CyberHQEngine.jsx";
import CyberStudio3D from "./CyberStudio3D.jsx";
import "./cyber_factorio.css";

const STAGE_KEYS = [
  { key: "intake", name: "接需求", code: "01", color: "#00F0FF", desc: "需求录入与意图识别" },
  { key: "charter", name: "立项界标", code: "02", color: "#00F0FF", desc: "实体绑定与预算上限" },
  { key: "workshop", name: "4轮研讨会", code: "03", color: "#FF6B00", desc: "提案-质询-修订-表决 & Loop Guard" },
  { key: "wbs", name: "黑板拆解", code: "04", color: "#00F0FF", desc: "DAG 依赖与共享黑板上下文" },
  { key: "build", name: "生产装配", code: "05", color: "#00F0FF", desc: "AI 员工代码与物料装配机械臂" },
  { key: "test", name: "脉冲激光质检", code: "06", color: "#00FF9D", desc: "L0红线 / L1评审 / L2沙箱断言" },
  { key: "accept", name: "验收网关", code: "07", color: "#00F0FF", desc: "多角色签章与合规通行许可" },
  { key: "release", name: "出货港", code: "08", color: "#00F0FF", desc: "Bundle.zip 交付归档与一键打包" },
  { key: "retro", name: "紫晶金库", code: "09", color: "#A855F7", desc: "组织避坑经验沉淀与 Prompt 反哺" },
];

const PRESET_DIRECTIVES = [
  {
    icon: "🚀",
    label: "单文件FinOps计算器(实盘)",
    prompt: "开发一个单文件零依赖的「赛博元公司 FinOps 算力成本与ROI计算器」网页（index.html）。要求：1. 采用深色霓虹科技风格，支持拖动滑块输入每日任务数、单任务Token消耗、模型单价与人工替代时薪，实时动态计算每日人民币开销与月度净节省金额；2. 页面底部附带产品说明文案，严禁出现任何新广告法极限词（确保100%通过L0合规网关）；3. 必须同步编写一个确定性验收脚本（test_check.py 或 node 脚本），自动校验 index.html 文件存在、包含核心计算公式函数且无语法错误，供 L2 激光质检门自动断言通过，最终打包输出至 Release。",
  },
  {
    icon: "⚡",
    label: "企业级Web中后台系统",
    prompt: "基于 React 与 FastAPI 极速构建企业级 Web 中后台系统，包含用户认证、RBAC 权限控制与自动化测试套件",
  },
  {
    icon: "📊",
    label: "全网行业竞品深度调研",
    prompt: "针对 AI Agent 行业竞争态势执行深度调研，输出包含技术架构对标、市场占有率与盈利模式的完整洞察分析研报",
  },
  {
    icon: "🛡️",
    label: "广告法全量红线安全审计",
    prompt: "对近期交付的所有落地页宣传文案与物料执行全量广告法审计，严格排查并剔除所有国家级、最优秀、100%等极限违规用词",
  },
];

export default function CyberFactorioDeck({
  me,
  Icon,
  onOpenChat,
  onNavigate,
  onReturnMainUI,
  onNavigateTab,
  onOpenAddMember,
  pendingCount = 0,
}) {
  // Studio Switcher: "hq" (🏢 智能总部 HQ, default) or "neko" (🐾 Neko-01 潮玩工坊)
  const [studioMode, setStudioMode] = useState("hq");

  const [wsConnected, setWsConnected] = useState(false);
  // 100% 真实状态绑定：初始全为 pending
  const [stagesState, setStagesState] = useState({
    intake: "pending",
    charter: "pending",
    workshop: "pending",
    wbs: "pending",
    build: "pending",
    test: "pending",
    accept: "pending",
    release: "pending",
    retro: "pending",
  });
  // 100% 真实 KPI：新项目初始如实显示 0
  const [totalRmb, setTotalRmb] = useState(0.0);
  const [totalTokens, setTotalTokens] = useState(0);
  const [crystalsCount, setCrystalsCount] = useState(0);
  const [budgetAlert80, setBudgetAlert80] = useState(false);
  const [alertData, setAlertData] = useState(null);
  const [flyingCrystals, setFlyingCrystals] = useState([]);
  const [latestEvent, setLatestEvent] = useState({
    type: "SYSTEM_READY",
    time_iso: new Date().toLocaleTimeString(),
    data: { message: "HQ 智能总部 3D 引擎就绪，实时总线已接入" }
  });
  const [selectedStage, setSelectedStage] = useState(null);
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

  // Dual-engine kernel mode state
  const [kernelMode, setKernelMode] = useState("auto_failover");

  // Camera preset view in Neko studio
  const [cameraPreset, setCameraPreset] = useState("overview");

  // CEO command bar state
  const [ceoDirective, setCeoDirective] = useState("");
  const [selectedFlow, setSelectedFlow] = useState("FLOW-SOFTWARE");
  const [isDispatching, setIsDispatching] = useState(false);
  const [dispatchSuccessMsg, setDispatchSuccessMsg] = useState("");
  const [isListening, setIsListening] = useState(false);

  const socketRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const speechRecognitionRef = useRef(null);
  const inputRef = useRef(null);
  const hqRef = useRef(null);

  // Trigger purple crystal particle flying animation
  const triggerCrystalDrop = useCallback((data) => {
    const id = Date.now() + Math.random();
    const newParticle = {
      id,
      startX: window.innerWidth * 0.45,
      startY: window.innerHeight * 0.6,
      targetX: window.innerWidth * 0.2,
      targetY: 80,
    };
    setFlyingCrystals((prev) => [...prev, newParticle]);
    setCrystalsCount((prev) => prev + 1);

    setTimeout(() => {
      setFlyingCrystals((prev) => prev.filter((p) => p.id !== id));
    }, 1200);
  }, []);

  // Fetch project list
  const loadProjects = useCallback(async (preferId) => {
    try {
      const res = await api.getProjects?.();
      if (res && res.projects) {
        setProjectList(res.projects);
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
      const res = await api.getProjectDetail?.(pid);
      if (res) {
        setProjectDetail(res);
        if (res.stages_state) {
          setStagesState((prev) => ({ ...prev, ...res.stages_state }));
        }
        if (typeof res.total_rmb === "number") setTotalRmb(res.total_rmb);
        if (typeof res.total_tokens === "number") setTotalTokens(res.total_tokens);
        if (typeof res.crystals_count === "number") setCrystalsCount(res.crystals_count);
      }
    } catch (e) {
      console.warn(`Failed to load detail for project ${pid}`, e);
    }
  }, []);

  // Real-time WebSocket connection
  const connectWebSocket = useCallback(() => {
    if (socketRef.current && (socketRef.current.readyState === WebSocket.OPEN || socketRef.current.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const loc = window.location;
    const protocol = loc.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${loc.host}/ui/ws`;

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
          setLatestEvent({
            type: payload.type || "TELEMETRY_PULSE",
            time_iso: new Date().toLocaleTimeString(),
            data: payload
          });

          // Forward to HQ 3D Engine live feed & Chief speech
          hqRef.current?.pushBackendEvent(payload);

          if (payload.type === "TELEMETRY_UPDATE" || payload.type === "STAGE_STATE_CHANGE") {
            if (payload.stages_state) {
              setStagesState((prev) => ({ ...prev, ...payload.stages_state }));
            }
            if (typeof payload.total_rmb === "number") setTotalRmb(payload.total_rmb);
            if (typeof payload.total_tokens === "number") setTotalTokens(payload.total_tokens);
            if (typeof payload.crystals_count === "number") setCrystalsCount(payload.crystals_count);
          }

          if (payload.type === "BUDGET_PAUSE_80" || payload.type === "BUDGET_ALERT_80") {
            setBudgetAlert80(true);
            setAlertData(payload);
          }

          if (payload.type === "EXPERIENCE_CRYSTAL_DROPPED") {
            triggerCrystalDrop(payload);
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
  }, [activeProjectId, triggerCrystalDrop]);

  useEffect(() => {
    connectWebSocket();
    loadProjects();
    return () => {
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (socketRef.current) socketRef.current.close();
    };
  }, [connectWebSocket, loadProjects]);

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

  // Launch new project from HQ Task Modal or CEO Command Bar
  const handleCreateTaskFromHQ = async (taskData) => {
    try {
      const res = await api.createProject({
        name: taskData.name,
        requirement: taskData.name,
        flow: taskData.tplKey === "soft" ? "FLOW-SOFTWARE" : "FLOW-SOFTWARE",
      });

      const newId = res.id || res.project_id;
      setActiveProjectId(newId);
      loadProjects(newId);
      loadProjectDetail(newId);

      // Notify HQ 3D Engine that cloud pipeline is live!
      hqRef.current?.pushBackendEvent({
        type: "CLOUD_PROJECT_CREATED",
        projId: taskData.projId,
        realId: newId,
        name: taskData.name,
      });
    } catch (err) {
      console.warn("Failed to create cloud project from HQ task", err);
    }
  };

  // Launch new project from CEO Command Bar in Neko mode
  const handleDispatchDirective = async () => {
    const promptText = ceoDirective.trim();
    if (!promptText || isDispatching) return;

    setIsDispatching(true);
    setDispatchSuccessMsg("");

    try {
      const autoName = promptText.length > 25 ? `${promptText.slice(0, 24)}...` : promptText;
      const res = await api.createProject({
        name: autoName,
        requirement: promptText,
        flow: selectedFlow || "FLOW-SOFTWARE",
      });

      const newId = res.id || res.project_id;
      setActiveProjectId(newId);

      setTotalTokens(0);
      setTotalRmb(0.0);
      setStagesState({
        intake: "active",
        charter: "pending",
        workshop: "pending",
        wbs: "pending",
        build: "pending",
        test: "pending",
        accept: "pending",
        release: "pending",
        retro: "pending",
      });

      setDispatchSuccessMsg(`🚀 指令已下达！小人正在走向工位，项目 [${newId}] 启动总装...`);
      setCeoDirective("");

      await loadProjects(newId);
      await loadProjectDetail(newId);

      hqRef.current?.pushBackendEvent({
        type: "CLOUD_PROJECT_CREATED",
        realId: newId,
        name: autoName,
      });

      setTimeout(() => setDispatchSuccessMsg(""), 6000);
    } catch (err) {
      alert(`流水线启动失败: ${err.message || "网络或权限异常"}`);
    } finally {
      setIsDispatching(false);
    }
  };

  // Open micro-control stage drawer
  const openStageDrawer = (stageKey) => {
    const standardMeta = STAGE_KEYS.find((s) => s.key === stageKey) || {
      key: stageKey,
      name: stageKey,
      code: "00",
      color: "#00F0FF",
      desc: "工序微调控制台",
    };
    setSelectedStage({ ...standardMeta, state: stagesState[stageKey] || "pending" });
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
    setStagesState((prev) => ({ ...prev, build: "blocked" }));
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
      {/* ============================================================== */}
      {/* MODE 1: 🏢 智能总部 HQ 3D 引擎 (@赛博老爸 10 · HQ 智能总部)          */}
      {/* ============================================================== */}
      {studioMode === "hq" && (
        <CyberHQEngine
          ref={hqRef}
          wsConnected={wsConnected}
          totalTokens={totalTokens}
          pendingCount={pendingCount}
          projectList={projectList}
          activeProjectId={activeProjectId}
          onCreateTask={handleCreateTaskFromHQ}
          onSwitchStudio={() => setStudioMode("neko")}
          onOpenExchange={() => setShowExchangeModal(true)}
          onNavigateTab={onNavigateTab || onNavigate}
        />
      )}

      {/* ============================================================== */}
      {/* MODE 2: 🐾 Neko-01 赛博猫娘潮玩工坊 (Three.js 60FPS 虚拟工作室)   */}
      {/* ============================================================== */}
      {studioMode === "neko" && (
        <>
          <CyberStudio3D
            stagesState={stagesState}
            activeProjectId={activeProjectId}
            totalRmb={totalRmb}
            totalTokens={totalTokens}
            crystalsCount={crystalsCount}
            budgetAlert80={budgetAlert80}
            approvalModal={approvalModal}
            openStageDrawer={openStageDrawer}
            onOpenExchange={() => setShowExchangeModal(true)}
            onOpenApproval={() => {
              if (approvalModal) setApprovalModal(approvalModal);
              else if (onNavigateTab) onNavigateTab("approvals");
              else if (onNavigate) onNavigate("approvals");
            }}
            onDirectiveFocus={() => inputRef.current?.focus()}
            cameraPreset={cameraPreset}
            onCameraPresetChange={setCameraPreset}
          />

          {/* Switch Back to HQ Button */}
          <button
            className="cyber-btn cyan"
            style={{
              position: "absolute",
              top: "14px",
              right: "18px",
              borderRadius: "9999px",
              padding: "7px 16px",
              fontSize: "12px",
              fontWeight: "600",
              zIndex: 35,
              boxShadow: "0 4px 18px rgba(0, 240, 255, 0.4)",
            }}
            onClick={() => setStudioMode("hq")}
            title="切回 🏢 智能总部 HQ 全景大厦"
          >
            🏢 切回智能总部 HQ
          </button>

          {/* Top-Left Stats for Neko Mode */}
          <div
            style={{
              position: "absolute",
              top: "14px",
              left: "18px",
              display: "flex",
              alignItems: "center",
              gap: "10px",
              zIndex: 30,
            }}
          >
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "8px",
                padding: "6px 14px",
                borderRadius: "9999px",
                background: "rgba(9, 14, 28, 0.88)",
                border: "1px solid rgba(0, 240, 255, 0.4)",
                fontSize: "12px",
                fontWeight: "600",
                color: "#f8fafc",
              }}
            >
              <span
                style={{
                  width: "7px",
                  height: "7px",
                  borderRadius: "50%",
                  background: wsConnected ? "#00FF9D" : "#64748B",
                }}
              />
              <span>{wsConnected ? "遥测 LIVE" : "遥测离线"}</span>
            </div>

            {projectList.length > 0 && (
              <select
                value={activeProjectId}
                onChange={(e) => {
                  const pid = e.target.value;
                  setActiveProjectId(pid);
                  loadProjectDetail(pid);
                }}
                style={{
                  background: "rgba(9, 14, 28, 0.88)",
                  border: "1px solid rgba(120, 140, 255, 0.35)",
                  borderRadius: "9999px",
                  padding: "6px 14px",
                  color: "#e2e8f0",
                  fontSize: "12px",
                  outline: "none",
                  cursor: "pointer",
                }}
              >
                {projectList.map((p) => (
                  <option key={p.id} value={p.id}>
                    📁 {p.name || p.id} ({p.current_stage || "init"})
                  </option>
                ))}
              </select>
            )}

            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "10px",
                padding: "6px 14px",
                borderRadius: "9999px",
                background: "rgba(9, 14, 28, 0.88)",
                border: "1px solid rgba(168, 85, 247, 0.35)",
                fontSize: "11px",
                color: "#cbd5e1",
              }}
            >
              <span style={{ color: "#a855f7", fontWeight: "bold" }}>💎 {crystalsCount} 晶石</span>
              <span style={{ color: "#475569" }}>|</span>
              <span style={{ color: "#f59e0b" }}>￥{totalRmb.toFixed(2)}</span>
              <span style={{ color: "#475569" }}>|</span>
              <span style={{ color: "#00f0ff" }}>{totalTokens.toLocaleString()} Tok</span>
            </div>
          </div>

          {/* Bottom Floating Console in Neko Mode */}
          <div
            style={{
              position: "absolute",
              bottom: "16px",
              left: "50%",
              transform: "translateX(-50%)",
              width: "min(960px, 94vw)",
              background: "rgba(9, 14, 28, 0.94)",
              border: "1px solid rgba(0, 240, 255, 0.4)",
              borderRadius: "16px",
              padding: "12px 18px",
              boxShadow: "0 16px 45px rgba(0, 0, 0, 0.8), 0 0 30px rgba(0, 240, 255, 0.15)",
              backdropFilter: "blur(14px)",
              zIndex: 35,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <span style={{ fontSize: "11px", color: "#94a3b8", fontWeight: "600" }}>工序微调:</span>
                {STAGE_KEYS.map((st) => (
                  <div
                    key={st.key}
                    onClick={() => openStageDrawer(st.key)}
                    style={{
                      padding: "2px 8px",
                      borderRadius: "9999px",
                      fontSize: "11px",
                      cursor: "pointer",
                      background: "rgba(255,255,255,0.06)",
                      color: "#94a3b8",
                    }}
                  >
                    <span>{st.code}</span> <span>{st.name}</span>
                  </div>
                ))}
              </div>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <input
                ref={inputRef}
                type="text"
                value={ceoDirective}
                onChange={(e) => setCeoDirective(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleDispatchDirective();
                  }
                }}
                placeholder="给 Neko-01 下达指令（发送后小人将自动走向工位开始总装）..."
                style={{
                  flex: 1,
                  background: "#060A14",
                  border: "1px solid rgba(0, 240, 255, 0.4)",
                  borderRadius: "8px",
                  padding: "10px 14px",
                  color: "#F8FAFC",
                  fontSize: "13px",
                  outline: "none",
                }}
              />
              <button
                type="button"
                className="cyber-btn cyan"
                disabled={isDispatching || !ceoDirective.trim()}
                onClick={handleDispatchDirective}
                style={{ padding: "8px 16px", whiteSpace: "nowrap" }}
              >
                {isDispatching ? "点火总装中..." : "🚀 派发战术指令"}
              </button>
            </div>
          </div>
        </>
      )}

      {/* 4. RED ALERT BANNER (80% BUDGET PAUSE) */}
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

      {/* 5. FLYING PURPLE CRYSTALS PARTICLES */}
      {flyingCrystals.map((fc) => (
        <div
          key={fc.id}
          className="flying-crystal-particle"
          style={{
            position: "fixed",
            left: `${fc.startX}px`,
            top: `${fc.startY}px`,
            "--tx": `${fc.targetX - fc.startX}px`,
            "--ty": `${fc.targetY - fc.startY}px`,
            pointerEvents: "none",
            zIndex: 9999,
          }}
        >
          💎 +1 组织避坑晶石
        </div>
      ))}

      {/* 6. SLIDE-OUT MICRO-CONTROL STAGE DRAWER */}
      {selectedStage && (
        <CyberStageDrawer
          stage={selectedStage}
          projectId={activeProjectId}
          projectDetail={projectDetail}
          onClose={() => setSelectedStage(null)}
          onOpenChat={onOpenChat}
          onOpenExchange={() => {
            setSelectedStage(null);
            setShowExchangeModal(true);
          }}
          triggerCrystalDrop={triggerCrystalDrop}
          onRefreshProject={() => loadProjectDetail(activeProjectId)}
        />
      )}

      {/* 7. HOLOGRAPHIC APPROVAL MODAL (WECOM DECOUPLED) */}
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
                  alert("已执行驳回！触发经验提取，避坑紫晶凝聚中...");
                  triggerCrystalDrop({ title: "指挥舱手动驳回", kind: "rejection" });
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

      {/* 8. METACORP MULTI-WORLD EXCHANGE MODAL */}
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
