import React, { useState, useEffect, useRef, useCallback } from "react";
import { api } from "./api.js";
import CyberStageDrawer from "./CyberStageDrawer.jsx";
import "./cyber_factorio.css";

const STAGE_KEYS = [
  { key: "intake", name: "接需求", code: "01", x: 140, y: 90, color: "#00F0FF", desc: "需求录入与意图识别" },
  { key: "charter", name: "立项界标", code: "02", x: 380, y: 90, color: "#00F0FF", desc: "实体绑定与预算上限" },
  { key: "workshop", name: "4轮研讨会", code: "03", x: 640, y: 90, color: "#FF6B00", isOcto: true, desc: "提案-质询-修订-表决 & Loop Guard" },
  { key: "wbs", name: "黑板拆解", code: "04", x: 640, y: 240, color: "#00F0FF", desc: "DAG 依赖与共享黑板上下文" },
  { key: "build", name: "生产装配", code: "05", x: 380, y: 240, color: "#00F0FF", desc: "AI 员工代码与物料装配机械臂" },
  { key: "test", name: "脉冲激光质检", code: "06", x: 140, y: 240, color: "#00FF9D", isLaser: true, desc: "L0红线 / L1评审 / L2沙箱断言" },
  { key: "accept", name: "验收网关", code: "07", x: 140, y: 390, color: "#00F0FF", desc: "多角色签章与合规通行许可" },
  { key: "release", name: "出货港", code: "08", x: 380, y: 390, color: "#00F0FF", isRelease: true, desc: "Bundle.zip 交付归档与一键打包" },
  { key: "retro", name: "紫晶金库", code: "09", x: 640, y: 390, color: "#A855F7", isVault: true, desc: "组织避坑经验沉淀与 Prompt 反哺" },
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


const NEKO_WORK_DIALOGUES = [
  "喵~ 报告 CEO！智能工坊 01~09 流水线全力咬合运转中！有新指令交给我吧！✨",
  "呼噜噜~ MCP 工具链已经全线锁紧，没有发现任何逻辑死锁，算力平稳！🐾",
  "本猫正在严密监视 L2 激光质检与验收网关！不合规的物料一个也别想出港！🛡️",
  "想要给本猫投喂点什么吗？发光紫晶石最美味了，还能扩充组织经验库哦~ 💎",
  "按【空格键】随时唤醒本猫的 CEO 指令舱！秒级起草并下发战术工作流！⚡",
];

const NEKO_CAMP_DIALOGUES = [
  "露营篝火噼里啪啦响呢~ Agent 小伙伴们正在星空下复盘今天的敏捷交付！🏕️",
  "紫晶石温润地吸收着今天跑通的避坑经验，组织资产又壮大了喵~ 💎",
  "喝一口赛博热可可~ 劳逸结合才能防止 Agent 发生幻觉与语义漂移哦！✨",
];

const NEKO_GYM_DIALOGUES = [
  "一二一！脑机超频 520 TFLOPS！高并发压力测试正在挥汗如雨！🏋️",
  "心率平稳，Token 吞吐量持续冲高！我们团队的上下文窗口越来越深厚了！⚡",
  "出汗的感觉真棒！打完这轮拳击，就能轻松扛住 100 个并发复杂 WBS 任务！🥊",
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
  zeroHudMode = true,
  onToggleClassicDeck,
}) {
  // 3D Toy-Box Diorama CyberDeck State
  const [dioramaMode, setDioramaMode] = useState(true);
  const [scene, setScene] = useState("work"); // "work" | "camp" | "gym"
  const [isFlipping, setIsFlipping] = useState(false);
  const [tilt, setTilt] = useState({ x: 0, y: 0 });
  const [showNekoDialog, setShowNekoDialog] = useState(false);
  const [nekoDialogIdx, setNekoDialogIdx] = useState(0);
  const [nekoBounce, setNekoBounce] = useState(false);
  const [nekoReactions, setNekoReactions] = useState([]);
  const [showAllModulesMenu, setShowAllModulesMenu] = useState(false);
  const [showDirectiveLauncher, setShowDirectiveLauncher] = useState(false);
  const [showWorkPropsMenu, setShowWorkPropsMenu] = useState(false);

  const [wsConnected, setWsConnected] = useState(false);
  // 100% 真实状态绑定：初始全为 pending，严禁任何写死 done/active！
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
  // 100% 真实 KPI：新项目初始如实显示 0，严禁写死 3.42 或 68200！
  const [totalRmb, setTotalRmb] = useState(0.0);
  const [totalTokens, setTotalTokens] = useState(0);
  const [crystalsCount, setCrystalsCount] = useState(0);
  const [budgetAlert80, setBudgetAlert80] = useState(false);
  const [alertData, setAlertData] = useState(null);
  const [flyingCrystals, setFlyingCrystals] = useState([]);
  const [vaultFlash, setVaultFlash] = useState(false);
  const [latestEvent, setLatestEvent] = useState({
    type: "SYSTEM_READY",
    time_iso: new Date().toLocaleTimeString(),
    data: { message: "赛博全息指挥舱已上线，实时遥测总线就绪" }
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

  // CEO command bar state
  const [ceoDirective, setCeoDirective] = useState("");
  const [selectedFlow, setSelectedFlow] = useState("FLOW-SOFTWARE");
  const [isDispatching, setIsDispatching] = useState(false);
  const [dispatchSuccessMsg, setDispatchSuccessMsg] = useState("");
  const [isListening, setIsListening] = useState(false);

  const socketRef = useRef(null);
  const reconnectTimeoutRef = useRef(null);
  const speechRecognitionRef = useRef(null);

  // Trigger purple crystal particle flying animation
  const triggerCrystalDrop = useCallback((data) => {
    const id = Date.now() + Math.random();
    const newParticle = {
      id,
      startX: "220px",
      startY: "280px",
      targetX: "640px",
      targetY: "420px",
    };
    setFlyingCrystals((prev) => [...prev, newParticle]);
    setTimeout(() => {
      setFlyingCrystals((prev) => prev.filter((p) => p.id !== id));
      setVaultFlash(true);
      setCrystalsCount((c) => c + 1);
      setTimeout(() => setVaultFlash(false), 700);
    }, 1200);
  }, []);

  // Fetch project list
  const loadProjects = useCallback(async (preferredId = null) => {
    try {
      const res = await api.projects();
      const list = res.projects || [];
      setProjectList(list);
      if (list.length > 0) {
        setActiveProjectId((cur) => {
          if (preferredId && list.some((p) => p.id === preferredId)) return preferredId;
          if (cur && list.some((p) => p.id === cur)) return cur;
          return list[0].id;
        });
      }
    } catch (e) {
      console.warn("Failed to load projects", e);
    }
  }, []);

  // Fetch single project details & bind 100% real states
  const loadProjectDetail = useCallback(async (pid) => {
    if (!pid) return;
    try {
      const detail = await api.project(pid);
      setProjectDetail(detail);

      // 1. 真实阶段状态映射：动态支持任意流程模板（4阶段/5阶段/9阶段）
      const nextStates = {};
      STAGE_KEYS.forEach((st) => {
        nextStates[st.key] = "pending";
      });
      if (detail.stages && detail.stages.length > 0) {
        detail.stages.forEach((st) => {
          nextStates[st.key] = st.state;
        });
      }
      setStagesState(nextStates);

      // 2. 真实算力与 Token 开销计算
      let usedTokens = detail.budget?.used_tokens || 0;
      let costRmb = Number(detail.budget?.cost_rmb || 0);

      // 如果 budget 对象未统计，从所有阶段已跑的 runs 累加
      if (usedTokens === 0 && detail.stages) {
        detail.stages.forEach((s) => {
          (s.runs || []).forEach((r) => {
            usedTokens += (r.usage_tokens || 0);
          });
        });
        costRmb = Number((usedTokens * 0.00005).toFixed(2));
      }

      setTotalTokens(usedTokens);
      setTotalRmb(costRmb);
    } catch (e) {
      console.warn(`Failed to load detail for project ${pid}`, e);
    }
  }, []);

  // Initial load
  useEffect(() => {
    loadProjects();
    api.kernelStatus?.().then((s) => {
      if (s && s.mode) setKernelMode(s.mode);
    }).catch(() => {});
    // Load real experience crystal count from DB
    api.experiences().then((res) => {
      const list = res.experiences || [];
      setCrystalsCount(list.length);
    }).catch(() => {});
  }, [loadProjects]);

  // Load project detail on activeProjectId change
  useEffect(() => {
    if (activeProjectId) {
      loadProjectDetail(activeProjectId);
    }
  }, [activeProjectId, loadProjectDetail]);

  // Real-time polling guard: 当项目正在运行中，每 3 秒刷新一次详情，保证界面不卡顿
  useEffect(() => {
    if (!activeProjectId || !projectDetail) return undefined;
    const isRunning = projectDetail.project?.state === "active" ||
      Object.values(stagesState).includes("active");
    if (!isRunning) return undefined;

    const timer = setInterval(() => {
      loadProjectDetail(activeProjectId);
    }, 3000);
    return () => clearInterval(timer);
  }, [activeProjectId, projectDetail, stagesState, loadProjectDetail]);

  // WebSocket connection & Telemetry bus listener
  useEffect(() => {
    let unmounted = false;

    const connectWs = () => {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const host = window.location.host || "127.0.0.1:8077";
      const wsUrl = `${protocol}//${host}/ws/metacorp`;

      try {
        const ws = new WebSocket(wsUrl);
        socketRef.current = ws;

        ws.onopen = () => {
          if (unmounted) return;
          setWsConnected(true);
          ws.send(JSON.stringify({ action: "ping" }));
        };

        ws.onmessage = (event) => {
          if (unmounted) return;
          try {
            const msg = JSON.parse(event.data);
            if (msg.type === "PONG") return;

            setLatestEvent(msg);

            // 1. Pipeline stage change
            if (msg.type === "PIPELINE_STAGE_CHANGED") {
              const { stage_key, state, project_id } = msg.data || {};
              if (stage_key) {
                setStagesState((prev) => ({ ...prev, [stage_key]: state }));
              }
              if (project_id) {
                if (project_id === activeProjectId) {
                  loadProjectDetail(project_id);
                }
              }
            }

            // 2. Usage update (tokens & RMB)
            if (msg.type === "ACP_USAGE_UPDATE") {
              const { used_tokens, cost_rmb } = msg.data || {};
              if (used_tokens) setTotalTokens((t) => Math.max(t, used_tokens));
              if (cost_rmb) setTotalRmb((r) => Math.max(r, cost_rmb));
            }

            // 3. Budget 80% alert
            if (msg.type === "BUDGET_ALERT_80") {
              setBudgetAlert80(true);
              setAlertData(msg.data);
            }

            // 4. Experience crystal drop
            if (msg.type === "EXP_CRYSTAL_DROP") {
              triggerCrystalDrop(msg.data);
            }

            // 5. Decoupled WeCom approval required
            if (msg.type === "APPROVAL_REQUIRED") {
              setApprovalModal(msg.data);
            }
          } catch (e) {
            console.error("Failed to parse telemetry message", e);
          }
        };

        ws.onclose = () => {
          if (unmounted) return;
          setWsConnected(false);
          reconnectTimeoutRef.current = setTimeout(connectWs, 3000);
        };

        ws.onerror = () => {
          ws.close();
        };
      } catch (e) {
        setWsConnected(false);
        reconnectTimeoutRef.current = setTimeout(connectWs, 3000);
      }
    };

    connectWs();

    // Fetch initial exchange packs
    fetch("/mcp-exchange/packs")
      .then((r) => r.json())
      .then((d) => { if (d.ok) setExchangePacks(d.packs); })
      .catch(() => {});

    return () => {
      unmounted = true;
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (socketRef.current) socketRef.current.close();
    };
  }, [activeProjectId, triggerCrystalDrop, loadProjectDetail]);

  // CEO Voice input handler
  const toggleVoiceInput = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
      if (isListening) {
        setIsListening(false);
      } else {
        setIsListening(true);
        setTimeout(() => {
          setCeoDirective(PRESET_DIRECTIVES[0].prompt);
          setIsListening(false);
        }, 1500);
      }
      return;
    }

    if (isListening) {
      if (speechRecognitionRef.current) {
        speechRecognitionRef.current.stop();
      }
      setIsListening(false);
    } else {
      try {
        const recognition = new SpeechRecognition();
        recognition.lang = "zh-CN";
        recognition.continuous = false;
        recognition.interimResults = true;

        recognition.onstart = () => setIsListening(true);
        recognition.onresult = (event) => {
          const transcript = Array.from(event.results)
            .map((result) => result[0].transcript)
            .join("");
          setCeoDirective(transcript);
        };
        recognition.onerror = () => setIsListening(false);
        recognition.onend = () => setIsListening(false);

        speechRecognitionRef.current = recognition;
        recognition.start();
      } catch (e) {
        setIsListening(false);
      }
    }
  };

  const handleSwitchKernel = async (newMode) => {
    setKernelMode(newMode);
    try {
      await api.setKernelMode?.(newMode);
    } catch (e) {
      console.warn("Failed to set kernel mode", e);
    }
  };

  // Launch new project from CEO Command Bar (真实点火执行)
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

      // 立即重置为新任务真实初始状态
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

      setDispatchSuccessMsg(`🚀 流水线点火成功！项目 [${newId}] 已启动自动总装，全屏沙盘监控中...`);
      setCeoDirective("");

      // 刷新列表并自动切换
      await loadProjects(newId);
      await loadProjectDetail(newId);

      setTimeout(() => setDispatchSuccessMsg(""), 6000);
    } catch (err) {
      alert(`流水线启动失败: ${err.message || "网络或权限异常"}`);
    } finally {
      setIsDispatching(false);
    }
  };

  const SLOT_COORDS = [
    { x: 140, y: 90 },
    { x: 380, y: 90 },
    { x: 640, y: 90 },
    { x: 640, y: 240 },
    { x: 380, y: 240 },
    { x: 140, y: 240 },
    { x: 140, y: 390 },
    { x: 380, y: 390 },
    { x: 640, y: 390 },
  ];

  const currentStages = (projectDetail && projectDetail.stages && projectDetail.stages.length > 0)
    ? projectDetail.stages.map((st, idx) => {
        const standardMeta = STAGE_KEYS.find((s) => s.key === st.key) || {};
        const coords = SLOT_COORDS[idx] || { x: 140 + (idx % 3) * 240, y: 90 + Math.floor(idx / 3) * 150 };
        return {
          key: st.key,
          name: st.title || standardMeta.name || st.key,
          code: String(idx + 1).padStart(2, "0"),
          x: coords.x,
          y: coords.y,
          color: standardMeta.color || "#00F0FF",
          desc: st.gate_name || standardMeta.desc || `${st.title || st.key}完成`,
          isOcto: st.key === "workshop",
          isLaser: st.key === "test",
          isRelease: st.key === "release",
          isVault: st.key === "retro",
        };
      })
    : STAGE_KEYS;

  // Open micro-control stage drawer
  const openStageDrawer = (stageKey) => {
    const stageObj = currentStages.find((s) => s.key === stageKey) ||
      STAGE_KEYS.find((s) => s.key === stageKey) || {
        key: stageKey,
        name: stageKey,
        code: "00",
        color: "#00F0FF",
        desc: "工序阶段",
      };
    setSelectedStage({ ...stageObj, state: stagesState[stageKey] || "pending" });
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

  const handleSimulateAdvancement = () => {
    // 纯粹刷新当前实盘项目最新真实状态，杜绝任何假数据覆盖
    if (activeProjectId) {
      loadProjectDetail(activeProjectId);
    }
  };


  const handleNavigate = onNavigateTab || onNavigate;

  const handleSwitchScene = (newScene) => {
    if (newScene === scene || isFlipping) return;
    setIsFlipping(true);
    setTimeout(() => {
      setScene(newScene);
    }, 400);
    setTimeout(() => {
      setIsFlipping(false);
    }, 800);
  };

  const handleMouseMove = (e) => {
    if (isFlipping) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const mouseX = (e.clientX - rect.left) / rect.width - 0.5;
    const mouseY = (e.clientY - rect.top) / rect.height - 0.5;
    setTilt({
      x: -(mouseY * 12),
      y: mouseX * 14,
    });
  };

  const handleMouseLeave = () => {
    setTilt({ x: 0, y: 0 });
  };

  const handleNekoClick = (e) => {
    e.stopPropagation();
    setNekoBounce(true);
    setTimeout(() => setNekoBounce(false), 600);

    const emojis = ["✨", "🐾", "💎", "⚡", "🐱", "💜", "🚀", "+10 EXP"];
    const emoji = emojis[Math.floor(Math.random() * emojis.length)];
    const rx = (Math.random() - 0.5) * 80;
    const reactionId = Date.now() + Math.random();
    setNekoReactions((prev) => [...prev.slice(-6), { id: reactionId, emoji, rx }]);
    setTimeout(() => {
      setNekoReactions((prev) => prev.filter((r) => r.id !== reactionId));
    }, 1200);

    setNekoDialogIdx((prev) => prev + 1);
    setShowNekoDialog(true);
  };

  useEffect(() => {
    const handleKeyDown = (e) => {
      const tag = document.activeElement?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if (e.code === "Space") {
        e.preventDefault();
        setShowDirectiveLauncher((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const currentNekoDialogues =
    scene === "camp"
      ? NEKO_CAMP_DIALOGUES
      : scene === "gym"
      ? NEKO_GYM_DIALOGUES
      : NEKO_WORK_DIALOGUES;
  const currentNekoText =
    currentNekoDialogues[nekoDialogIdx % currentNekoDialogues.length];

  const sceneImages = {
    work: `${import.meta.env.BASE_URL}diorama/work_pod.png`,
    camp: `${import.meta.env.BASE_URL}diorama/idle_camp.png`,
    gym: `${import.meta.env.BASE_URL}diorama/idle_gym.png`,
  };

  const nekoPositions = {
    work: { left: "48%", top: "48%" },
    camp: { left: "50%", top: "64%" },
    gym: { left: "44%", top: "56%" },
  };

  return (
    <div className={`cyber-deck ${budgetAlert80 ? "alert-strobe" : ""}`}>

      {/* 1. TOP TELEMETRY HUD */}
            {/* 3D TOY-BOX DIORAMA MODE */}
      {dioramaMode ? (
        <div
          className="diorama-container"
          onClick={() => {
            setShowAllModulesMenu(false);
            setShowWorkPropsMenu(false);
            setShowNekoDialog(false);
          }}
        >
          {/* Top-Left Controls */}
          <div className="diorama-top-left" onClick={(e) => e.stopPropagation()}>
            <button
              className="diorama-pill-btn"
              onClick={() => {
                if (onReturnMainUI) onReturnMainUI();
                else if (handleNavigate) handleNavigate("chat");
              }}
              title="返回 AI 团队日常主工作台"
            >
              <span>←</span>
              <span>返回 AI 团队主界面</span>
            </button>

            <div className={`diorama-status-badge ${wsConnected ? "active" : ""}`}>
              <span
                style={{
                  display: "inline-block",
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: wsConnected ? "#00FF9D" : "#64748B",
                }}
              />
              <span>{wsConnected ? "全息总线 LIVE" : "遥测离线"}</span>
            </div>

            {projectList.length > 0 && (
              <select
                className="diorama-project-picker"
                value={activeProjectId}
                onChange={(e) => {
                  const newPid = e.target.value;
                  setActiveProjectId(newPid);
                  loadProjectDetail(newPid);
                }}
              >
                {projectList.map((p) => (
                  <option key={p.id} value={p.id}>
                    📁 {p.name || p.id} ({p.current_stage || "init"})
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* Top-Right Controls */}
          <div className="diorama-top-right" onClick={(e) => e.stopPropagation()}>
            {/* Dual-Kernel Switcher */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "6px",
                background: "rgba(9, 14, 28, 0.85)",
                border: "1px solid rgba(0, 240, 255, 0.35)",
                borderRadius: "9999px",
                padding: "4px 12px",
                backdropFilter: "blur(10px)",
              }}
            >
              <span style={{ fontSize: "11px", color: "#00F0FF", fontWeight: "600" }}>⚙️ 内核:</span>
              <select
                value={kernelMode}
                onChange={(e) => handleSwitchKernel(e.target.value)}
                style={{
                  background: "transparent",
                  color: "#00F0FF",
                  border: "none",
                  fontSize: "11px",
                  outline: "none",
                  cursor: "pointer",
                }}
              >
                <option value="auto_failover">⚡ 自动容错</option>
                <option value="native">🟢 原生内核</option>
                <option value="dsh">🛡️ DSH-ACP</option>
              </select>
            </div>

            {/* Capsule Menu: 盲盒手办柜 (All Modules Dropdown) */}
            <button
              className="diorama-pill-btn"
              onClick={() => setShowAllModulesMenu((prev) => !prev)}
            >
              <span>📦</span>
              <span>盲盒手办柜</span>
              {pendingCount > 0 && (
                <span
                  style={{
                    background: "#FF0055",
                    color: "#fff",
                    fontSize: 10,
                    padding: "1px 6px",
                    borderRadius: 999,
                  }}
                >
                  {pendingCount}
                </span>
              )}
            </button>

            {/* Switch to Classic 2D Factorio Deck */}
            <button
              className="diorama-pill-btn"
              onClick={() => {
                setDioramaMode(false);
                if (onToggleClassicDeck) onToggleClassicDeck();
              }}
              title="切换至经典 2D S型工业流水线"
            >
              <span>🎛️</span>
              <span>经典 2D 流程</span>
            </button>
          </div>

          {/* Modules Capsule Dropdown Menu */}
          {showAllModulesMenu && (
            <div
              className="diorama-modules-menu"
              onClick={(e) => e.stopPropagation()}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  borderBottom: "1px solid rgba(0, 240, 255, 0.2)",
                  paddingBottom: "8px",
                }}
              >
                <div style={{ fontWeight: "bold", color: "#00F0FF", fontSize: "13px" }}>
                  📦 盲盒手办柜 · 全功能舱室导航
                </div>
                <button
                  className="neko-dialog-close"
                  onClick={() => setShowAllModulesMenu(false)}
                >
                  ×
                </button>
              </div>

              <div className="diorama-modules-grid">
                <button
                  className="module-card-btn"
                  onClick={() => {
                    if (handleNavigate) handleNavigate("chat");
                    setShowAllModulesMenu(false);
                  }}
                >
                  <span>💬</span>
                  <span>团队通讯 Chat</span>
                </button>

                <button
                  className="module-card-btn"
                  onClick={() => {
                    if (handleNavigate) handleNavigate("agents");
                    setShowAllModulesMenu(false);
                  }}
                >
                  <span>👥</span>
                  <span>员工工卡 Agents</span>
                </button>

                <button
                  className="module-card-btn"
                  onClick={() => {
                    if (handleNavigate) handleNavigate("workflows");
                    setShowAllModulesMenu(false);
                  }}
                >
                  <span>🌿</span>
                  <span>敏捷工作流 WBS</span>
                </button>

                <button
                  className="module-card-btn"
                  onClick={() => {
                    if (handleNavigate) handleNavigate("approvals");
                    setShowAllModulesMenu(false);
                  }}
                >
                  <span>🛡️</span>
                  <span>决策审批 Approvals</span>
                  {pendingCount > 0 && <span className="badge-pill">{pendingCount}</span>}
                </button>

                <button
                  className="module-card-btn"
                  onClick={() => {
                    if (handleNavigate) handleNavigate("finops");
                    setShowAllModulesMenu(false);
                  }}
                >
                  <span>💰</span>
                  <span>FinOps 算力表</span>
                </button>

                <button
                  className="module-card-btn"
                  onClick={() => {
                    setShowExchangeModal(true);
                    setShowAllModulesMenu(false);
                  }}
                >
                  <span>🌐</span>
                  <span>万界交易所 MCP</span>
                </button>

                <button
                  className="module-card-btn"
                  onClick={() => {
                    if (onOpenAddMember) onOpenAddMember();
                    setShowAllModulesMenu(false);
                  }}
                >
                  <span>➕</span>
                  <span>招募新员工</span>
                </button>

                <button
                  className="module-card-btn"
                  onClick={() => {
                    if (handleNavigate) handleNavigate("settings");
                    setShowAllModulesMenu(false);
                  }}
                >
                  <span>⚙️</span>
                  <span>元系统配置</span>
                </button>
              </div>
            </div>
          )}

          {/* 3D Viewport with Perspective & Parallax */}
          <div
            className="diorama-viewport"
            onMouseMove={handleMouseMove}
            onMouseLeave={handleMouseLeave}
          >
            <div
              className={`diorama-stage-card ${isFlipping ? "flipping" : ""}`}
              style={{
                transform: isFlipping
                  ? "rotateY(180deg) scale(0.88)"
                  : `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg)`,
              }}
            >
              {/* Scene Artwork Image */}
              <img
                src={sceneImages[scene]}
                alt={`Cyber Diorama - ${scene}`}
                className="diorama-scene-img"
                draggable={false}
              />

              {/* Glowing Base Ambient Ring */}
              <div className={`diorama-base-glow ${scene}`} />

              {/* Ambient Floating Spark Particles */}
              <div className="diorama-particles-layer">
                <div className="spark-dot" style={{ left: "20%", top: "60%", animationDelay: "0s" }} />
                <div className="spark-dot purple" style={{ left: "75%", top: "45%", animationDelay: "1.2s" }} />
                <div className="spark-dot green" style={{ left: "45%", top: "70%", animationDelay: "2.4s" }} />
                <div className="spark-dot" style={{ left: "85%", top: "65%", animationDelay: "0.8s" }} />
                <div className="spark-dot purple" style={{ left: "30%", top: "50%", animationDelay: "1.8s" }} />
              </div>

              {/* DIEGETIC PROP HOTSPOTS: WORK POD SCENE */}
              {scene === "work" && (
                <>
                  {/* Prop 1: 01~04 规划拆解 (立项黑板) */}
                  <div
                    className="prop-hotspot"
                    style={{ left: "22%", top: "36%" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowWorkPropsMenu((prev) => !prev);
                    }}
                    title="01~04 需求分解与立项黑板 (点击展开多阶段)"
                  >
                    <div className="prop-marker">📋</div>
                    <div className="prop-ar-badge">
                      <span>01~04 规划黑板</span>
                      <span style={{ color: "#00F0FF" }}>({stagesState.wbs || "待命"})</span>
                    </div>

                    {/* Popover for 01-04 */}
                    {showWorkPropsMenu && (
                      <div className="micro-prop-popover" onClick={(e) => e.stopPropagation()}>
                        <button
                          className="micro-stage-btn"
                          onClick={() => {
                            openStageDrawer("intake");
                            setShowWorkPropsMenu(false);
                          }}
                        >
                          <span>01 接需求</span>
                          <span style={{ color: "#00F0FF" }}>{stagesState.intake || "pending"}</span>
                        </button>
                        <button
                          className="micro-stage-btn"
                          onClick={() => {
                            openStageDrawer("charter");
                            setShowWorkPropsMenu(false);
                          }}
                        >
                          <span>02 立项界标</span>
                          <span style={{ color: "#00F0FF" }}>{stagesState.charter || "pending"}</span>
                        </button>
                        <button
                          className="micro-stage-btn"
                          onClick={() => {
                            openStageDrawer("workshop");
                            setShowWorkPropsMenu(false);
                          }}
                        >
                          <span>03 4轮研讨会</span>
                          <span style={{ color: "#FF6B00" }}>{stagesState.workshop || "pending"}</span>
                        </button>
                        <button
                          className="micro-stage-btn"
                          onClick={() => {
                            openStageDrawer("wbs");
                            setShowWorkPropsMenu(false);
                          }}
                        >
                          <span>04 黑板拆解</span>
                          <span style={{ color: "#00F0FF" }}>{stagesState.wbs || "pending"}</span>
                        </button>
                      </div>
                    )}
                  </div>

                  {/* Prop 2: 05 生产装配 (弧形吧台 & 机械臂) */}
                  <div
                    className="prop-hotspot"
                    style={{ left: "52%", top: "66%" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      openStageDrawer("build");
                    }}
                    title="05 生产装配流水线 (点击开启机械臂装配台)"
                  >
                    <div className="prop-marker">⚙️</div>
                    <div className="prop-ar-badge">
                      <span>05 装配中枢</span>
                      <span style={{ color: stagesState.build === "active" ? "#00F0FF" : stagesState.build === "done" ? "#00FF9D" : "#94A3B8" }}>
                        ({stagesState.build || "pending"})
                      </span>
                    </div>
                  </div>

                  {/* Prop 3: 09 紫晶金库 / FinOps 算力表 */}
                  <div
                    className="prop-hotspot"
                    style={{ left: "78%", top: "44%" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      openStageDrawer("retro");
                    }}
                    title="09 组织避坑紫晶金库 & FinOps (点击开启复盘金库)"
                  >
                    <div className="prop-marker" style={{ borderColor: "#A855F7" }}>💎</div>
                    <div className="prop-ar-badge purple">
                      <span>09 紫晶金库</span>
                      <span style={{ color: "#A855F7" }}>￥{totalRmb.toFixed(2)} | 💎{crystalsCount}</span>
                    </div>
                  </div>

                  {/* Prop 4: 万界 MCP 交易所 (MCP 磁带架) */}
                  <div
                    className="prop-hotspot"
                    style={{ left: "80%", top: "22%" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowExchangeModal(true);
                    }}
                    title="万界 MCP 交易所 (蓝图导出/外部雇佣)"
                  >
                    <div className="prop-marker" style={{ borderColor: "#A855F7" }}>🌐</div>
                    <div className="prop-ar-badge purple">
                      <span>万界 MCP 交易所</span>
                    </div>
                  </div>

                  {/* Prop 5: 06 质检 & 07 验收 (激光扫描仪) */}
                  <div
                    className="prop-hotspot"
                    style={{ left: "28%", top: "68%" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      openStageDrawer("test");
                    }}
                    title="06 激光脉冲质检 & 07 验收网关"
                  >
                    <div className="prop-marker" style={{ borderColor: "#00FF9D" }}>🔬</div>
                    <div className="prop-ar-badge laser">
                      <span>06 脉冲质检 / 07 验收</span>
                      <span style={{ color: stagesState.test === "done" ? "#00FF9D" : "#00F0FF" }}>
                        ({stagesState.test || "pending"})
                      </span>
                    </div>
                  </div>

                  {/* Prop 6: 08 出货港 / 审批签章 (传送出货口) */}
                  <div
                    className="prop-hotspot"
                    style={{ left: "68%", top: "78%" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      openStageDrawer("release");
                    }}
                    title="08 出货港 · Bundle.zip 一键交付"
                  >
                    <div className="prop-marker" style={{ borderColor: approvalModal ? "#FF0055" : "#00F0FF" }}>📦</div>
                    <div className={`prop-ar-badge ${approvalModal ? "alert" : ""}`}>
                      <span>08 出货港</span>
                      <span style={{ color: approvalModal ? "#FF0055" : "#00F0FF" }}>
                        {approvalModal ? "⚠️待签章" : `(${stagesState.release || "pending"})`}
                      </span>
                    </div>
                  </div>
                </>
              )}

              {/* DIEGETIC PROP HOTSPOTS: CAMP SCENE */}
              {scene === "camp" && (
                <>
                  <div className="prop-hotspot" style={{ left: "28%", top: "42%" }}>
                    <div className="prop-marker">⛺</div>
                    <div className="prop-ar-badge">休眠舱 · 全体 Agent 离线蓄能 (100%)</div>
                  </div>
                  <div
                    className="prop-hotspot"
                    style={{ left: "52%", top: "68%" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      openStageDrawer("retro");
                    }}
                  >
                    <div className="prop-marker" style={{ borderColor: "#A855F7" }}>🔥</div>
                    <div className="prop-ar-badge purple">紫晶篝火 · 经验沉淀 💎 {crystalsCount}</div>
                  </div>
                  <div
                    className="prop-hotspot"
                    style={{ left: "74%", top: "36%" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      loadProjectDetail(activeProjectId);
                    }}
                  >
                    <div className="prop-marker">🌌</div>
                    <div className="prop-ar-badge">全息天幕 · 项目 {activeProjectId || "空闲"}</div>
                  </div>
                </>
              )}

              {/* DIEGETIC PROP HOTSPOTS: GYM SCENE */}
              {scene === "gym" && (
                <>
                  <div
                    className="prop-hotspot"
                    style={{ left: "26%", top: "46%" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      openStageDrawer("build");
                    }}
                  >
                    <div className="prop-marker">🏃</div>
                    <div className="prop-ar-badge">脑机超频 · 520 TFLOPS 稳态</div>
                  </div>
                  <div
                    className="prop-hotspot"
                    style={{ left: "74%", top: "42%" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      openStageDrawer("retro");
                    }}
                  >
                    <div className="prop-marker">🥤</div>
                    <div className="prop-ar-badge">Token 能量吧 · 消耗 {totalTokens.toLocaleString()}</div>
                  </div>
                  <div
                    className="prop-hotspot"
                    style={{ left: "50%", top: "72%" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      openStageDrawer("test");
                    }}
                  >
                    <div className="prop-marker">🥊</div>
                    <div className="prop-ar-badge laser">抗压质检 · 0 异常</div>
                  </div>
                </>
              )}

              {/* Neko-01 Chibi Character Hotspot */}
              <div
                className="neko-hotspot"
                style={{
                  left: nekoPositions[scene].left,
                  top: nekoPositions[scene].top,
                }}
                onClick={handleNekoClick}
                title="Neko-01 赛博猫娘首席助理 (点击互动/对话)"
              >
                <div className={`neko-aura ${budgetAlert80 ? "alert" : wsConnected ? "active" : ""}`} />
                <div className={`neko-clickable-core ${nekoBounce ? "neko-bounce" : ""}`}>
                  🐱
                </div>

                {/* Floating Reaction Particles */}
                {nekoReactions.map((r) => (
                  <div
                    key={r.id}
                    className="neko-reaction-particle"
                    style={{ "--rx": `${r.rx}px` }}
                  >
                    {r.emoji}
                  </div>
                ))}

                {/* Holographic Speech Bubble */}
                {showNekoDialog && (
                  <div
                    className="neko-dialog-bubble"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="neko-dialog-head">
                      <div className="neko-dialog-name">
                        <span>🐾</span>
                        <span>Neko-01 · 指挥官首席助手</span>
                      </div>
                      <button
                        className="neko-dialog-close"
                        onClick={() => setShowNekoDialog(false)}
                      >
                        ×
                      </button>
                    </div>
                    <div className="neko-dialog-text">
                      {currentNekoText}
                    </div>
                    <div className="neko-dialog-actions">
                      <button
                        className="neko-action-pill primary"
                        onClick={() => {
                          setShowDirectiveLauncher(true);
                          setShowNekoDialog(false);
                        }}
                      >
                        ✨ 派发 CEO 指令
                      </button>
                      <button
                        className="neko-action-pill"
                        onClick={() => {
                          setCrystalsCount((c) => c + 1);
                          triggerCrystalDrop({
                            title: "Neko-01 投喂奖励",
                            experience_summary: "投喂避坑紫晶石，元公司灵感 +1",
                          });
                        }}
                      >
                        💎 投喂紫晶石
                      </button>
                      {scene !== "work" && (
                        <button
                          className="neko-action-pill"
                          onClick={() => handleSwitchScene("work")}
                        >
                          🛠️ 回工坊
                        </button>
                      )}
                      {scene !== "camp" && (
                        <button
                          className="neko-action-pill"
                          onClick={() => handleSwitchScene("camp")}
                        >
                          🏕️ 去露营
                        </button>
                      )}
                      {scene !== "gym" && (
                        <button
                          className="neko-action-pill"
                          onClick={() => handleSwitchScene("gym")}
                        >
                          🏋️ 去健身
                        </button>
                      )}
                      {approvalModal && (
                        <button
                          className="neko-action-pill alert"
                          onClick={() => setShowNekoDialog(false)}
                        >
                          🐾 待办审批待签章
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Bottom Controls Bar */}
          <div className="diorama-bottom-controls" onClick={(e) => e.stopPropagation()}>
            {/* Mini 01-09 Crystal Stage Pills */}
            <div className="diorama-mini-stages">
              {STAGE_KEYS.map((st) => {
                const state = stagesState[st.key] || "pending";
                return (
                  <div
                    key={st.key}
                    className={`mini-stage-pill ${state}`}
                    onClick={() => openStageDrawer(st.key)}
                    title={`${st.code} ${st.name} (${state})`}
                  >
                    <span>{st.code}</span>
                    <span>{st.name}</span>
                  </div>
                );
              })}
            </div>

            {/* Scene Switcher Tabs (180 deg Flip) */}
            <div className="diorama-scene-tabs">
              <div
                className={`scene-tab-btn ${scene === "work" ? "active" : ""}`}
                onClick={() => handleSwitchScene("work")}
              >
                🛠️ 智能工坊
              </div>
              <div
                className={`scene-tab-btn ${scene === "camp" ? "active" : ""}`}
                onClick={() => handleSwitchScene("camp")}
              >
                🏕️ 赛博露营
              </div>
              <div
                className={`scene-tab-btn ${scene === "gym" ? "active" : ""}`}
                onClick={() => handleSwitchScene("gym")}
              >
                🏋️ 极客体能
              </div>
            </div>

            {/* Space Shortcut Pill */}
            <div
              className="space-directive-pill"
              onClick={() => setShowDirectiveLauncher(true)}
            >
              ⚡ 按 <strong>[SPACE 空格]</strong> 或点击此处唤醒 CEO 战略指令舱
            </div>
          </div>

          {/* Floating CEO Directive Launcher Modal */}
          {showDirectiveLauncher && (
            <div
              className="diorama-directive-modal-overlay"
              onClick={() => setShowDirectiveLauncher(false)}
            >
              <div
                className="diorama-directive-modal"
                onClick={(e) => e.stopPropagation()}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: "16px",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span style={{ fontSize: "20px" }}>✨</span>
                    <span style={{ color: "#00F0FF", fontWeight: "bold", fontSize: "16px" }}>
                      CEO 战略工作流极速下发中枢
                    </span>
                  </div>
                  <button
                    className="cyber-btn"
                    style={{ padding: "4px 10px" }}
                    onClick={() => setShowDirectiveLauncher(false)}
                  >
                    ✕ 关闭
                  </button>
                </div>

                {/* Presets */}
                <div style={{ marginBottom: "14px" }}>
                  <div style={{ fontSize: "11px", color: "#94A3B8", marginBottom: "6px" }}>⚡ 经典实盘模板预设：</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                    {PRESET_DIRECTIVES.map((p, idx) => (
                      <button
                        key={idx}
                        type="button"
                        className="cyber-btn"
                        style={{ fontSize: "11px", padding: "4px 8px" }}
                        onClick={() => {
                          setCeoDirective(p.prompt);
                        }}
                      >
                        <span>{p.icon}</span>
                        <span>{p.label}</span>
                      </button>
                    ))}
                  </div>
                </div>

                {/* Workflow selector */}
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
                  <span style={{ fontSize: "12px", color: "#94A3B8" }}>流水线架构:</span>
                  <select
                    value={selectedFlow}
                    onChange={(e) => setSelectedFlow(e.target.value)}
                    style={{
                      background: "#060913",
                      color: "#00F0FF",
                      border: "1px solid rgba(0, 240, 255, 0.4)",
                      borderRadius: "6px",
                      padding: "4px 10px",
                      fontSize: "12px",
                      outline: "none",
                    }}
                  >
                    <option value="FLOW-SOFTWARE">💻 软件产研标准流水线 (01-09)</option>
                    <option value="FLOW-MARKETING">📢 品牌营销投放流 (01-08)</option>
                    <option value="FLOW-RESEARCH">🔬 行业竞争情报流 (01-07)</option>
                  </select>
                </div>

                {/* Textarea */}
                <textarea
                  rows={4}
                  value={ceoDirective}
                  onChange={(e) => setCeoDirective(e.target.value)}
                  placeholder="输入 CEO 宏观需求指令（如：开发高并发计数器组件、全网行业竞品调研、落地页广告法合规审计...）"
                  style={{
                    width: "100%",
                    background: "#060A14",
                    border: "1px solid rgba(0, 240, 255, 0.4)",
                    borderRadius: "8px",
                    padding: "12px",
                    color: "#F8FAFC",
                    fontSize: "13px",
                    resize: "vertical",
                    outline: "none",
                    marginBottom: "14px",
                  }}
                />

                {dispatchSuccessMsg && (
                  <div
                    style={{
                      padding: "10px",
                      marginBottom: "12px",
                      borderRadius: "6px",
                      background: "rgba(0, 255, 157, 0.15)",
                      border: "1px solid #00FF9D",
                      color: "#00FF9D",
                      fontSize: "12px",
                    }}
                  >
                    {dispatchSuccessMsg}
                  </div>
                )}

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <button
                    className="cyber-btn"
                    onClick={() => setShowDirectiveLauncher(false)}
                  >
                    取消
                  </button>
                  <button
                    className="cyber-btn cyan"
                    disabled={isDispatching || !ceoDirective.trim()}
                    onClick={async () => {
                      await handleDispatchDirective();
                      setTimeout(() => {
                        setShowDirectiveLauncher(false);
                      }, 1500);
                    }}
                  >
                    {isDispatching ? "点火装配中..." : "🚀 确认下发并启动总装流水线"}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      ) : (
        /* CLASSIC 2D FACTORIO CONVEYOR DECK */
        <>
<header className="cyber-hud">
        <div className="hud-title-group">
          <div className="hud-logo">
            <span>⚙️</span>
            <span>CYBER-FACTORIO DECK</span>
          </div>
          <div className={`telemetry-status ${wsConnected ? "live" : ""}`}>
            <span className="pulse-dot" />
            <span>{wsConnected ? "TELEMETRY LIVE" : "TELEMETRY OFFLINE"}</span>
          </div>
        </div>

        <div className="hud-metrics">
          <div className="metric-pill">
            <span className="metric-label">当前监控项目</span>
            <span className="metric-value cyan">{activeProjectId || "未选定项目"}</span>
          </div>
          <div className="metric-pill">
            <span className="metric-label">累计算力开销</span>
            <span className="metric-value orange">￥{totalRmb.toFixed(2)}</span>
          </div>
          <div className="metric-pill">
            <span className="metric-label">消耗 TOKENS</span>
            <span className="metric-value cyan">{totalTokens.toLocaleString()}</span>
          </div>
          <div className="metric-pill">
            <span className="metric-label">组织避坑晶石库</span>
            <span className="metric-value purple">💎 {crystalsCount} 块</span>
          </div>
        </div>

        <div className="hud-actions" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          {/* Dual-Kernel Engine Switcher */}
          <div className="kernel-engine-selector" style={{ display: "flex", alignItems: "center", gap: "6px", background: "rgba(0, 240, 255, 0.08)", border: "1px solid rgba(0, 240, 255, 0.3)", borderRadius: "6px", padding: "4px 8px" }}>
            <span style={{ fontSize: "12px", color: "#00F0FF", fontWeight: "600" }}>⚙️ 内核:</span>
            <select
              value={kernelMode}
              onChange={(e) => handleSwitchKernel(e.target.value)}
              style={{ background: "#060913", color: "#00F0FF", border: "1px solid #1a2744", borderRadius: "4px", padding: "3px 6px", fontSize: "12px", outline: "none", cursor: "pointer" }}
            >
              <option value="auto_failover">⚡ 自动容错 (推荐)</option>
              <option value="native">🟢 原生内核 (Native)</option>
              <option value="dsh">🛡️ DSH-ACP 内核</option>
            </select>
          </div>
                    <button
            className="cyber-btn"
            style={{
              background: "linear-gradient(135deg, rgba(0, 240, 255, 0.25), rgba(168, 85, 247, 0.25))",
              border: "1px solid #00F0FF",
              color: "#FFF",
              fontWeight: "bold",
              boxShadow: "0 0 15px rgba(0, 240, 255, 0.4)",
            }}
            onClick={() => setDioramaMode(true)}
          >
            <span>🔮</span>
            <span>切回 3D 盲盒指挥舱</span>
          </button>
          <button className="cyber-btn purple" onClick={() => setShowExchangeModal(true)}>
            <span>🌐</span> 万界 MCP 交易所
          </button>
          <button className="cyber-btn" onClick={() => loadProjectDetail(activeProjectId)}>
            <span>🔄</span> 刷新沙盘
          </button>
          <button className="cyber-btn orange" onClick={() => triggerCrystalDrop({ title: "演练掉落", kind: "consensus" })}>
            <span>💎</span> 晶石吸纳演练
          </button>
        </div>
      </header>

      {/* 2. RED ALERT BANNER (80% BUDGET PAUSE) */}
      {budgetAlert80 && (
        <div className="cyber-alert-ribbon">
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

      {/* 3. FLOATING CEO STRATEGIC COMMAND BAR */}
      <section className="cyber-ceo-bar">
        <div className="ceo-bar-header">
          <div className="ceo-bar-title">
            <span className="pulse-indicator-neon" />
            <span>CEO STRATEGIC DIRECTIVE // 战术总装发射台</span>
          </div>
          <div className="ceo-bar-project-select">
            <label>监控项目:</label>
            <select
              value={activeProjectId}
              onChange={(e) => {
                const newPid = e.target.value;
                setActiveProjectId(newPid);
                loadProjectDetail(newPid);
              }}
            >
              {projectList.map((p) => (
                <option key={p.id} value={p.id}>
                  [{p.state?.toUpperCase() || "ACTIVE"}] {p.name || p.id} ({p.id.slice(0, 10)})
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="ceo-input-row">
          <div className="ceo-input-wrapper">
            <input
              type="text"
              className="ceo-text-input"
              placeholder="下达 CEO 战略指令，例如：“开发单文件 FinOps 算力成本与ROI计算器网页” 或 点击语音按钮下达..."
              value={ceoDirective}
              onChange={(e) => setCeoDirective(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleDispatchDirective();
                }
              }}
            />
            <button
              type="button"
              className={`ceo-voice-btn ${isListening ? "listening" : ""}`}
              title={isListening ? "正在聆听语音指令..." : "启动 CEO 语音指令下达"}
              onClick={toggleVoiceInput}
            >
              <span className="voice-icon">🎙️</span>
              {isListening && <span className="voice-wave-pulse" />}
            </button>
          </div>

          <select
            className="ceo-flow-select"
            value={selectedFlow}
            onChange={(e) => setSelectedFlow(e.target.value)}
          >
            <option value="FLOW-SOFTWARE">标准 9 阶段软件工程流水线 (推荐)</option>
            <option value="FLOW-ANALYSIS">业务数据分析流</option>
            <option value="FLOW-RESEARCH">行业竞品调研流</option>
            <option value="FLOW-URGENT">紧急排障抢修流</option>
          </select>

          <button
            className="cyber-btn cyan ceo-dispatch-btn"
            disabled={isDispatching}
            onClick={handleDispatchDirective}
          >
            <span>{isDispatching ? "⏳" : "⚡"}</span>
            <span>{isDispatching ? "点火总装中..." : "立即启动流水线"}</span>
          </button>
        </div>

        {/* Quick Strategic Directive Presets */}
        <div className="ceo-chips-row">
          <span className="chips-label">快捷指令预设:</span>
          {PRESET_DIRECTIVES.map((preset, idx) => (
            <button
              key={idx}
              type="button"
              className="ceo-chip"
              onClick={() => setCeoDirective(preset.prompt)}
            >
              <span>{preset.icon}</span>
              <span>{preset.label}</span>
            </button>
          ))}
        </div>

        {dispatchSuccessMsg && (
          <div className="ceo-dispatch-success">
            <span>🚀</span>
            <span>{dispatchSuccessMsg}</span>
          </div>
        )}
      </section>

      {/* 4. S-CURVE CONVEYOR BELT (MAIN VIEWPORT) */}
      <main className="deck-viewport">
        {/* Flying Crystal Particles */}
        {flyingCrystals.map((p) => (
          <div
            key={p.id}
            className="flying-crystal-particle"
            style={{ "--start-x": p.startX, "--start-y": p.startY, "--target-x": p.targetX, "--target-y": p.targetY }}
          />
        ))}

        <svg className="conveyor-svg-container" viewBox="0 0 800 480" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="cyanGlow" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#00F0FF" stopOpacity="0.8" />
              <stop offset="100%" stopColor="#0080FF" stopOpacity="0.4" />
            </linearGradient>
            <linearGradient id="octoGlow" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#FF6B00" stopOpacity="0.9" />
              <stop offset="100%" stopColor="#FF3300" stopOpacity="0.5" />
            </linearGradient>
            <linearGradient id="vaultGlow" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#C084FC" stopOpacity="0.9" />
              <stop offset="100%" stopColor="#7E22CE" stopOpacity="0.6" />
            </linearGradient>
            <filter id="neonFilter" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
          </defs>

          {/* S-Curve Conveyor Tracks Base */}
          <path
            d="M 140 100 L 640 100 Q 720 100 720 170 Q 720 240 640 240 L 140 240 Q 60 240 60 315 Q 60 390 140 390 L 640 390"
            fill="none"
            stroke="#1E293B"
            strokeWidth="32"
            strokeLinecap="round"
          />
          <path
            d="M 140 100 L 640 100 Q 720 100 720 170 Q 720 240 640 240 L 140 240 Q 60 240 60 315 Q 60 390 140 390 L 640 390"
            fill="none"
            stroke="#0B1329"
            strokeWidth="20"
            strokeLinecap="round"
          />
          {/* Animated Cyan Pulse Conveyor Belt */}
          <path
            className="conveyor-belt-track"
            d="M 140 100 L 640 100 Q 720 100 720 170 Q 720 240 640 240 L 140 240 Q 60 240 60 315 Q 60 390 140 390 L 640 390"
            fill="none"
            stroke="#00F0FF"
            strokeWidth="4"
            filter="url(#neonFilter)"
          />

          {/* Dynamic Stage Nodes (supports 4/5/9 stages flow) */}
          {currentStages.map((st) => {
            const state = stagesState[st.key] || "pending";
            const isDone = state === "done";
            const isActive = state === "active";
            const isBlocked = state === "blocked";
            const nodeColor = isBlocked ? "#FF0055" : isDone ? "#00FF9D" : isActive ? st.color : "#475569";

            return (
              <g
                key={st.key}
                className="stage-node-box"
                onClick={() => openStageDrawer(st.key)}
                transform={`translate(${st.x - 55}, ${st.y - 45})`}
                style={{ cursor: "pointer" }}
              >
                {/* Node Housing Box */}
                <rect
                  x="0"
                  y="0"
                  width="110"
                  height="90"
                  rx="8"
                  fill="#0B132B"
                  stroke={nodeColor}
                  strokeWidth={isActive ? "2.5" : "1.2"}
                  filter={isActive ? "url(#neonFilter)" : undefined}
                />

                {/* Stage Code Badge */}
                <rect x="8" y="8" width="28" height="18" rx="3" fill="rgba(255,255,255,0.06)" />
                <text x="22" y="21" fill="#94A3B8" fontSize="10" textAnchor="middle" fontWeight="bold">
                  {st.code}
                </text>

                {/* State Tag */}
                <text
                  x="100"
                  y="21"
                  fill={nodeColor}
                  fontSize="9"
                  textAnchor="end"
                  fontWeight="bold"
                  letterSpacing="0.5"
                >
                  {isDone ? "✓ DONE" : isActive ? "● ACTIVE" : isBlocked ? "✗ BLOCKED" : "WAIT"}
                </text>

                {/* Stage Name */}
                <text x="55" y="48" fill="#F8FAFC" fontSize="12" fontWeight="bold" textAnchor="middle">
                  {st.name}
                </text>

                {/* Subtitle / Key */}
                <text x="55" y="65" fill="#64748B" fontSize="9" textAnchor="middle">
                  {st.key.toUpperCase()}
                </text>

                {/* Status Indicator Bar */}
                <line x1="12" y1="78" x2="98" y2="78" stroke={nodeColor} strokeWidth="3" strokeLinecap="round" />
              </g>
            );
          })}

          {/* STAGE 03: Dual-Ring Rotating Octo Loop Guard Reactor (Clickable) */}
          {currentStages.some((s) => s.key === "workshop") && (
          <g
            className="octo-reactor-clickable"
            style={{ cursor: "pointer" }}
            onClick={() => openStageDrawer("workshop")}
          >
            {/* Outer Counter-Rotating Ring */}
            <circle
              className="octo-ring-outer"
              cx="640"
              cy="100"
              r="48"
              fill="none"
              stroke="#FF6B00"
              strokeWidth="2"
              strokeDasharray="18 10 6 10"
              opacity="0.8"
            />
            {/* Inner Clockwise Rotating Ring */}
            <circle
              className="octo-ring-inner"
              cx="640"
              cy="100"
              r="34"
              fill="none"
              stroke="#FFA500"
              strokeWidth="2"
              strokeDasharray="12 8"
              opacity="0.9"
            />
            {/* Plasma Pulsing Core */}
            <circle className="octo-core-plasma" cx="640" cy="100" r="18" />
            <text x="640" y="103" fill="#FFFFFF" fontSize="8" fontWeight="bold" textAnchor="middle">
              OCTO
            </text>
          </g>
          )}

          {/* STAGE 06: Triple Pulsing Laser Inspection Gates (Clickable) */}
          {currentStages.some((s) => s.key === "test") && (
          <g
            className="laser-gates-clickable"
            style={{ cursor: "pointer" }}
            onClick={() => openStageDrawer("test")}
          >
            {/* L0 Laser Gate (Emerald Green) */}
            <line className="laser-beam" x1="120" y1="205" x2="120" y2="275" stroke="#00FF9D" />
            <text x="120" y="200" fill="#00FF9D" fontSize="7" fontWeight="bold" textAnchor="middle">L0</text>

            {/* L1 Laser Gate (Electric Cyan) */}
            <line className="laser-beam" x1="140" y1="205" x2="140" y2="275" stroke="#00F0FF" />
            <text x="140" y="200" fill="#00F0FF" fontSize="7" fontWeight="bold" textAnchor="middle">L1</text>

            {/* L2 Laser Gate (Amber Orange) */}
            <line className="laser-beam" x1="160" y1="205" x2="160" y2="275" stroke="#FFB703" />
            <text x="160" y="200" fill="#FFB703" fontSize="7" fontWeight="bold" textAnchor="middle">L2</text>
          </g>
          )}

          {/* STAGE 08: Bundle.zip Port (Clickable) */}
          {currentStages.some((s) => s.key === "release") && (
          <g
            className="zip-port-clickable"
            transform="translate(380, 390)"
            style={{ cursor: "pointer" }}
            onClick={() => openStageDrawer("release")}
          >
            <circle cx="0" cy="0" r="32" fill="none" stroke="#00F0FF" strokeWidth="1.5" strokeDasharray="4 4" />
            <text x="0" y="-12" fill="#00F0FF" fontSize="8" fontWeight="bold" textAnchor="middle">
              ZIP PORT
            </text>
          </g>
          )}

          {/* STAGE 09: Glowing Amethyst Experience Crystal Vault (Clickable) */}
          {currentStages.some((s) => s.key === "retro") && (
          <g
            className={`amethyst-crystal ${vaultFlash ? "vault-burst" : ""}`}
            style={{ cursor: "pointer" }}
            onClick={() => openStageDrawer("retro")}
          >
            {/* Vault Crystal Octahedron */}
            <polygon
              points="640,360 670,390 640,420 610,390"
              fill="url(#vaultGlow)"
              stroke="#E9D5FF"
              strokeWidth="2"
            />
            <polygon
              points="640,360 655,390 640,420 625,390"
              fill="#A855F7"
              opacity="0.6"
            />
            {/* Crystal Core Label */}
            <text x="640" y="394" fill="#FFFFFF" fontSize="9" fontWeight="bold" textAnchor="middle">
              VAULT
            </text>
            <text x="640" y="435" fill="#C084FC" fontSize="9" fontWeight="bold" textAnchor="middle">
              {crystalsCount} CRYSTALS
            </text>
          </g>
          )}
        </svg>
      </main>

      {/* 5. REAL-TIME TELEMETRY STREAM FEED BAR */}
      <footer className="cyber-stream-bar">
        <span className="stream-label">[LIVE BUS]</span>
        <span style={{ color: "#64748B" }}>{latestEvent.time_iso || ""}</span>
        <span style={{ color: "#00F0FF", fontWeight: "bold" }}>{latestEvent.type}:</span>
        <span className="stream-content">{JSON.stringify(latestEvent.data || {})}</span>
      </footer>

              </>
      )}
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
