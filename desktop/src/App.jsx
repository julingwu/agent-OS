import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, clearConn, getConn, setConn, wsUrl } from "./api.js";

const TABS = [
  { id: "computer", label: "电脑" },
  { id: "details", label: "详情" },
  { id: "library", label: "资料库" },
];

export default function App() {
  const [conn, setConnState] = useState(getConn());
  const [phase, setPhase] = useState(() => (getConn() ? "checking" : "login"));
  const [bootError, setBootError] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!conn) {
      setPhase("login");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        await api("/v1/bots");
        if (!cancelled) {
          setBootError("");
          setPhase("app");
        }
      } catch (e) {
        if (!cancelled) {
          setBootError(String(e.message || e));
          setPhase("login");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conn, tick]);

  if (phase === "app" && conn) {
    return (
      <MainApp
        key={conn.baseUrl + conn.token}
        onLogout={() => {
          clearConn();
          setConnState(null);
          setPhase("login");
        }}
      />
    );
  }
  return (
    <LoginScreen
      initial={conn}
      checking={phase === "checking"}
      notice={bootError}
      onRetry={() => {
        setPhase("checking");
        setTick((t) => t + 1);
      }}
      onConnected={(next) => {
        setConn(next);
        setConnState(next);
        setPhase("app");
      }}
    />
  );
}

/* ============================== 赛博风登录页 ============================== */
const DEFAULT_HOSTS = [
  { name: "官方体验节点", url: "https://193.112.172.223" },
  { name: "本地开发实例", url: "http://127.0.0.1:8077" },
];

function loadSavedHosts() {
  try {
    const raw = localStorage.getItem("agentos_saved_hosts");
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function persistSavedHosts(list) {
  try {
    localStorage.setItem("agentos_saved_hosts", JSON.stringify(list));
  } catch {}
}

/* ============================== 赛博风登录页 (支持多主机自备节点 BYOH) ============================== */
function LoginScreen({ initial, checking, notice, onRetry, onConnected }) {
  const [mode, setMode] = useState("token"); // token | code
  const [customHosts, setCustomHosts] = useState(loadSavedHosts);
  const [url, setUrl] = useState(initial?.baseUrl || "https://193.112.172.223");
  const [tok, setTok] = useState(initial?.token || "");
  const [code, setCode] = useState("");
  const [devName, setDevName] = useState(guessDeviceName());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ping, setPing] = useState({ state: "idle", text: "" });

  const [showAddModal, setShowAddModal] = useState(false);
  const [newHostName, setNewHostName] = useState("");
  const [newHostUrl, setNewHostUrl] = useState("");
  const [showDeployGuide, setShowDeployGuide] = useState(false);
  const [copyFeedback, setCopyFeedback] = useState(false);

  const allHosts = [...DEFAULT_HOSTS, ...customHosts];

  const pingHost = async (target) => {
    const base = (target || url).trim().replace(/\/+$/, "");
    if (!base) return;
    setPing({ state: "wait", text: "探活中…" });
    const start = performance.now();
    try {
      const res = await fetch(`${base}/v1/health`);
      const cost = Math.round(performance.now() - start);
      if (res.ok) {
        setPing({ state: "ok", text: `在线 ${cost}ms` });
      } else {
        setPing({ state: "err", text: `响应异常 (${res.status})` });
      }
    } catch {
      setPing({ state: "err", text: "未连通 / 证书未信任" });
    }
  };

  const saveNewHost = () => {
    const name = newHostName.trim();
    let u = newHostUrl.trim().replace(/\/+$/, "");
    if (!name || !u) return;
    if (!u.startsWith("http://") && !u.startsWith("https://")) {
      u = `https://${u}`;
    }
    const next = [...customHosts.filter((h) => h.url !== u), { name, url: u }];
    setCustomHosts(next);
    persistSavedHosts(next);
    setUrl(u);
    setShowAddModal(false);
    setNewHostName("");
    setNewHostUrl("");
    pingHost(u);
  };

  const removeHost = (targetUrl) => {
    const next = customHosts.filter((h) => h.url !== targetUrl);
    setCustomHosts(next);
    persistSavedHosts(next);
  };

  const copyDeployCmd = () => {
    const cmd = "curl -sSL https://raw.githubusercontent.com/julingwu/agent-OS/mainline/scripts/setup_server.sh | bash";
    navigator.clipboard?.writeText(cmd).catch(() => {});
    setCopyFeedback(true);
    setTimeout(() => setCopyFeedback(false), 2000);
  };

  const submit = async () => {
    if (busy) return;
    setErr("");
    const base = url.trim().replace(/\/+$/, "");
    if (!base) {
      setErr("先填服务器地址");
      return;
    }
    setBusy(true);
    try {
      await fetch(`${base}/v1/health`).then((r) => {
        if (!r.ok) throw new Error(`服务不可达（${r.status}）`);
        return r.json();
      });
      if (mode === "token") {
        const token = tok.trim();
        if (!token) throw new Error("设备令牌不能为空");
        const res = await fetch(`${base}/v1/bots`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.status === 401) throw new Error("设备令牌无效或已吊销");
        if (!res.ok) throw new Error(`鉴权失败（${res.status}）`);
        onConnected({ baseUrl: base, token });
      } else {
        const clean = code.trim();
        if (!/^\d{6}$/.test(clean)) throw new Error("配对码是 6 位数字");
        const res = await fetch(`${base}/v1/pairing/claim`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: clean, name: devName || "我的电脑", kind: "exe" }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `配对失败（${res.status}）`);
        onConnected({ baseUrl: base, token: data.token });
      }
    } catch (e) {
      const msg = String(e.message || e);
      setErr(
        msg.includes("Failed to fetch")
          ? "连不上服务器：若是自备云主机，请先在浏览器打开一次该地址接受证书"
          : msg
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-root">
      <div className="cyber-bg" aria-hidden="true">
        <span className="orb orb-a" />
        <span className="orb orb-b" />
        <span className="orb orb-c" />
        <span className="grid-plane" />
        <span className="scanlines" />
      </div>

      <div className="login-card">
        <div className="login-brand">
          <span className="brand-mark">◢◤</span>
          <h1>AGENT&nbsp;OS</h1>
          <p className="brand-sub">自托管 AI 团队 · 连接你的专属主机</p>
        </div>

        <div className="login-status">
          <span className={`pulse ${checking ? "on" : ""}`} />
          {checking ? "正在验证已有会话…" : "自备算力主机 · 数据完全私有"}
        </div>

        {notice && (
          <div className="login-notice">
            <div>连不上服务器：{notice}</div>
            <button type="button" onClick={onRetry}>重试连接</button>
          </div>
        )}

        {/* 主机管理卡片 (BYOH) */}
        <div className="host-selector-card">
          <div className="host-selector-head">
            <span>🌐 接入主机实例</span>
            {ping.state !== "idle" && (
              <span className={`host-ping-badge ${ping.state}`}>
                ● {ping.text}
              </span>
            )}
          </div>

          <div style={{ display: "flex", gap: "6px" }}>
            <select
              className="host-select-input"
              value={allHosts.some((h) => h.url === url) ? url : "custom"}
              onChange={(e) => {
                if (e.target.value !== "custom") {
                  setUrl(e.target.value);
                  pingHost(e.target.value);
                }
              }}
            >
              {allHosts.map((h) => (
                <option key={h.url} value={h.url}>
                  {h.name} ({h.url})
                </option>
              ))}
              <option value="custom">-- 自定义自备主机 --</option>
            </select>
          </div>

          <div className="host-quick-actions">
            <button type="button" className="host-mini-btn" onClick={() => pingHost(url)}>
              ⚡ 测速探活
            </button>
            <button type="button" className="host-mini-btn" onClick={() => setShowAddModal(true)}>
              ＋ 存为自备主机
            </button>
            <button type="button" className="host-mini-btn" onClick={() => setShowDeployGuide(true)}>
              📖 部署我的VPS
            </button>
          </div>
        </div>

        <div className="login-modes">
          <button className={mode === "token" ? "login-mode on" : "login-mode"}
                  onClick={() => { setMode("token"); setErr(""); }}>已有令牌</button>
          <button className={mode === "code" ? "login-mode on" : "login-mode"}
                  onClick={() => { setMode("code"); setErr(""); }}>配对码</button>
        </div>

        <label className="field">
          <span>服务器地址</span>
          <input value={url} spellCheck={false} placeholder="https://IP 或 https://你的域名"
            onChange={(e) => {
              setUrl(e.target.value);
              setPing({ state: "idle", text: "" });
            }}
            onKeyDown={(e) => e.key === "Enter" && submit()} />
        </label>

        {mode === "token" ? (
          <label className="field">
            <span>设备令牌</span>
            <input value={tok} spellCheck={false} type="password" placeholder="配对或安装输出的 token"
              onChange={(e) => setTok(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()} />
          </label>
        ) : (
          <>
            <label className="field">
              <span>6 位配对码</span>
              <input value={code} spellCheck={false} inputMode="numeric" maxLength={6}
                placeholder="在已登录的设备上生成"
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                onKeyDown={(e) => e.key === "Enter" && submit()} />
            </label>
            <label className="field">
              <span>这台设备的名字</span>
              <input value={devName} spellCheck={false} placeholder="比如：我的手机"
                onChange={(e) => setDevName(e.target.value)} />
            </label>
          </>
        )}

        {err && <div className="login-error">{err}</div>}
        <button className="enter-btn" onClick={submit} disabled={busy}>
          {busy ? "连接中…" : mode === "code" ? "配 对 并 进 入" : "进 入"}
        </button>
        <div className="login-foot">
          <span>v1.0 (Mobile Ready)</span>
          <span>数据只存你的专属服务器</span>
        </div>
      </div>

      {/* 弹窗：添加自定义自备主机 */}
      {showAddModal && (
        <div className="modal-overlay" onClick={() => setShowAddModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>＋ 登记我的自备云主机</h3>
            </div>
            <p className="hint">
              保存后可直接在登录页的主机下拉框中快速切换，无需重复输入地址。
            </p>
            <label className="field">
              <span>主机名称 / 备注</span>
              <input
                value={newHostName}
                placeholder="例如：我的腾讯云 VPS / 办公室工作站"
                onChange={(e) => setNewHostName(e.target.value)}
              />
            </label>
            <label className="field">
              <span>主机访问地址</span>
              <input
                value={newHostUrl}
                placeholder="https://123.45.67.89:8077 或 https://my-agent.com"
                onChange={(e) => setNewHostUrl(e.target.value)}
              />
            </label>
            <div className="modal-actions">
              <button onClick={() => setShowAddModal(false)}>取消</button>
              <button className="primary" onClick={saveNewHost}>保存实例</button>
            </div>
          </div>
        </div>
      )}

      {/* 弹窗：一键部署我的自备云主机指南 */}
      {showDeployGuide && (
        <div className="modal-overlay" onClick={() => setShowDeployGuide(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>🚀 一键部署你的专属服务器 (BYOH)</h3>
            </div>
            <p className="hint">
              在任意干净的 Ubuntu / Debian / CentOS 云服务器终端中粘贴并运行以下单行命令：
            </p>
            <pre className="deploy-modal-pre">
              curl -sSL https://raw.githubusercontent.com/julingwu/agent-OS/mainline/scripts/setup_server.sh | bash
            </pre>
            <div style={{ textAlign: "center", marginBottom: "12px" }}>
              <button
                type="button"
                className="tool-btn"
                style={{ padding: "6px 14px", display: "inline-block", color: copyFeedback ? "var(--ok)" : "var(--cyan)" }}
                onClick={copyDeployCmd}
              >
                {copyFeedback ? "✅ 已复制到剪贴板！" : "📋 复制一键部署命令"}
              </button>
            </div>
            <p className="hint" style={{ fontSize: "11px", lineHeight: "1.5" }}>
              * 脚本将自动准备环境、配置守护进程与安全秘钥，并在执行完毕后直接打印出该服务器的<b>连接地址</b>与<b>管理令牌</b>，填入本客户端即可立即连接！
            </p>
            <div className="modal-actions">
              <button className="primary" onClick={() => setShowDeployGuide(false)}>知道了</button>
            </div>
          </div>
        </div>
      )}

      <div className="login-hint">
        配对码在“已登录的设备 → 账户 → 添加设备”里生成，5 分钟内有效、用一次即废。
      </div>
    </div>
  );
}

function guessDeviceName() {
  try {
    const ua = navigator.userAgent || "";
    if (ua.includes("Windows")) return "Windows 电脑";
    if (ua.includes("Mac")) return "Mac";
    if (ua.includes("Android")) return "Android";
    if (uiaIncludesIOS(ua)) return "iPhone";
    return "我的设备";
  } catch {
    return "我的设备";
  }
}
function uiaIncludesIOS(ua) {
  return /iPhone|iPad|iPod/.test(ua);
}

function getProfileFromLevels(levels) {
  if (!Array.isArray(levels) || levels.length === 0) return "deepseek";
  if (levels.includes("low") && levels.includes("medium") && !levels.includes("standard") && !levels.includes("none")) {
    return "openai";
  }
  if (levels.includes("budget") || (levels.includes("low") && levels.includes("none") && levels.includes("max"))) {
    return "claude";
  }
  return "deepseek";
}

const REASONING_PROFILES = {
  deepseek: ["none", "standard", "max"],
  openai: ["none", "low", "medium", "high"],
  claude: ["none", "low", "medium", "high", "max"],
};

function inferModelMetadata(modelInput) {
  const rawId = typeof modelInput === "string" ? modelInput : (modelInput?.id || modelInput?.name || "");
  const trimmedId = (rawId || "").trim();
  const name = trimmedId.toLowerCase();

  // 上下文大小推断
  let context_window = "128K";
  if (["1m", "1000k", "1024k", "deepseek-flash", "deepseek-v4-pro", "glm-4-long", "long"].some((k) => name.includes(k))) {
    context_window = "1M";
  } else if (["claude-3", "o1", "o3", "o4"].some((k) => name.includes(k))) {
    context_window = "200K";
  } else if (name.includes("256k")) {
    context_window = "256K";
  } else if (["128k", "gpt-4o", "qwen"].some((k) => name.includes(k))) {
    context_window = "128K";
  } else if (["deepseek-chat", "deepseek-reasoner", "64k"].some((k) => name.includes(k))) {
    context_window = "64K";
  } else if (name.includes("32k")) {
    context_window = "32K";
  } else if (name.includes("16k")) {
    context_window = "16K";
  }

  // 最大输出推断
  let max_tokens = "8K";
  if (["100k", "o1", "o3", "o4"].some((k) => name.includes(k))) {
    max_tokens = "100K";
  } else if (["64k", "claude-3-7", "claude-3.7"].some((k) => name.includes(k))) {
    max_tokens = "64K";
  } else if (["32k", "deepseek-flash", "deepseek-v4-pro", "scnet"].some((k) => name.includes(k))) {
    max_tokens = "32K";
  } else if (["16k", "gpt-4o"].some((k) => name.includes(k))) {
    max_tokens = "16K";
  } else if (["4k", "glm-4-long"].some((k) => name.includes(k))) {
    max_tokens = "4K";
  }

  // 推理思考能力
  const is_o_series = ["o1", "o3", "o4"].some((k) => name.includes(k));
  const is_claude_thinking = name.includes("claude-3-7") || name.includes("claude-3.7") || (name.includes("claude") && name.includes("thinking"));
  const is_scnet = name.includes("scnet");
  const is_reasoner = ["reasoner", "r1", "thinking", "qwq"].some((k) => name.includes(k));

  let supports_reasoning = false;
  let reasoning_profile = "deepseek";
  let reasoning_levels = [];

  if (is_o_series) {
    supports_reasoning = true;
    reasoning_profile = "openai";
    reasoning_levels = ["low", "medium", "high"];
  } else if (is_claude_thinking) {
    supports_reasoning = true;
    reasoning_profile = "claude";
    reasoning_levels = ["none", "low", "medium", "high", "max"];
  } else if (is_scnet) {
    supports_reasoning = true;
    reasoning_profile = "openai";
    reasoning_levels = ["none", "low", "medium", "high"];
  } else if (is_reasoner) {
    supports_reasoning = true;
    reasoning_profile = "deepseek";
    reasoning_levels = ["none", "standard", "max"];
  } else {
    supports_reasoning = false;
    reasoning_profile = "deepseek";
    reasoning_levels = [];
  }

  // 若传入的是已有富对象，继承并规整自定义字段
  if (modelInput && typeof modelInput === "object") {
    const sr = modelInput.supports_reasoning !== undefined ? !!modelInput.supports_reasoning : supports_reasoning;
    const rLevels = Array.isArray(modelInput.reasoning_levels) && modelInput.reasoning_levels.length > 0
      ? modelInput.reasoning_levels
      : (sr ? (reasoning_levels.length > 0 ? reasoning_levels : ["none", "standard", "max"]) : []);
    const rProfile = modelInput.reasoning_profile || (sr ? (reasoning_profile || getProfileFromLevels(rLevels)) : "deepseek");
    return {
      id: trimmedId,
      name: (modelInput.name || modelInput.label || trimmedId).trim() || trimmedId,
      context_window: modelInput.context_window ? String(modelInput.context_window).trim() : context_window,
      max_tokens: modelInput.max_tokens ? String(modelInput.max_tokens).trim() : max_tokens,
      supports_reasoning: sr,
      reasoning_profile: rProfile,
      reasoning_levels: sr ? rLevels : [],
    };
  }

  return {
    id: trimmedId,
    name: trimmedId,
    context_window,
    max_tokens,
    supports_reasoning,
    reasoning_profile,
    reasoning_levels: supports_reasoning ? reasoning_levels : [],
  };
}

function normalizeModelList(list) {
  if (!Array.isArray(list)) return [];
  return list.map(inferModelMetadata).filter((m) => m && m.id);
}

function getModelReasoningLevels(modelName, allModels) {
  const targetId = (modelName || "").trim();
  if (!targetId) return [];

  // 1. 优先在已加载的全局模型列表（从 /v1/models 获取）中查找该模型富元数据
  let items = [];
  if (Array.isArray(allModels)) {
    items = allModels;
  } else if (allModels && Array.isArray(allModels.items)) {
    items = allModels.items;
  }

  const found = items.find((m) => {
    if (!m) return false;
    if (typeof m === "string") return m.toLowerCase() === targetId.toLowerCase();
    const id = String(m.id || "").trim().toLowerCase();
    const name = String(m.name || "").trim().toLowerCase();
    const t = targetId.toLowerCase();
    return id === t || name === t;
  });

  if (found && typeof found === "object") {
    if (found.supports_reasoning === true) {
      if (Array.isArray(found.reasoning_levels) && found.reasoning_levels.length > 0) {
        return found.reasoning_levels;
      }
      return ["none", "standard", "max"];
    }
    if (found.supports_reasoning === false) {
      return [];
    }
  }

  // 2. 若未查到元数据，通过模型名称关键词匹配（包含 scnet, deepseek, reasoner, r1, o1, o3, thinking, qwq 等）
  const name = targetId.toLowerCase();
  if (
    /\b(o1|o3|o4)\b/.test(name) ||
    name.startsWith("o1-") ||
    name.startsWith("o3-") ||
    name.startsWith("o4-") ||
    name === "o1" ||
    name === "o3" ||
    name === "o4"
  ) {
    return ["low", "medium", "high"];
  }
  if (
    name.includes("claude-3-7") ||
    name.includes("claude-3.7") ||
    (name.includes("claude") && name.includes("thinking"))
  ) {
    return ["none", "low", "medium", "high", "max"];
  }
  if (name.includes("scnet")) {
    return ["none", "low", "medium", "high"];
  }
  if (
    name.includes("deepseek") ||
    name.includes("reasoner") ||
    name.includes("r1") ||
    name.includes("thinking") ||
    name.includes("qwq") ||
    name.includes("qwen-max")
  ) {
    return ["none", "standard", "max"];
  }

  return [];
}

const REASONING_LABELS = {
  none: "关闭深度思考 (none)",
  low: "轻度思考 (low)",
  medium: "标准深度思考 (medium)",
  high: "极限深度思考 (high)",
  standard: "标准深度思考 (medium)",
  max: "极限深度思考 (high)",
};

const REASONING_SHORT_LABELS = {
  none: "思考: 关",
  low: "思考: 轻度",
  medium: "思考: 标准",
  high: "思考: 极限",
  standard: "思考: 标准",
  max: "思考: 极限",
};

const DEFAULT_FALLBACK_MODELS = [
  "deepseek-flash",
  "deepseek-chat",
  "deepseek-reasoner",
];

/* ============================== 主界面 ============================== */
function MainApp({ onLogout }) {
  const [bots, setBots] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [activity, setActivity] = useState([]);
  // 流式中的半截回复：{ turnId, text }。message.bot 到了就清掉（以持久化为准）
  const [streamText, setStreamText] = useState(null);
  const [status, setStatus] = useState("idle");
  const [usage, setUsage] = useState(0);
  const [tab, setTab] = useState("computer");
  const [details, setDetails] = useState(null);
  const [library, setLibrary] = useState(null);
  const [libPath, setLibPath] = useState("");
  const [preview, setPreview] = useState(null);
  const [frame, setFrame] = useState(null);
  const [screenMeta, setScreenMeta] = useState(null);
  const [computerState, setComputerState] = useState("unknown");
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [connected, setConnected] = useState(false);
  // 截图放大查看：{url, name}，点缩略图时设置，Esc/点遮罩关闭
  const [lightbox, setLightbox] = useState(null);

  const [showMenu, setShowMenu] = useState(false);
  const [modal, setModal] = useState(null);           // search | newbot | group | apps | settings
  const [settingsTab, setSettingsTab] = useState("general");
  const [showMobileSidebar, setShowMobileSidebar] = useState(false);
  const [showMobilePanel, setShowMobilePanel] = useState(false);
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === "undefined") return false;
    return (
      window.innerWidth <= 1024 ||
      /Android|iPhone|iPad|iPod|HarmonyOS|Mobile/i.test(navigator.userAgent)
    );
  });

  useEffect(() => {
    const onResize = () => {
      setIsMobile(
        window.innerWidth <= 1024 ||
        /Android|iPhone|iPad|iPod|HarmonyOS|Mobile/i.test(navigator.userAgent)
      );
    };
    window.addEventListener("resize", onResize);
    const onMsg = (e) => {
      if (e.data?.type === "SWITCH_TO_CHAT") {
        setView("chat");
      }
    };
    window.addEventListener("message", onMsg);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("message", onMsg);
    };
  }, []);
  const [fullscreen, setFullscreen] = useState(false);
  const [controlMode, setControlMode] = useState(false);
  const [settings, setSettings] = useState(null);
  const [apps, setApps] = useState([]);
  const [integrations, setIntegrations] = useState([]);
  const [vault, setVault] = useState([]);
  const [models, setModels] = useState({ default: null, items: [] });
  const [localDevices, setLocalDevices] = useState([]);
  const [approvals, setApprovals] = useState([]);   // 本地操作待批准
  const [sessionApproved, setSessionApproved] = useState({}); // session_id -> bool 全会话免审状态
  const [memories, setMemories] = useState({ items: [], total: 0 });  const [routines, setRoutines] = useState({ items: [], count: 0 });
  const [rtnTitle, setRtnTitle] = useState("");
  const [rtnPrompt, setRtnPrompt] = useState("");
  const [rtnSchedule, setRtnSchedule] = useState("interval:30m");
  const [skillsList, setSkillsList] = useState({ items: [], count: 0 });
  const [rolePresets, setRolePresets] = useState([]);
  const [selectedPresetRoleId, setSelectedPresetRoleId] = useState("");
  const [skName, setSkName] = useState("");
  const [skDesc, setSkDesc] = useState("");
  const [skBody, setSkBody] = useState("");
  const [skGlobal, setSkGlobal] = useState(true);
  const [autoTick, setAutoTick] = useState(0);

  const [memInput, setMemInput] = useState("");
  const [memoriesTick, setMemoriesTick] = useState(0);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [view, setView] = useState("chat");       // chat | workbench（整窗切换）
  const [workbenchSrc, setWorkbenchSrc] = useState("");
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const [ctx, setCtx] = useState(null);
  const [toast, setToast] = useState("");
  const [pairCode, setPairCode] = useState(null);

  const [reasoningEffort, setReasoningEffort] = useState("medium");
  const [editingCustomModel, setEditingCustomModel] = useState(false);
  const [customModelInput, setCustomModelInput] = useState("");
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [showReasoningPicker, setShowReasoningPicker] = useState(false);
  const modelPickerRef = useRef(null);
  const reasoningPickerRef = useRef(null);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (modelPickerRef.current && !modelPickerRef.current.contains(e.target)) {
        setShowModelPicker(false);
        setEditingCustomModel(false);
      }
      if (reasoningPickerRef.current && !reasoningPickerRef.current.contains(e.target)) {
        setShowReasoningPicker(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const activeSession = sessions.find((s) => s.id === activeId);
  const activeBot =
    bots.find(
      (b) =>
        b.id === activeSession?.admin_bot_id ||
        (activeSession?.members && activeSession.members.includes(b.id))
    ) || bots[0];
  const activeBotId = activeBot?.id;

  const globalModel = settings?.model || models?.default || "deepseek-flash";
  const effectiveModel = (activeBot?.model || "").trim() || globalModel;
  const reasoningLevels = useMemo(() => getModelReasoningLevels(effectiveModel, models), [effectiveModel, models]);
  const isRunning = status === "thinking" || status === "running" || !!activeSession?.running;

  const modelOptions = useMemo(() => {
    const list = [];
    (models?.items || []).forEach((m) => {
      const id = typeof m === "string" ? m : m?.id;
      if (id && !list.includes(id)) {
        list.push(id);
      }
    });
    if (list.length === 0) {
      list.push(...DEFAULT_FALLBACK_MODELS);
    }
    if (effectiveModel && !list.includes(effectiveModel)) {
      list.unshift(effectiveModel);
    }
    if (models?.default && !list.includes(models.default)) {
      list.push(models.default);
    }
    return list;
  }, [models, effectiveModel]);

  useEffect(() => {
    if (reasoningLevels.length > 0) {
      if (!reasoningLevels.includes(reasoningEffort)) {
        if (reasoningLevels.includes("medium")) {
          setReasoningEffort("medium");
        } else if (reasoningLevels.includes("standard")) {
          setReasoningEffort("standard");
        } else {
          setReasoningEffort(reasoningLevels[0]);
        }
      }
    }
  }, [reasoningLevels, reasoningEffort]);

  const streamRef = useRef(null);
  const fileInputRef = useRef(null);
  const [attachments, setAttachments] = useState([]);
  const [isDragging, setIsDragging] = useState(false);

  // 切换会话时，释放并清空当前未发送的附件
  useEffect(() => {
    setAttachments((prev) => {
      prev.forEach((a) => {
        if (a.previewUrl && a.previewUrl.startsWith("blob:")) {
          try {
            URL.revokeObjectURL(a.previewUrl);
          } catch {}
        }
      });
      return [];
    });
  }, [activeId]);

  const removeAttachment = useCallback((id) => {
    setAttachments((prev) => {
      const target = prev.find((a) => a.id === id);
      if (target?.previewUrl && target.previewUrl.startsWith("blob:")) {
        try {
          URL.revokeObjectURL(target.previewUrl);
        } catch {}
      }
      return prev.filter((a) => a.id !== id);
    });
  }, []);

  const enqueueFiles = useCallback((files) => {
    if (!files) return;
    const list = Array.from(files);
    const imageFiles = list.filter((f) => {
      if (!f) return false;
      if (f.type && f.type.startsWith("image/")) return true;
      return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(f.name || "");
    });

    if (imageFiles.length === 0) {
      if (list.length > 0) {
        setToast("仅支持上传图片文件");
      }
      return;
    }

    setAttachments((prev) => {
      const remainingSlots = 5 - prev.length;
      if (remainingSlots <= 0) {
        setToast("最多支持 5 张图片");
        return prev;
      }

      const filesToAdd = imageFiles.slice(0, remainingSlots);
      if (imageFiles.length > remainingSlots) {
        setToast(`最多支持 5 张图片，已选取前 ${remainingSlots} 张`);
      }

      const newItems = filesToAdd.map((file, idx) => {
        const id = `att_${Date.now()}_${idx}_${Math.random().toString(36).slice(2, 7)}`;
        const previewUrl = URL.createObjectURL(file);
        const name = file.name || `image_${Date.now()}_${idx + 1}.png`;
        return {
          id,
          file,
          name,
          size: file.size || 0,
          previewUrl,
          status: "uploading",
          path: "",
          artifact_id: "",
          url: "",
        };
      });

      // 异步上传至工作区 uploads/ 产物目录
      newItems.forEach((item) => {
        const reader = new FileReader();
        reader.onload = async () => {
          const b64 = reader.result;
          try {
            const res = await api("/v1/workspace/upload", {
              method: "POST",
              body: {
                filename: item.name,
                data_b64: b64,
                session_id: activeId,
                bot_id: activeBot?.id,
                publish_event: false,
              },
            });
            if (res && res.ok) {
              setAttachments((curr) =>
                curr.map((a) =>
                  a.id === item.id
                    ? {
                        ...a,
                        status: "done",
                        path: res.path,
                        artifact_id: res.artifact_id,
                        url: res.url,
                        name: res.filename || a.name,
                      }
                    : a
                )
              );
            } else {
              setAttachments((curr) =>
                curr.map((a) => (a.id === item.id ? { ...a, status: "error" } : a))
              );
              setToast(`“${item.name}” 上传失败: ${res?.error || "未知错误"}`);
            }
          } catch (err) {
            setAttachments((curr) =>
              curr.map((a) => (a.id === item.id ? { ...a, status: "error" } : a))
            );
            setToast(`“${item.name}” 上传失败: ${err.message || String(err)}`);
          }
        };
        reader.onerror = () => {
          setAttachments((curr) =>
            curr.map((a) => (a.id === item.id ? { ...a, status: "error" } : a))
          );
          setToast(`“${item.name}” 读取失败`);
        };
        reader.readAsDataURL(item.file);
      });

      return [...prev, ...newItems];
    });
  }, [activeId, activeBot?.id]);

  const handleFileUpload = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      enqueueFiles(e.target.files);
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handlePaste = (e) => {
    const items = e.clipboardData?.items;
    if (!items || items.length === 0) return;
    const files = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type && item.type.startsWith("image/")) {
        const file = item.getAsFile();
        if (file) {
          const ext = (item.type.split("/")[1] || "png").replace("jpeg", "jpg");
          const customName =
            file.name && file.name !== "image.png"
              ? file.name
              : `screenshot_${Date.now()}_${i + 1}.${ext}`;
          files.push(new File([file], customName, { type: file.type }));
        }
      }
    }
    if (files.length > 0) {
      e.preventDefault();
      enqueueFiles(files);
    }
  };

  const listRef = useRef(null);
  const imgRef = useRef(null);

  const scrollToBottom = useCallback((smooth = false) => {
    setShowScrollBottom(false);
    const el = listRef.current;
    if (!el) return;
    if (smooth && typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    } else {
      el.scrollTop = el.scrollHeight;
    }
    requestAnimationFrame(() => {
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
    });
    setTimeout(() => {
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
    }, 60);
  }, []);

  const handleMessagesScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    setShowScrollBottom(dist > 100);
  }, []);

  useEffect(() => {
    if (view === "chat") {
      scrollToBottom(false);
    }
  }, [view, scrollToBottom]);
  const activeRef = useRef(null);
  activeRef.current = activeId;
  const bootCursorRef = useRef(0);   // 打开界面时的事件游标：早于它的是历史，不回放审批卡

  /* ---------------- 数据加载 ---------------- */
  const loadBasics = useCallback(async () => {
    try {
      const [b, s, ro] = await Promise.all([
        api("/v1/bots"),
        api("/v1/sessions"),
        api("/v1/roles").catch(() => ({ items: [] })),
      ]);
      setBots(b.items);
      setSessions(s.items);
      setRolePresets(ro.items || []);
      const curActive = activeRef.current || (s.items.length ? s.items[0].id : null);
      if (!activeRef.current && s.items.length) setActiveId(s.items[0].id);
      const activeSession = (s.items || []).find((x) => x.id === curActive);
      if (activeSession && activeSession.running) {
        setStatus("thinking");
      }
      try {
        const [cur, pa] = await Promise.all([
          api("/v1/events?since=999999999"),
          api("/v1/local/approvals"),
        ]);
        bootCursorRef.current = cur.cursor || 0;
        setApprovals((pa.items || []).map((a) => ({
          approvalId: a.approval_id, action: a.action,
          detail: a.detail || "", risk: a.risk || "normal", preview: a.preview || null,
        })));
      } catch {
        /* 老服务端没有这些接口时忽略 */
      }
      setError("");
    } catch (e) {
      setError(String(e.message || e));
    }
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      const [st, md, ig, ld] = await Promise.all([
        api("/v1/settings"), api("/v1/models"), api("/v1/integrations"),
        api("/v1/local/devices"),
      ]);
      setSettings(st.values);
      setModels(md);
      setIntegrations(ig.items || []);
      setLocalDevices(ld.items || []);
    } catch (e) {
      setError(String(e.message || e));
    }
  }, []);

  useEffect(() => {
    loadBasics();
    loadSettings();
  }, [loadBasics, loadSettings]);

  useEffect(() => {
    if (!activeId) return;
    let closed = false;
    const sync = async () => {
      try {
        const s = await api(`/v1/sessions/${activeId}`);
        if (closed) return;
        setStatus(prev => s.running ? 'thinking' : prev === 'thinking' ? 'idle' : prev);
      } catch { /* websocket reconnect retains the visible status */ }
    };
    const timer = setInterval(sync, 5000);
    return () => { closed = true; clearInterval(timer); };
  }, [activeId]);

  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    clearArtifactCache();     // 切会话时释放上一批截图的 blob（下载会走 WebView 的 HTTP 缓存）
    setStatus("idle");
    setActivity([]);
    setStreamText(null);
    (async () => {
      try {
        // 消息接口只返回 message.*；图片元数据在 artifact.attached 事件里，
        // 单独补拉一次再按事件 id 合并，保证首屏/切换会话也能看到历史截图。
        const [data, arts] = await Promise.all([
          api(`/v1/sessions/${activeId}/messages?limit=200`),
          api(`/v1/events?session_id=${encodeURIComponent(activeId)}&kinds=artifact.attached&limit=100`)
            .catch(() => ({ items: [] })),
        ]);
        if (cancelled) return;
        const merged = [
          ...data.items.map(toMessage),
          ...(arts.items || []).map(toArtifact),
        ].sort((a, b) => (a.id || 0) - (b.id || 0));
        setMessages(merged);
        setActivity([]);
        scrollToBottom(false);
      } catch (e) {
        if (!cancelled) setError(String(e.message || e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  /* ---------------- 事件流 ---------------- */
  const handleEvent = useCallback((e) => {
    const p = e.payload || {};
    // 审批是应用级事件：不管当前停在哪个会话都要弹出来
    if (e.kind === "approval.requested") {
      if (e.id && e.id <= bootCursorRef.current) return;   // 历史事件不回放
      setApprovals((prev) => {
        if (prev.some((a) => a.approvalId === p.approval_id)) return prev;
        return [...prev, {
          approvalId: p.approval_id, action: p.action || "本地操作",
          detail: p.detail || "", risk: p.risk || "normal",
          preview: p.preview || null,
          sessionId: p.session_id || activeRef.current,
        }];
      });
      return;
    }
    if (e.kind === "approval.decided") {
      setApprovals((prev) => prev.filter((a) => a.approvalId !== p.approval_id));
      return;
    }
    if (e.session_id && e.session_id !== activeRef.current) return;
    switch (e.kind) {
      case "message.delta":
        setStreamText((prev) => {
          const sameTurn = prev && prev.turnId === (e.turn_id || p.turn_id || "");
          const base = sameTurn ? prev.text : "";
          return { turnId: e.turn_id || p.turn_id || "", text: base + (p.text || "") };
        });
        break;
      case "message.user":
      case "message.bot":
      case "message.system":
        if (e.kind === "message.bot") setStreamText(null);
        setMessages((prev) => (prev.some((m) => m.id === e.id) ? prev : [...prev, toMessage(e)]));
        break;
      case "artifact.attached":
        // 截图/文件产物：事件里只有引用（id/url/bytes），像素由 /v1/artifacts/<id> 单独取
        setMessages((prev) => (prev.some((m) => m.id === e.id) ? prev : [...prev, toArtifact(e)]));
        break;
      case "tool.call":
        if (e.__replay) break;   // 历史工具调用不属于"本轮"
        setActivity((prev) => [...prev.filter((a) => a.id !== "model-wait"), { id: e.id, callId: p.call_id, tool: p.tool, args: p.args, state: "running" }]);
        break;
      case "tool.result":
        if (e.__replay) break;
        setActivity((prev) => prev.map((a) => (a.callId === p.call_id ? { ...a, state: p.ok ? "ok" : "fail", summary: p.summary || "" } : a)));
        break;
      case "turn.started":
        if (e.__replay) break;
        setStatus("thinking");
        setActivity([]);        // 新一轮从空白开始，别把上轮的过程堆在这轮
        break;
      case "turn.ended":
        if (e.__replay) break;
        setStatus(p.status === "done" ? "idle" : p.status || "idle");
        setStreamText(null);
        setActivity((prev) => prev.filter((a) => a.id !== "model-wait"));
        break;
      case "needs_human":
      case "thought.delta":
        if (e.kind === "thought.delta") {
          if (!e.__replay) setActivity((prev) => [...prev.filter((a) => a.id !== "model-wait"),
            { id: "model-wait", tool: "模型处理中", state: "waiting", summary: p.text || "" }]);
          break;
        }
        if (e.__replay) break;
        setActivity((prev) => [...prev, { id: e.id, tool: "needs_human", state: "waiting", summary: p.question }]);
        setStatus("needs_human");
        break;
      case "budget.updated":
        setUsage(p.used || 0);
        break;
      case "error":
        if (e.__replay) break;
        setActivity((prev) => [...prev, { id: e.id, tool: "error", state: "fail", summary: p.message || p.code }]);
        break;
      default:
        break;
    }
  }, []);

  useEffect(() => {
    if (!activeId) return;
    let closed = false;
    let retry = null;
    const connect = () => {
      const ws = new WebSocket(wsUrl("/v1/stream", { since: 0 }));
      streamRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1500);
      };
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.t === "events") {
            // replay=true 是重连后的历史补发：只用来对齐消息，不能当本轮过程
            data.items.forEach((it) => handleEvent({ ...it, __replay: !!data.replay }));
          }
          if (data.t === "resync") {
            api(`/v1/sessions/${activeRef.current}/messages?limit=200`)
              .then((d) => setMessages(d.items.map(toMessage))).catch(() => {});
          }
        } catch {
          /* ignore */
        }
      };
    };
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      if (streamRef.current) streamRef.current.close();
    };
  }, [activeId, handleEvent]);

  /* ---------------- 电脑画面 ---------------- */
  const streamOn = tab === "computer" || fullscreen;
  useEffect(() => {
    if (!streamOn) return;
    let ws = null;
    let lastFrame = null;
    const fps = Number(settings?.computer_fps || 2);
    try {
      ws = new WebSocket(wsUrl("/v1/computer/screen"));
      ws.binaryType = "arraybuffer";
      ws.onopen = () => ws.send(JSON.stringify({ t: "subscribe", fps, quality: 6, max_w: 1280 }));
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          const m = JSON.parse(ev.data);
          if (m.t === "meta") setScreenMeta(m);
          if (m.t === "capture_error") setError(m.error);
          return;
        }
        const buf = new Uint8Array(ev.data);
        const url = URL.createObjectURL(new Blob([buf.slice(4)], { type: "image/jpeg" }));
        if (lastFrame) URL.revokeObjectURL(lastFrame);
        lastFrame = url;
        setFrame(url);
      };
    } catch (e) {
      setError(String(e.message || e));
    }
    return () => {
      if (ws) ws.close();
      if (lastFrame) URL.revokeObjectURL(lastFrame);
    };
  }, [streamOn, settings?.computer_fps]);

  useEffect(() => {
    if (tab !== "computer" && !fullscreen) return;
    api("/v1/computer").then((d) => setComputerState(d.state)).catch(() => {});
  }, [tab, fullscreen]);


  /* ---------------- 快捷键 ---------------- */
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setModal((m) => (m === "search" ? null : "search"));
      }
      if (e.key === "Escape") {
        setModal(null);
        setFullscreen(false);
        setControlMode(false);
        setShowMenu(false);
        setCtx(null);
        setLightbox(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const clear = () => setCtx(null);
    window.addEventListener("click", clear);
    return () => window.removeEventListener("click", clear);
  }, []);

  // 点击外部自动收起底部账户菜单
  useEffect(() => {
    if (!showMenu) return;
    const onOutside = (e) => {
      if (!e.target.closest(".sidebar-foot")) {
        setShowMenu(false);
      }
    };
    window.addEventListener("pointerdown", onOutside);
    return () => window.removeEventListener("pointerdown", onOutside);
  }, [showMenu]);

  /* ---------------- 动作 ---------------- */
  const isUploading = attachments.some((a) => a.status === "uploading");
  const hasAttachments = attachments.length > 0 && attachments.every((a) => a.status === "done");
  const canSend = Boolean(draft.trim() || hasAttachments) && !isUploading;

  const send = async () => {
    if (!canSend || !activeId) return;
    const text = draft.trim();
    const validAttachments = attachments
      .filter((a) => a.status === "done")
      .map((a) => ({
        name: a.name,
        path: a.path,
        artifact_id: a.artifact_id || undefined,
        url: a.url || undefined,
        bytes: a.size || undefined,
        kind: "image",
      }));

    // 清理前端预览创建的 blob URL
    attachments.forEach((att) => {
      if (att.previewUrl && att.previewUrl.startsWith("blob:")) {
        try {
          URL.revokeObjectURL(att.previewUrl);
        } catch {}
      }
    });

    setDraft("");
    setAttachments([]);
    scrollToBottom(false);

    try {
      const payload = {
        text,
        client_msg_id: crypto.randomUUID(),
        attachments: validAttachments,
      };
      const effModel = (activeBot?.model || "").trim() || settings?.model || models?.default || "deepseek-chat";
      const effLevels = getModelReasoningLevels(effModel, models);
      if (effLevels.length > 0 && reasoningEffort) {
        const effortMap = { standard: "medium", max: "high" };
        payload.reasoning_effort = effortMap[reasoningEffort] || reasoningEffort;
      }
      await api(`/v1/sessions/${activeId}/messages`, {
        method: "POST",
        body: payload,
      });
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const abortCurrentSession = async () => {
    if (!activeId) return;
    try {
      const res = await api(`/v1/sessions/${activeId}/abort`, { method: "POST" });
      if (res && res.ok) {
        setStatus("idle");
        notify("⏹️ 任务已停止");
      }
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const saveCustomModel = async () => {
    const val = customModelInput.trim();
    if (activeBot?.id) {
      await saveBotModel(activeBot.id, val);
    }
    setEditingCustomModel(false);
  };

  const createBot = async ({ name, role, persona, role_id, skills }) => {
    const bot = await api("/v1/bots", {
      method: "POST",
      body: { name, role, persona, role_id, skills },
    });
    const session = await api("/v1/sessions", {
      method: "POST",
      body: { kind: "direct", bot_ids: [bot.id], title: `和 ${bot.name} 的对话` },
    });
    await loadBasics();
    setActiveId(session.id);
    setModal(null);
  };

  const createGroup = async ({ title, botIds }) => {
    const session = await api("/v1/sessions", {
      method: "POST",
      body: { kind: "group", bot_ids: botIds, title },
    });
    await loadBasics();
    setActiveId(session.id);
    setModal(null);
  };

  const saveSettings = async (values, applyModelGlobally) => {
    const body = { values };
    if (applyModelGlobally !== undefined) {
      body.apply_model_globally = applyModelGlobally;
    }
    const res = await api("/v1/settings", { method: "PUT", body });
    setSettings(res.values);
    await loadBasics();
  };

  const toggleApp = async (app) => {
    const res = await api(`/v1/apps/${app.id}/connect`, {
      method: "POST",
      body: { connected: !app.connected },
    });
    setApps((prev) => prev.map((a) => (a.id === app.id ? { ...a, connected: res.connected } : a)));
  };

  const openVault = async () => {
    setVault((await api("/v1/vault")).items);
  };

  const wakeComputer = async () => {
    try {
      await api("/v1/computer/wake", { method: "POST" });
      setComputerState("awake");
    } catch (e) {
      setError(String(e.message || e));
    }
  };
  const sleepComputer = async () => {
    try {
      await api("/v1/computer/sleep", { method: "POST" });
      setComputerState("sleeping");
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const onScreenClick = async (ev) => {
    if (!controlMode || !screenMeta || !imgRef.current) return;
    const rect = imgRef.current.getBoundingClientRect();
    const sw = screenMeta.geometry.w;
    const sh = screenMeta.geometry.h;
    const scale = Math.min(rect.width / sw, rect.height / sh);
    const dw = sw * scale;
    const dh = sh * scale;
    const ox = rect.left + (rect.width - dw) / 2;
    const oy = rect.top + (rect.height - dh) / 2;
    const x = Math.round((ev.clientX - ox) / scale);
    const y = Math.round((ev.clientY - oy) / scale);
    if (x < 0 || y < 0 || x > sw || y > sh) return;
    try {
      await api("/v1/computer/input", { method: "POST", body: { type: "click", x, y } });
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const sendKeys = async (keys) => {
    try {
      await api("/v1/computer/input", { method: "POST", body: { type: "key", keys } });
    } catch (e) {
      setError(String(e.message || e));
    }
  };
  const sendText = async (text) => {
    if (!text) return;
    try {
      await api("/v1/computer/input", { method: "POST", body: { type: "type", text } });
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const openWorkbench = async () => {
    try {
      const res = await api("/v1/legacy-sso/ticket", { method: "POST" });
      const base = (getConn()?.baseUrl || "").replace(/\/$/, "");
      setWorkbenchSrc(`${base}/app/?sso=${encodeURIComponent(res.ticket)}&_t=${Date.now()}`);
      setView("workbench");
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  useEffect(() => {
    const conn = getConn();
    const invoke = window.__TAURI_INTERNALS__?.invoke;
    if (!conn || !invoke) return;
    invoke("local_bridge_start", { baseUrl: conn.baseUrl, token: conn.token })
      .catch(() => {});
  }, []);

  const decideLocal = async (approvalId, allow, scope = "once") => {
    const targetApproval = approvals.find((a) => a.approvalId === approvalId);
    const targetSess = targetApproval?.sessionId || activeId;
    try {
      await api(`/v1/local/approvals/${approvalId}`, {
        method: "POST",
        body: { allow, scope }
      });
      if (allow && scope === "session") {
        if (targetSess) {
          setSessionApproved((prev) => ({ ...prev, [targetSess]: true }));
        }
        notify("⚡ 已允许当前会话所有操作，后续操作将自动放行");
        setApprovals((prev) => prev.filter((a) => a.sessionId !== targetSess && a.approvalId !== approvalId));
      } else {
        setApprovals((prev) => prev.filter((a) => a.approvalId !== approvalId));
      }
    } catch (e) {
      setError(String(e.message || e));
      setApprovals((prev) => prev.filter((a) => a.approvalId !== approvalId));
    }
  };

  const toggleSessionAuth = async (sessionId, approved) => {
    if (!sessionId) return;
    try {
      await api(`/v1/local/sessions/${sessionId}/approval`, {
        method: "POST",
        body: { approved }
      });
      setSessionApproved((prev) => ({ ...prev, [sessionId]: approved }));
      notify(approved ? "⚡ 已开启当前会话全操作免审授权" : "已恢复逐次确认模式");
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const reloadMemories = () => setMemoriesTick((t) => t + 1);

  const addMemory = async () => {
    const content = memInput.trim();
    if (!content || !activeBot?.id) return;
    try {
      await api("/v1/memories", {
        method: "POST",
        body: { bot_id: activeBot.id, content, kind: "fact", importance: 2 },
      });
      setMemInput("");
      reloadMemories();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const deleteMemory = async (memoryId) => {
    try {
      await api(`/v1/memories/${memoryId}`, { method: "DELETE" });
      reloadMemories();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const bumpAuto = () => setAutoTick((x) => x + 1);

  const createRoutine = async () => {
    if (!activeBotId || !activeId) {
      setError("先在这个 Bot 的会话里操作（例行任务要挂在一个会话上）");
      return;
    }
    if (!rtnTitle.trim() || !rtnPrompt.trim()) {
      setError("任务名和执行内容都要填");
      return;
    }
    try {
      await api("/v1/routines", {
        method: "POST",
        body: {
          bot_id: activeBotId, session_id: activeId,
          title: rtnTitle.trim(), prompt: rtnPrompt.trim(),
          schedule: rtnSchedule.trim() || "interval:30m",
        },
      });
      setRtnTitle(""); setRtnPrompt("");
      bumpAuto();
      notify("已创建例行任务");
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const toggleRoutine = async (r) => {
    try {
      await api(`/v1/routines/${r.id}`, { method: "PATCH", body: { enabled: !r.enabled } });
      bumpAuto();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const runRoutineNow = async (r) => {
    try {
      await api(`/v1/routines/${r.id}/run`, { method: "POST" });
      notify(`已触发「${r.title}」，结果会发到它所在的会话`);
      bumpAuto();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const deleteRoutine = async (id) => {
    try {
      await api(`/v1/routines/${id}`, { method: "DELETE" });
      bumpAuto();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const assignPresetSkill = async (skillName) => {
    if (!activeBotId) return;
    try {
      await api("/v1/skills", {
        method: "POST",
        body: {
          name: skillName,
          description: `【${activeBot.name}】专属技能：${skillName}`,
          body: `【${activeBot.name}技能：${skillName}】\n针对当前任务「{input}」，按照专业标准执行「${skillName}」，给出准确、结构化且具备实操性的结论。`,
          bot_id: activeBotId,
        },
      });
      bumpAuto();
      notify(`已赋予技能：${skillName}`);
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const createSkill = async () => {
    if (!skName.trim() || !skBody.trim()) {
      setError("技能名和步骤都要填");
      return;
    }
    try {
      await api("/v1/skills", {
        method: "POST",
        body: {
          name: skName.trim(), description: skDesc.trim(), body: skBody.trim(),
          bot_id: skGlobal ? null : activeBotId,
        },
      });
      setSkName(""); setSkDesc(""); setSkBody("");
      bumpAuto();
      notify("技能已保存");
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const deleteSkill = async (id) => {
    try {
      await api(`/v1/skills/${id}`, { method: "DELETE" });
      bumpAuto();
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const fmtWhen = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };

  const notify = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2200);
  };

  const deleteSession = async (id) => {
    if (!window.confirm("删除这个会话？聊天记录会保留在审计日志里，但列表不再显示。")) return;
    try {
      await api(`/v1/sessions/${id}`, { method: "DELETE" });
      setCtx(null);
      if (activeId === id) setActiveId(null);
      await loadBasics();
      notify("已删除会话");
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const deleteBot = async (id) => {
    if (!window.confirm("删除这个 Bot？它独占的单聊会话也会一起下架。")) return;
    try {
      await api(`/v1/bots/${id}`, { method: "DELETE" });
      setCtx(null);
      if (activeBotId === id) setActiveId(null);
      await loadBasics();
      notify("已删除 Bot");
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const saveBotModel = async (botId, model) => {
    try {
      const updated = await api(`/v1/bots/${botId}`, {
        method: "PATCH", body: { model: model || null },
      });
      setBots((prev) => prev.map((b) => (b.id === botId ? { ...b, model: updated.model } : b)));
      setDetails((prev) => (prev && prev.id === botId ? { ...prev, model: updated.model } : prev));
      notify(`模型已切换为 ${model || effectiveModel}`);
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const generatePairCode = async () => {
    try {
      const res = await api("/v1/pairing/codes", { method: "POST" });
      setPairCode({ code: res.code, expires: Date.now() + res.expires_in * 1000 });
      notify("配对码已生成，5 分钟内有效");
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  useEffect(() => {
    if (!showScrollBottom) {
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages, activity, streamText, showScrollBottom]);

  // 详情与资料库：切换当前 Bot 或 Tab 时，加载对应当前 Bot 的数据
  useEffect(() => {
    if (!activeBotId) return;
    let cancelled = false;
    setDetails(null);
    setLibPath("");
    setPreview(null);
    if (tab === "details") {
      api(`/v1/bots/${activeBotId}/details`)
        .then((d) => { if (!cancelled) setDetails(d); })
        .catch((e) => { if (!cancelled) setError(String(e.message)); });
    }
    if (tab === "library") {
      api(`/v1/bots/${activeBotId}/library`)
        .then((l) => { if (!cancelled) setLibrary(l); })
        .catch((e) => { if (!cancelled) setError(String(e.message)); });
    }
    return () => { cancelled = true; };
  }, [tab, activeBotId]);

  // 选择 Bot 即直接进入该 Bot 的单聊会话（一个 Bot 对应一个独立会话）
  const selectBot = async (b) => {
    if (!b?.id) return;
    let s = sessions.find(
      (x) =>
        x.admin_bot_id === b.id ||
        (x.kind === "direct" && x.members?.includes(b.id))
    );
    if (!s) {
      try {
        s = await api("/v1/sessions", {
          method: "POST",
          body: { kind: "direct", bot_ids: [b.id], title: `和 ${b.name} 的对话` },
        });
        await loadBasics();
      } catch (err) {
        console.error("Auto create session failed", err);
      }
    }
    if (s) {
      setActiveId(s.id);
    }
    setShowMobileSidebar(false);
  };

  // 同步当前会话的免审授权状态
  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    api(`/v1/local/sessions/${activeId}/approval`)
      .then((d) => {
        if (!cancelled && d && typeof d.approved === "boolean") {
          setSessionApproved((prev) => ({ ...prev, [activeId]: d.approved }));
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [activeId]);

  // 详情页的例行任务 + 技能
  useEffect(() => {
    if (tab !== "details" || !activeBotId) return;
    let cancelled = false;
    api(`/v1/routines?bot_id=${activeBotId}`)
      .then((d) => { if (!cancelled) setRoutines(d); })
      .catch(() => {});
    api(`/v1/skills?bot_id=${activeBotId}`)
      .then((d) => { if (!cancelled) setSkillsList(d); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [tab, activeBotId, memoriesTick, autoTick]);

  // 详情页的长期记忆：切到详情或增删后重新拉一次
  useEffect(() => {
    if (tab !== "details" || !activeBotId) return;
    let cancelled = false;
    api(`/v1/memories?bot_id=${activeBotId}`)
      .then((data) => { if (!cancelled) setMemories(data); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [tab, activeBotId, memoriesTick]);

  // 整窗切换：赛博指挥舱（/app/），带免登录票据
  if (view === "workbench" && workbenchSrc) {
    return (
      <div className="workbench-shell">
        <iframe className="workbench-frame" title="cyberdeck" src={workbenchSrc} />
      </div>
    );
  }

  return (
    <div className={`app ${isMobile ? "is-mobile" : ""}`}>
      {(showMobileSidebar || showMobilePanel) && (
        <div
          className="sidebar-backdrop"
          onClick={() => {
            setShowMobileSidebar(false);
            setShowMobilePanel(false);
          }}
        />
      )}

      <aside className={`sidebar ${showMobileSidebar ? "mobile-open" : ""}`}>
        <div className="sidebar-tools">
          <button className="tool-btn" title="搜索 (Ctrl+K)" onClick={() => setModal("search")}>⌕</button>
          <button className="tool-btn" title="新建 Bot" onClick={() => setModal("newbot")}>＋</button>
        </div>

        <div className="bot-list">
          {bots.map((b) => (
            <button key={b.id}
              className={`bot-item ${activeBotId === b.id && (!activeSession || activeSession.kind !== "group") ? "active" : ""}`}
              onClick={() => selectBot(b)}
              onContextMenu={(e) => {
                e.preventDefault();
                setCtx({ x: e.clientX, y: e.clientY, kind: "bot", id: b.id, label: b.name });
              }}>
              <span className="avatar">{b.name.slice(0, 1)}</span>
              <span className="bot-meta">
                <span className="bot-name">{b.name}</span>
                <span className="bot-sub">{b.role || "assistant"}</span>
              </span>
              <span className={`dot ${activeBotId === b.id ? status : "idle"}`} />
            </button>
          ))}
        </div>

        {sessions.filter((s) => s.kind === "group").length > 0 && (
          <>
            <div className="section-label">群聊</div>
            <div className="session-list">
              {sessions.filter((s) => s.kind === "group").map((s) => (
                <button key={s.id} className={`session-item ${s.id === activeId ? "active" : ""}`}
                  onClick={() => {
                    setActiveId(s.id);
                    setShowMobileSidebar(false);
                  }}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    setCtx({ x: e.clientX, y: e.clientY, kind: "session", id: s.id, label: s.title });
                  }}>
                  ◇ {s.title || s.id}
                </button>
              ))}
            </div>
          </>
        )}

        <button className="connect-apps" onClick={() => setModal("apps")}>＋ 连接应用</button>
        <button className="connect-apps deck-btn" onClick={openWorkbench}>▦ 赛博指挥舱</button>
        <div className="sidebar-foot">
          {showMenu && (
            <div className="account-menu" role="menu">
              <div className="account-line" title={getConn()?.baseUrl}>
                <span className="account-server-dot" />
                <span className="account-server-text">{getConn()?.baseUrl || "未连接"}</span>
              </div>
              <button onClick={() => { setShowMenu(false); setSettingsTab("general"); setModal("settings"); }}>
                <span className="menu-icon">⚙</span>
                <span>设置</span>
              </button>
              <button onClick={() => { setShowMenu(false); setSettingsTab("vault"); setModal("settings"); openVault(); }}>
                <span className="menu-icon">🔒</span>
                <span>保险库</span>
              </button>
              <button onClick={() => { setShowMenu(false); setSettingsTab("general"); setModal("settings"); generatePairCode(); }}>
                <span className="menu-icon">📱</span>
                <span>添加设备（配对码）</span>
              </button>
              <button onClick={() => { setShowMenu(false); openWorkbench(); }}>
                <span className="menu-icon">▦</span>
                <span>打开项目工作台（赛博指挥舱）</span>
              </button>
              <button className="danger" onClick={onLogout}>
                <span className="menu-icon">⏻</span>
                <span>退出登录</span>
              </button>
            </div>
          )}
          <button
            className={`foot-btn ${showMenu ? "active" : ""}`}
            onClick={() => setShowMenu((v) => !v)}
            title="账户设置"
          >
            <span className="foot-btn-left">◍ 账户</span>
            <span className="foot-arrow">{showMenu ? "▾" : "▴"}</span>
          </button>
        </div>
      </aside>

      <main className="chat">
        <header className="chat-head">
          <div className="chat-head-left">
            <button
              className="mobile-toggle-btn"
              onClick={() => {
                setShowMobileSidebar((v) => !v);
                setShowMobilePanel(false);
              }}
              title="切换菜单"
            >
              ☰
            </button>
            <div>
              <div className="chat-title">{activeBot?.name || "Bot"}</div>
              <div className="chat-sub">
                <span className={`live-dot ${connected ? "on" : ""}`} />
                {connected ? "已连接" : "连接中…"} · {statusText(status)}
              </div>
            </div>
          </div>
          <div className="chat-head-right">
            {activeId && sessionApproved[activeId] && (
              <div className="session-auth-pill" title="此对话所有本地操作免确认授权中，点击可收回">
                <span className="dot on" />
                <span>全操作已授权</span>
                <button className="revoke-btn" onClick={() => toggleSessionAuth(activeId, false)}>收回</button>
              </div>
            )}
            <div className="chat-usage">
              {Number(settings?.daily_tokens) === 0
                ? `今日 ${usage.toLocaleString()} tokens · 不限额`
                : `今日 ${usage.toLocaleString()} / ${(settings?.daily_tokens || 500000).toLocaleString()} tokens`}
            </div>
            <button
              className="mobile-toggle-btn"
              onClick={() => {
                setShowMobilePanel((v) => !v);
                setShowMobileSidebar(false);
              }}
              title="助手详情与控制"
            >
              ⚙
            </button>
          </div>
        </header>

        <div className="messages" ref={listRef} onScroll={handleMessagesScroll}>
          {messages.map((m) => (
            m.kind === "artifact.attached" ? (
              <ArtifactBubble key={`art-${m.id}`} item={m.artifact} ts={m.ts}
                onOpen={(art) => setLightbox(art)} />
            ) : (
              <div key={m.id} className={`bubble ${m.kind === "message.user" ? "me" : "bot"}`}>
                {m.kind === "message.bot" ? (
                  <div className="bubble-text md"
                       dangerouslySetInnerHTML={{ __html: renderMarkdown(m.text) }} />
                ) : (
                  <>
                    {m.attachments && m.attachments.length > 0 && (
                      <div className="bubble-attachments">
                        {m.attachments.map((att, idx) => (
                          <MessageAttachmentItem
                            key={att.artifact_id || att.path || idx}
                            item={att}
                            onOpen={(art) => setLightbox(art)}
                          />
                        ))}
                      </div>
                    )}
                    {m.text ? <div className="bubble-text">{m.text}</div> : null}
                  </>
                )}
                <div className="bubble-time">{formatTime(m.ts)}</div>
              </div>
            )
          ))}
          {streamText?.text && (
            <div className="bubble bot streaming">
              <div className="bubble-text md"
                   dangerouslySetInnerHTML={{ __html: renderMarkdown(streamText.text) }} />
              <span className="stream-caret" />
            </div>
          )}
          {activity.length > 0 && (
            <details className="activity" open={status === "thinking" || status === "failed"}>
              <summary>本轮过程（{activity.length}）</summary>
              {activity.map((a) => (
                <div key={a.id} className={`activity-row ${a.state}`}>
                  <span className="tool-name">{a.tool}</span>
                  <span className="tool-state">{stateLabel(a.state)}</span>
                  <span className="tool-detail">{(a.summary || JSON.stringify(a.args || {})).slice(0, 160)}</span>
                </div>
              ))}
            </details>
          )}
        </div>

        {showScrollBottom && (
          <button
            type="button"
            className="scroll-bottom-btn"
            onClick={() => scrollToBottom(true)}
            title="回到最新消息"
            aria-label="回到最新消息"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 5v14" />
              <path d="m19 12-7 7-7-7" />
            </svg>
          </button>
        )}

        <div
          className={`composer ${isDragging ? "drag-over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={(e) => {
            e.preventDefault();
            setIsDragging(false);
          }}
          onDrop={(e) => {
            e.preventDefault();
            setIsDragging(false);
            if (e.dataTransfer?.files?.length) {
              enqueueFiles(e.dataTransfer.files);
            }
          }}
        >
          {error && <div className="error-bar" onClick={() => setError("")}>{error}（点击关闭）</div>}

          {/* 附件托盘 (Attachment Tray) */}
          {attachments.length > 0 && (
            <div className="composer-tray">
              {attachments.map((item) => (
                <div key={item.id} className={`tray-card ${item.status}`}>
                  <div className="tray-thumb-wrap">
                    <img
                      src={item.previewUrl || item.url}
                      alt={item.name}
                      className="tray-thumb"
                      onClick={() => {
                        if (item.previewUrl || item.url) {
                          setLightbox({ url: item.previewUrl || item.url, name: item.name });
                        }
                      }}
                      title="点击预览大图"
                    />
                    {item.status === "uploading" && (
                      <div className="tray-spinner-overlay" title="上传中…">
                        <span className="tray-spinner" />
                      </div>
                    )}
                    {item.status === "error" && (
                      <div className="tray-error-overlay" title="上传失败">
                        ⚠️
                      </div>
                    )}
                  </div>
                  <div className="tray-info">
                    <span className="tray-name" title={item.name}>{item.name}</span>
                    <span className="tray-size">{formatFileSize(item.size)}</span>
                  </div>
                  <button
                    type="button"
                    className="tray-close"
                    onClick={() => removeAttachment(item.id)}
                    title="移除图片"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="composer-row">
            <input
              type="file"
              ref={fileInputRef}
              style={{ display: "none" }}
              multiple
              accept="image/*"
              onChange={handleFileUpload}
            />
            <button
              type="button"
              className="attach-btn"
              title="添加图片 (支持多选 / 粘贴 / 拖拽)"
              onClick={() => fileInputRef.current?.click()}
              disabled={attachments.length >= 5 || isUploading}
            >
              {isUploading ? "⏳" : "📎"}
            </button>
            <textarea
              value={draft}
              placeholder={`给 ${activeBot?.name || "Bot"} 发消息…`}
              onChange={(e) => setDraft(e.target.value)}
              onPaste={handlePaste}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (canSend) {
                    send();
                  }
                }
              }}
            />
            {isRunning ? (
              canSend ? (
                <button
                  type="button"
                  className="send-btn btn-steer"
                  onClick={send}
                  title="在途转向：无需等待，将新要求直接注入给正在执行中的 Agent"
                >
                  ↪ 转向 / 追加
                </button>
              ) : (
                <button
                  type="button"
                  className="send-btn btn-abort"
                  onClick={abortCurrentSession}
                  title="停止当前正在执行的任务"
                >
                  ⏹ 停止
                </button>
              )
            ) : (
              <button
                type="button"
                className="send-btn"
                onClick={send}
                disabled={!canSend}
                title={canSend ? "发送消息 (Enter)" : isUploading ? "图片上传中…" : "请输入消息或添加图片"}
              >
                {isUploading ? "上传中…" : "发送"}
              </button>
            )}
          </div>

          <div className="hermes-toolbar">
            {/* Hermes 极简模型切换 Pill */}
            <div className="hermes-pill-wrapper" ref={modelPickerRef}>
              <button
                type="button"
                className={`hermes-pill ${showModelPicker ? "active" : ""}`}
                onClick={() => {
                  setShowModelPicker(!showModelPicker);
                  setShowReasoningPicker(false);
                }}
                title={`当前生效模型: ${effectiveModel} (点击切换)`}
              >
                <span className="hermes-pill-dot" />
                <span className="hermes-pill-text">{effectiveModel}</span>
                <svg className="hermes-chevron" viewBox="0 0 16 16" width="10" height="10">
                  <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>

              {showModelPicker && (
                <div className="hermes-popover model-popover">
                  <div className="hermes-popover-header">
                    <span>可用模型</span>
                    <span className="hermes-popover-count">{modelOptions.length} 个模型</span>
                  </div>
                  <div className="hermes-popover-list">
                    {modelOptions.map((m) => {
                      const isSelected = m === effectiveModel;
                      return (
                        <div
                          key={m}
                          className={`hermes-popover-item ${isSelected ? "selected" : ""}`}
                          onClick={() => {
                            saveBotModel(activeBot?.id, m);
                            setShowModelPicker(false);
                          }}
                        >
                          <span className="hermes-item-label">{m}</span>
                          {isSelected && <span className="hermes-item-check">✓</span>}
                        </div>
                      );
                    })}
                  </div>
                  <div className="hermes-popover-divider" />
                  <button
                    type="button"
                    className="hermes-popover-action hermes-popover-settings-btn"
                    onClick={() => {
                      setShowModelPicker(false);
                      setSettingsTab("models");
                      setModal("settings");
                    }}
                  >
                    <span className="popover-action-icon">⚙️</span>
                    <span>配置第三方模型与自定义 API...</span>
                  </button>
                </div>
              )}
            </div>

            {/* Hermes 极简自适应思考推演档位 (仅在支持推理的模型展示，绝不显示丑陋的“不支持”占位块) */}
            {reasoningLevels.length > 0 && (
              <div className="hermes-pill-wrapper" ref={reasoningPickerRef}>
                <button
                  type="button"
                  className={`hermes-pill ${showReasoningPicker ? "active" : ""}`}
                  onClick={() => {
                    setShowReasoningPicker(!showReasoningPicker);
                    setShowModelPicker(false);
                  }}
                  title="思考推演强度设置"
                >
                  <span className="hermes-pill-icon">🧠</span>
                  <span className="hermes-pill-text">{REASONING_SHORT_LABELS[reasoningEffort] || reasoningEffort}</span>
                  <svg className="hermes-chevron" viewBox="0 0 16 16" width="10" height="10">
                    <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>

                {showReasoningPicker && (
                  <div className="hermes-popover reasoning-popover">
                    <div className="hermes-popover-header">
                      <span>思考推演强度</span>
                    </div>
                    <div className="hermes-popover-list">
                      {reasoningLevels.map((lvl) => {
                        const isSelected = lvl === reasoningEffort;
                        return (
                          <div
                            key={lvl}
                            className={`hermes-popover-item ${isSelected ? "selected" : ""}`}
                            onClick={() => {
                              setReasoningEffort(lvl);
                              setShowReasoningPicker(false);
                            }}
                          >
                            <span className="hermes-item-label">{REASONING_LABELS[lvl] || lvl}</span>
                            {isSelected && <span className="hermes-item-check">✓</span>}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </main>

      <aside className={`panel ${showMobilePanel ? "mobile-open" : ""}`}>
        <div className="tabs">
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "tab active" : "tab"} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </div>

        {tab === "details" && (
          <div className="panel-body">
            <div className="detail-head">
              <span className="avatar big">{activeBot?.name?.slice(0, 1) || "?"}</span>
              <div>
                <div className="detail-name">{details?.name || activeBot?.name}</div>
                <div className="detail-role">{details?.role || activeBot?.role || "assistant"}</div>
              </div>
            </div>
            <div className="kv model-row">
              <span>模型</span>
              <select
                value={effectiveModel}
                onChange={(e) => saveBotModel(activeBot.id, e.target.value)}
              >
                {modelOptions.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
            <div className="kv"><span>今日用量</span><b>{(details?.usage_today_tokens || 0).toLocaleString()} tokens</b></div>
            <div className="kv"><span>会话</span><b>{details?.sessions?.length || 1}</b></div>
            <div className="block-title">人格</div>
            <p className="persona">{details?.persona || activeBot?.persona || "—"}</p>

            <div className="block-title">
              长期记忆 <span className="mem-count">{memories.total || 0}</span>
            </div>
            <div className="mem-add">
              <input value={memInput} spellCheck={false}
                placeholder="教它记住一条，如：客户叫王总，偏好短回复"
                onChange={(e) => setMemInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addMemory()} />
              <button className="mini-btn" onClick={addMemory}>记住</button>
            </div>
            <div className="mem-list">
              {(memories.items || []).map((m) => (
                <div key={m.id} className="mem-item">
                  <span className={`mem-kind k-${m.kind}`}>
                    {{ fact: "事实", preference: "偏好", decision: "决定", note: "备注" }[m.kind] || m.kind}
                  </span>
                  <span className="mem-text">{m.content}</span>
                  <button className="mem-del" title="删除这条记忆"
                    onClick={() => deleteMemory(m.id)}>×</button>
                </div>
              ))}
              {!memories.items?.length && (
                <p className="hint">还没有记忆。对话里它会自己学，你也可以手动教。</p>
              )}
            </div>
            <div className="block-title">
              例行任务 <span className="mem-count">{routines.count || 0}</span>
            </div>
            <div className="mem-add">
              <input value={rtnTitle} spellCheck={false} placeholder="任务名，如 每日早报"
                onChange={(e) => setRtnTitle(e.target.value)} />
              <input value={rtnSchedule} spellCheck={false} className="rtn-sched"
                placeholder="interval:30m 或 daily:09:00"
                onChange={(e) => setRtnSchedule(e.target.value)} />
            </div>
            <textarea className="rtn-prompt" value={rtnPrompt} spellCheck={false}
              placeholder="到点让它做什么？例如：汇总昨天的工作进展，列 3 条要点"
              onChange={(e) => setRtnPrompt(e.target.value)} />
            <div className="mem-add">
              {["interval:30m", "interval:2h", "daily:09:00"].map((s) => (
                <button key={s} className="mini-btn ghost" onClick={() => setRtnSchedule(s)}>{s}</button>
              ))}
              <button className="mini-btn" onClick={createRoutine}>新建任务</button>
            </div>
            <div className="mem-list">
              {(routines.items || []).map((r) => (
                <div key={r.id} className="mem-item rtn-item">
                  <span className="rtn-title">{r.title}</span>
                  <span className="rtn-meta">
                    {r.schedule_human}
                    {r.enabled ? ` · 下次 ${fmtWhen(r.next_run_at)}` : " · 已停用"}
                    {r.run_count ? ` · 跑过 ${r.run_count} 次` : ""}
                  </span>
                  <button className="mini-btn ghost" onClick={() => runRoutineNow(r)}>立即执行</button>
                  <button className="mini-btn ghost" onClick={() => toggleRoutine(r)}>
                    {r.enabled ? "停用" : "启用"}
                  </button>
                  <button className="mem-del" title="删除任务" onClick={() => deleteRoutine(r.id)}>×</button>
                </div>
              ))}
              {!routines.items?.length && (
                <p className="hint">还没有例行任务。给它定个每天/每小时的活。</p>
              )}
            </div>

            <div className="block-title">
              技能 <span className="mem-count">{skillsList.count || 0}</span>
            </div>
            <p className="hint">
              技能 = 沉淀好的做事流程，Bot 接到任务会先找技能再动手。正文里可用 <code>{'{input}'}</code> 占位。
            </p>

            {/* 快速从角色库赋予技能 */}
            {(() => {
              const matchedRole = rolePresets.find(
                (r) => r.tag === activeBot?.role || r.name === activeBot?.role || r.name === activeBot?.name
              );
              const targetRole = rolePresets.find((r) => r.id === selectedPresetRoleId) || matchedRole;
              const unassignedSkills = (targetRole?.skills || "")
                .split(",")
                .map((s) => s.trim())
                .filter((s) => s && !skillsList.items?.some((it) => it.name === s));

              return (
                <div className="skill-quick-assign">
                  <div className="skill-quick-head">
                    <span>💡 角色技能赋予（当前：{activeBot?.name}）：</span>
                    <select
                      value={targetRole?.id || ""}
                      onChange={(e) => setSelectedPresetRoleId(e.target.value)}
                    >
                      <option value="">-- 选择角色模板库 --</option>
                      {rolePresets.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.name} ({r.industry || "通用"})
                        </option>
                      ))}
                    </select>
                  </div>
                  {unassignedSkills.length > 0 ? (
                    <div className="skill-pills-row">
                      {unassignedSkills.map((sk) => (
                        <button
                          key={sk}
                          className="skill-add-pill"
                          title={`点击为 ${activeBot?.name} 赋予技能「${sk}」`}
                          onClick={() => assignPresetSkill(sk)}
                        >
                          + 赋予「{sk}」
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className="hint" style={{ margin: 0 }}>
                      {targetRole ? `「${targetRole.name}」的推荐技能已全部赋予该助手。` : "请在上方下拉菜单选择角色模板。"}
                    </p>
                  )}
                </div>
              );
            })()}
            <div className="mem-add">
              <input value={skName} spellCheck={false} placeholder="技能名，如 写周报"
                onChange={(e) => setSkName(e.target.value)} />
              <input value={skDesc} spellCheck={false} placeholder="什么时候用（一句话）"
                onChange={(e) => setSkDesc(e.target.value)} />
            </div>
            <textarea className="rtn-prompt" value={skBody} spellCheck={false}
              placeholder={"具体步骤，例如：\n1. 先看本周工作区文件\n2. 按 进展/风险/下周计划 三段写\n3. 结尾列出待确认事项"}
              onChange={(e) => setSkBody(e.target.value)} />
            <div className="mem-add">
              <label className="check-inline">
                <input type="checkbox" checked={skGlobal}
                  onChange={(e) => setSkGlobal(e.target.checked)} />
                全局（所有 Bot 可用）
              </label>
              <button className="mini-btn" onClick={createSkill}>保存技能</button>
            </div>
            <div className="mem-list">
              {(skillsList.items || []).map((s) => (
                <div key={s.id} className="mem-item">
                  <span className="mem-kind k-fact">{s.bot_id ? "私有" : "全局"}</span>
                  <span className="mem-text"><b>{s.name}</b>{s.description ? ` — ${s.description}` : ""}</span>
                  {s.use_count ? <span className="rtn-meta">用过 {s.use_count}</span> : null}
                  <button className="mem-del" title="删除技能" onClick={() => deleteSkill(s.id)}>×</button>
                </div>
              ))}
              {!skillsList.items?.length && <p className="hint">还没有技能。</p>}
            </div>
          </div>
        )}

        {tab === "library" && (
          <div className="panel-body">
            <div className="crumb">
              <button className="link" onClick={() => openLibraryPath("")}>工作区</button>
              {libPath && <span>/ {libPath}</span>}
            </div>
            {libPath && <button className="link" onClick={() => openLibraryPath(parentOf(libPath))}>← 上一层</button>}
            <div className="file-list">
              {(library?.files?.items || []).map((it) => (
                <button key={it.name} className="file-row"
                  onClick={() => (it.type === "dir" ? openLibraryPath(join(libPath, it.name)) : openFile(it.name))}>
                  <span className="file-icon">{it.type === "dir" ? "▸" : "·"}</span>
                  <span className="file-name">{it.name}</span>
                  {it.bytes ? <span className="file-size">{it.bytes}B</span> : null}
                </button>
              ))}
              {!library?.files?.items?.length && <p className="hint">工作区还是空的。让 Bot 干点活试试。</p>}
            </div>
            {preview && <pre className="preview">{preview.text.slice(0, 4000)}</pre>}
          </div>
        )}

        {tab === "computer" && (
          <div className="panel-body">
            <div className="screen-wrap" onClick={() => setFullscreen(true)} title="点击全屏">
              {frame ? <img className="screen" src={frame} alt="computer" /> : <div className="screen placeholder">{computerState === "sleeping" ? "电脑休眠中" : "等待画面…"}</div>}
            </div>
            <div className="screen-bar">
              <span className={`computer-state ${computerState}`}>
                {computerState === "awake" ? "● 运行中" : computerState === "sleeping" ? "○ 休眠" : "… 未知"}
              </span>
              <button className="mini-btn" onClick={() => setFullscreen(true)}>全屏</button>
              {computerState === "sleeping"
                ? <button className="mini-btn" onClick={wakeComputer}>唤醒</button>
                : <button className="mini-btn" onClick={sleepComputer}>休眠</button>}
            </div>
            <div className="kv"><span>分辨率</span><b>{screenMeta ? `${screenMeta.geometry.w}×${screenMeta.geometry.h}` : "—"}</b></div>
            <div className="kv"><span>帧率</span><b>{screenMeta ? `${screenMeta.fps} fps` : "—"}</b></div>
            <p className="hint">点击画面进入全屏；全屏后可切换"取得控制权"直接操作服务器桌面。</p>
          </div>
        )}
      </aside>

      {modal === "search" && (
        <SearchModal onClose={() => setModal(null)} bots={bots} sessions={sessions}
          onPickBot={(id) => { const b = bots.find((x) => x.id === id); if (b) selectBot(b); setModal(null); }}
          onPickSession={(id) => { setActiveId(id); setModal(null); }}
          onAction={(a) => {
            if (a === "newbot") setModal("newbot");
            if (a === "group") setModal("group");
            if (a === "settings") { setSettingsTab("general"); setModal("settings"); }
            if (a === "apps") setModal("apps");
          }} />
      )}
      {modal === "newbot" && <NewBotModal onSubmit={createBot} onClose={() => setModal(null)} />}
      {modal === "group" && <GroupModal bots={bots} onSubmit={createGroup} onClose={() => setModal(null)} />}
      {modal === "apps" && <AppsModal onClose={() => setModal(null)} />}
      {modal === "settings" && (
        <SettingsModal
          tab={settingsTab} setTab={setSettingsTab}
          settings={settings} onSave={saveSettings}
          computerState={computerState} onWake={wakeComputer} onSleep={sleepComputer}
          vault={vault} onOpenVault={openVault}
          models={models} pairCode={pairCode} onGenPairCode={generatePairCode}
          integrations={integrations} localDevices={localDevices}
          onClose={() => setModal(null)}
          onReloadAll={async () => {
            await loadSettings();
            await loadBasics();
          }}
        />
      )}

      {ctx && (
        <div className="ctx-menu" style={{ left: ctx.x, top: ctx.y }}
             onMouseDown={(e) => e.stopPropagation()}>
          <div className="ctx-title">{ctx.label || "操作"}</div>
          {ctx.kind === "session" && (
            <button onClick={() => deleteSession(ctx.id)}>删除会话</button>
          )}
          {ctx.kind === "bot" && (
            <button onClick={() => deleteBot(ctx.id)}>删除 Bot</button>
          )}
          <button onClick={() => { setCtx(null); openWorkbench(); }}>打开赛博指挥舱</button>
        </div>
      )}

      {workspaceOpen && (
        <div className="modal-mask" onClick={() => setWorkspaceOpen(false)}>
          <div className="workspace-modal" onClick={(e) => e.stopPropagation()}>
            <div className="workspace-head">
              <span>项目工作台（旧版 OA Agent）</span>
              <button onClick={() => setWorkspaceOpen(false)}>关闭</button>
            </div>
            <iframe title="workbench" src={`${getConn()?.baseUrl || ""}/legacy`} />
          </div>
        </div>
      )}

      {approvals.length > 0 && (
        <div className="modal-mask">
          <div className="modal approval-modal">
            <div className="approval-modal-head">
              <h3>本地电脑操作请求</h3>
              <div className="approval-session-tag">
                当前对话：{activeSession?.title || activeBot?.name || "协作会话"}
              </div>
            </div>
            {approvals.map((a) => (
              <div key={a.approvalId} className="approval-card">
                <div className="approval-head">
                  <div className="approval-action">{a.action}</div>
                  <span className={`risk-badge r-${a.risk || "normal"}`}>
                    {{ low: "低风险", medium: "中风险", high: "高风险" }[a.risk] || "常规"}
                  </span>
                </div>
                {a.detail ? <div className="approval-detail">{a.detail}</div>
                  : <div className="approval-detail muted">（无参数）</div>}
                {a.preview && a.preview.kind === "write" && a.preview.excerpt ? (
                  <pre className="approval-preview">{a.preview.excerpt}</pre>
                ) : null}
                {a.preview && a.preview.kind === "exec" && a.preview.cmd ? (
                  <pre className="approval-preview">$ {a.preview.cmd}</pre>
                ) : null}
                {a.preview && a.preview.kind === "type" && a.preview.excerpt ? (
                  <pre className="approval-preview">{a.preview.excerpt}</pre>
                ) : null}
                <div className="modal-actions approval-actions">
                  <button className="btn-reject" onClick={() => decideLocal(a.approvalId, false, "once")}>
                    拒绝
                  </button>
                  <button className="btn-once" onClick={() => decideLocal(a.approvalId, true, "once")}>
                    运行这一次
                  </button>
                  <button className="primary btn-session" onClick={() => decideLocal(a.approvalId, true, "session")}>
                    ⚡ 允许当前会话所有操作
                  </button>
                </div>
              </div>
            ))}
            <p className="hint">
              这些操作将在您的本机执行。「允许当前会话所有操作」可避免反复确认，可在顶部状态栏随时收回。
            </p>
          </div>
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}

      {lightbox && (
        <div className="artifact-viewer" onClick={() => setLightbox(null)}>
          <div className="artifact-viewer-bar">
            <span className="artifact-viewer-name">{lightbox.name || "原图"}</span>
            <button className="seg" onClick={() => setLightbox(null)}>关闭</button>
          </div>
          <img className="artifact-viewer-img" src={lightbox.url} alt={lightbox.name || "original"}
               onClick={(e) => e.stopPropagation()} draggable={false} />
        </div>
      )}

      {fullscreen && (
        <div className="fullscreen-mask">
          <div className="fullscreen-bar">
            <span className="fs-title">{activeBot?.name} 的屏幕 · {computerState === "awake" ? "运行中" : computerState}</span>
            <div className="fs-actions">
              <button className={`seg ${!controlMode ? "on" : ""}`} onClick={() => setControlMode(false)}>仅观看</button>
              <button className={`seg ${controlMode ? "on" : ""}`} onClick={() => setControlMode(true)}>取得控制权</button>
              <button className="seg" onClick={() => sendKeys("Return")}>Enter</button>
              <button className="seg" onClick={() => sendKeys("Escape")}>Esc</button>
              <button className="seg" onClick={() => sendKeys("ctrl+t")}>新标签</button>
              <button className="seg" onClick={() => setFullscreen(false)}>退出全屏</button>
            </div>
          </div>
          <div className={`fullscreen-stage ${controlMode ? "controlling" : ""}`}>
            {frame ? (
              <img ref={imgRef} className="fullscreen-img" src={frame} alt="computer"
                onClick={onScreenClick} draggable={false} />
            ) : (
              <div className="screen placeholder">{computerState === "sleeping" ? "电脑休眠中，点右上角唤醒" : "等待画面…"}</div>
            )}
          </div>
          <form className="fs-input" onSubmit={(e) => { e.preventDefault(); const v = e.target.elements.txt.value; e.target.elements.txt.value = ""; sendText(v); }}>
            <input name="txt" placeholder={controlMode ? "输入文字后回车，会键入服务器桌面（先点一下画面定位光标）" : "先取得控制权才能输入"} disabled={!controlMode} />
            <button disabled={!controlMode}>键入</button>
          </form>
        </div>
      )}
    </div>
  );

  function openLibraryPath(path) {
    const botId = activeBotId || bots[0]?.id || "bot_chief";
    setLibPath(path);
    setPreview(null);
    api(`/v1/bots/${botId}/library?path=${encodeURIComponent(path)}`)
      .then(setLibrary).catch((e) => setError(String(e.message || e)));
  }

  async function openFile(name) {
    const path = libPath ? `${libPath}/${name}` : name;
    try {
      setPreview({ path, text: await api(`/v1/workspace/raw?path=${encodeURIComponent(path)}`) });
    } catch (e) {
      setError(String(e.message || e));
    }
  }
}

/* ============================== 截图 / artifact 气泡 ============================== */
// 事件里只有 {artifact_id, url, bytes}，像素要带 Bearer 单独取：
// 直接用 <img src="/v1/artifacts/x"> 是取不到的（img 不能带 Authorization 头），
// 所以先 fetch 成 blob，再用 object URL 渲染，顺便按 id 缓存，滚动重渲染不会重复下载。
const ARTIFACT_URLS = new Map();

function clearArtifactCache() {
  for (const url of ARTIFACT_URLS.values()) URL.revokeObjectURL(url);
  ARTIFACT_URLS.clear();
}

function formatFileSize(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function artifactObjectUrl(art) {
  if (art?.previewUrl) return art.previewUrl;
  const key = art.artifact_id || art.url || art.name;
  if (!key) return "";
  const hit = ARTIFACT_URLS.get(key);
  if (hit) return hit;
  const conn = getConn() || {};
  const base = (conn.baseUrl || "").replace(/\/$/, "");
  let path = art.url || (art.artifact_id ? `/v1/artifacts/${art.artifact_id}` : "");
  if (!path) return "";
  if (path.startsWith("blob:") || path.startsWith("data:")) return path;
  let fullUrl = path;
  if (!fullUrl.startsWith("http://") && !fullUrl.startsWith("https://")) {
    fullUrl = `${base}${path.startsWith("/") ? "" : "/"}${path}`;
  }
  const res = await fetch(fullUrl, {
    headers: conn.token ? { Authorization: `Bearer ${conn.token}` } : {},
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  ARTIFACT_URLS.set(key, url);
  return url;
}

function MessageAttachmentItem({ item, onOpen }) {
  const [src, setSrc] = useState(item.previewUrl || "");
  const [loading, setLoading] = useState(!item.previewUrl);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let alive = true;
    if (src) return;
    if (!item?.artifact_id && !item?.url) {
      setLoading(false);
      setErr(true);
      return;
    }
    setLoading(true);
    setErr(false);
    artifactObjectUrl(item)
      .then((u) => {
        if (alive) {
          setSrc(u);
          setLoading(false);
        }
      })
      .catch(() => {
        if (alive) {
          setErr(true);
          setLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [item?.artifact_id, item?.url, src]);

  const handleClick = () => {
    if (src) {
      onOpen({ url: src, name: item.name });
    }
  };

  return (
    <div className="bubble-att-item" onClick={handleClick} title={item.name || "点击放大预览"}>
      {src ? (
        <img src={src} alt={item.name || "附件图片"} className="bubble-att-thumb" />
      ) : loading ? (
        <div className="bubble-att-loading">
          <span className="tray-spinner" />
        </div>
      ) : (
        <div className="bubble-att-err" title="图片加载失败">
          ⚠️
        </div>
      )}
      <div className="bubble-att-info">
        <span className="bubble-att-name" title={item.name}>{item.name || "图片"}</span>
        {item.bytes ? <span className="bubble-att-size">{formatFileSize(item.bytes)}</span> : null}
      </div>
    </div>
  );
}

function ArtifactBubble({ item, ts, onOpen }) {
  const [url, setUrl] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    if (!item?.artifact_id && !item?.url) {
      setErr("缺少 artifact 引用");
      return () => { alive = false; };
    }
    setErr("");
    artifactObjectUrl(item)
      .then((u) => { if (alive) setUrl(u); })
      .catch((e) => { if (alive) setErr(String(e.message || e)); });
    return () => { alive = false; };
  }, [item?.artifact_id, item?.url]);
  const kb = item?.bytes ? Math.max(1, Math.round(item.bytes / 1024)) : 0;
  return (
    <div className="bubble bot artifact">
      {url ? (
        <img className="artifact-img" src={url} alt={item.name || "截图"} draggable={false}
          onClick={() => onOpen({ url, name: item.name })} title="点击看原图" />
      ) : (
        <div className="artifact-loading">
          {err ? `图片加载失败：${err}` : "截图加载中…"}
        </div>
      )}
      <div className="artifact-meta">
        <span className="artifact-name">{item?.name || "截图"}</span>
        {kb > 0 && <span className="artifact-size">{kb} KB</span>}
        {url && (
          <button className="artifact-open" onClick={() => onOpen({ url, name: item?.name })}>
            看原图
          </button>
        )}
      </div>
      <div className="bubble-time">{formatTime(ts)}</div>
    </div>
  );
}

/* ============================== 弹层：搜索 ============================== */
function SearchModal({ onClose, bots, sessions, onPickBot, onPickSession, onAction }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState(null);
  const inputRef = useRef(null);
  useEffect(() => inputRef.current?.focus(), []);
  useEffect(() => {
    if (!q.trim()) { setRes(null); return; }
    const timer = setTimeout(() => {
      api(`/v1/search?q=${encodeURIComponent(q.trim())}`).then(setRes).catch(() => setRes(null));
    }, 180);
    return () => clearTimeout(timer);
  }, [q]);

  const actions = [
    { id: "newbot", label: "新建 Bot" },
    { id: "group", label: "创建群聊" },
    { id: "apps", label: "连接应用" },
    { id: "settings", label: "设置" },
  ];
  const filtered = actions.filter((a) => !q || a.label.includes(q));

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="search-modal" onClick={(e) => e.stopPropagation()}>
        <div className="search-head">
          <span>⌕</span>
          <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="搜索 Bot、会话、消息、文件…（Ctrl+K）" />
          <kbd>ESC</kbd>
        </div>
        <div className="search-body">
          {resourceList("Bot", res?.bots, (b) => onPickBot(b.id), (b) => `${b.name} · ${b.role || ""}`)}
          {resourceList("会话", res?.sessions, (s) => onPickSession(s.id), (s) => s.title || s.id)}
          {resourceList("消息", res?.messages, (m) => onPickSession(m.session_id), (m) => m.text)}
          {resourceList("文件", res?.files, null, (f) => f.path)}
          {(filtered.length > 0 || !res) && (
            <>
              <div className="search-section">操作</div>
              {filtered.map((a) => (
                <button key={a.id} className="search-row action" onClick={() => onAction(a.id)}>
                  <span className="row-main">{a.label}</span>
                </button>
              ))}
            </>
          )}
          {!res && <p className="hint search-hint">输入关键词开始搜索；Ctrl+K 可随时唤起。</p>}
        </div>
      </div>
    </div>
  );
}

function resourceList(title, items, onPick, render) {
  if (!items?.length) return null;
  return (
    <>
      <div className="search-section">{title}（{items.length}）</div>
      {items.map((it, i) => (
        <button key={i} className="search-row" onClick={() => onPick && onPick(it)} disabled={!onPick}>
          <span className="row-main">{render(it)}</span>
          {it.ts ? <span className="row-sub">{formatTime(it.ts)}</span> : null}
        </button>
      ))}
    </>
  );
}

/* ============================== 弹层：新建 Bot ============================== */
const DEFAULT_GENERAL_PRESET = {
  id: "ROLE-GENERAL",
  name: "通用助理",
  role: "assistant",
  persona: "你是全能通用助手，负责协助用户处理日常各类综合事务、深度思考分析、执行工作流与工具调度。说话简明务实，给结论、给结构、给证据；遇到不确定时明确说明。",
  skills: ["日常问答与检索", "任务拆解与总结", "工具调度与协助", "第一性原理决策分析"],
};

function NewBotModal({ onSubmit, onClose }) {
  const [roles, setRoles] = useState([]);
  const [selectedRoleId, setSelectedRoleId] = useState(DEFAULT_GENERAL_PRESET.id);
  const [name, setName] = useState(DEFAULT_GENERAL_PRESET.name);
  const [role, setRole] = useState(DEFAULT_GENERAL_PRESET.role);
  const [persona, setPersona] = useState(DEFAULT_GENERAL_PRESET.persona);
  const [roleSkills, setRoleSkills] = useState(DEFAULT_GENERAL_PRESET.skills);
  const [err, setErr] = useState("");
  const nameInputRef = useRef(null);

  useEffect(() => {
    api("/v1/roles")
      .then((d) => setRoles(d.items || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (nameInputRef.current) {
      nameInputRef.current.focus();
      nameInputRef.current.select();
    }
  }, []);

  const onPickRole = (id) => {
    setSelectedRoleId(id);
    if (!id) {
      setName("");
      setRole("");
      setPersona("");
      setRoleSkills([]);
      return;
    }
    if (id === DEFAULT_GENERAL_PRESET.id) {
      setName(DEFAULT_GENERAL_PRESET.name);
      setRole(DEFAULT_GENERAL_PRESET.role);
      setPersona(DEFAULT_GENERAL_PRESET.persona);
      setRoleSkills(DEFAULT_GENERAL_PRESET.skills);
      return;
    }
    const r = roles.find((x) => x.id === id);
    if (!r) return;
    if (!name.trim() || name === DEFAULT_GENERAL_PRESET.name || roles.some((x) => x.name === name)) {
      setName(r.name);
    }
    setRole(r.tag || r.name);
    setPersona(r.prompt || "");
    const sks = (r.skills || "").split(",").map((s) => s.trim()).filter(Boolean);
    setRoleSkills(sks);
  };

  const submit = async () => {
    if (!name.trim()) { setErr("名字不能为空"); return; }
    try {
      await onSubmit({
        name: name.trim(),
        role: role.trim() || null,
        persona: persona.trim() || null,
        role_id: selectedRoleId || null,
        skills: roleSkills,
      });
    } catch (e) {
      setErr(String(e.message || e));
    }
  };

  const PREFERRED_INDUSTRY_ORDER = ["文学与创作", "自媒体与创作", "个人智库", "研发", "金融", "市场", "设计", "法务", "运营", "通用"];
  const otherRoles = roles.filter((r) => r.id !== DEFAULT_GENERAL_PRESET.id);
  const rawIndustries = Array.from(new Set(otherRoles.map((r) => r.industry || "通用")));
  const industries = [
    ...PREFERRED_INDUSTRY_ORDER.filter((ind) => rawIndustries.includes(ind)),
    ...rawIndustries.filter((ind) => !PREFERRED_INDUSTRY_ORDER.includes(ind)),
  ];

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>新建 Bot</h3>
        <label>{roles.length ? `选择角色预设（从 ${roles.length} 个专业角色库选择）` : "选择角色预设（从专业角色库选择）"}</label>
        <select value={selectedRoleId} onChange={(e) => onPickRole(e.target.value)}>
          <option value="ROLE-GENERAL">✨ 通用助理 (推荐通用模板)</option>
          <option value="">-- 自定义空白角色 --</option>
          {industries.map((ind) => {
            const list = otherRoles.filter((r) => (r.industry || "通用") === ind);
            if (!list.length) return null;
            return (
              <optgroup key={ind} label={ind}>
                {list.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name} ({r.tag})
                  </option>
                ))}
              </optgroup>
            );
          })}
        </select>

        {roleSkills.length > 0 && (
          <div className="role-skills-preview">
            <span style={{ fontSize: "11px", color: "var(--sub)" }}>角色自带基础技能：</span>
            {roleSkills.map((sk) => (
              <span key={sk} className="skill-pill">{sk}</span>
            ))}
          </div>
        )}

        <label>名字</label>
        <input
          ref={nameInputRef}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onFocus={(e) => e.target.select()}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          placeholder="比如：小文 / 数据合规顾问"
          autoFocus
        />
        <label>职责（可选）</label>
        <input value={role} onChange={(e) => setRole(e.target.value)} placeholder="content / legal / dev" />
        <label>人格提示词（可选）</label>
        <textarea rows={4} value={persona} onChange={(e) => setPersona(e.target.value)}
          placeholder="你是……，说话风格……，遇到不确定时……" />
        {err && <div className="login-error">{err}</div>}
        <div className="modal-actions">
          <button onClick={onClose}>取消</button>
          <button className="primary" onClick={submit}>创建</button>
        </div>
      </div>
    </div>
  );
}

/* ============================== 弹层：创建群聊 ============================== */
function GroupModal({ bots, onSubmit, onClose }) {
  const [title, setTitle] = useState("");
  const [picked, setPicked] = useState([]);
  const [err, setErr] = useState("");
  const toggle = (id) => {
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };
  const submit = async () => {
    if (picked.length < 2) { setErr("至少选 2 个 Bot"); return; }
    try {
      await onSubmit({ title: title.trim() || "新群聊", botIds: picked.slice(0, 6) });
    } catch (e) {
      setErr(String(e.message || e));
    }
  };
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>创建群聊</h3>
        <label>群名</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="比如：双十一落地页" autoFocus />
        <label>成员（2–6 个）</label>
        <div className="pick-list">
          {bots.map((b) => (
            <button key={b.id} className={`pick-item ${picked.includes(b.id) ? "on" : ""}`}
              onClick={() => toggle(b.id)}>
              <span className="avatar">{b.name.slice(0, 1)}</span>
              {b.name}
            </button>
          ))}
        </div>
        {err && <div className="login-error">{err}</div>}
        <div className="modal-actions">
          <button onClick={onClose}>取消</button>
          <button className="primary" onClick={submit}>创建</button>
        </div>
      </div>
    </div>
  );
}

/* ============================== 弹层：连接应用（MCP 管理预留） ============================== */
function AppsModal({ onClose }) {
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="apps-modal" onClick={(e) => e.stopPropagation()}>
        <div className="apps-head">
          <h3>连接应用</h3>
          <span className="hint">MCP 管理 · 规划中</span>
        </div>
        <p className="hint">
          这里将用于管理外部平台的 MCP 连接（工具服务器、SaaS、数据源、团队应用），
          可以在这里查看连接状态、权限和可用工具。当前版本先保留入口，功能在后续构建中补齐。
        </p>
        <div className="modal-actions">
          <button className="primary" onClick={onClose}>知道了</button>
        </div>
      </div>
    </div>
  );
}

/* ============================== 弹层：设置 ============================== */
const PROVIDER_PRESETS = [
  {
    id: "deepseek",
    label: "DeepSeek 官方",
    baseUrl: "https://api.deepseek.com",
    defaultModel: "deepseek-chat",
    models: ["deepseek-chat", "deepseek-reasoner"],
  },
  {
    id: "siliconflow",
    label: "硅基流动 (SiliconFlow)",
    baseUrl: "https://api.siliconflow.cn/v1",
    defaultModel: "deepseek-ai/DeepSeek-V3",
    models: ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen2.5-72B-Instruct"],
  },
  {
    id: "moonshot",
    label: "Moonshot (月之暗面 / Kimi)",
    baseUrl: "https://api.moonshot.cn/v1",
    defaultModel: "moonshot-v1-8k",
    models: ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
  },
  {
    id: "zhipu",
    label: "智谱 AI (GLM)",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    defaultModel: "glm-4-plus",
    models: ["glm-4-plus", "glm-4-flash", "glm-4-long"],
  },
  {
    id: "dashscope",
    label: "阿里云百炼 (通义千问)",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    defaultModel: "qwen-plus",
    models: ["qwen-plus", "qwen-max", "qwen-turbo"],
  },
  {
    id: "openai",
    label: "OpenAI 官方",
    baseUrl: "https://api.openai.com/v1",
    defaultModel: "gpt-4o",
    models: ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
  },
  {
    id: "anthropic",
    label: "Anthropic (Claude)",
    baseUrl: "https://api.anthropic.com/v1",
    defaultModel: "claude-3-7-sonnet",
    models: ["claude-3-7-sonnet", "claude-3-5-sonnet"],
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    baseUrl: "https://openrouter.ai/api/v1",
    defaultModel: "deepseek/deepseek-r1",
    models: ["deepseek/deepseek-r1", "anthropic/claude-3.7-sonnet", "openai/gpt-4o"],
  },
  {
    id: "groq",
    label: "Groq",
    baseUrl: "https://api.groq.com/openai/v1",
    defaultModel: "llama-3.3-70b-versatile",
    models: ["llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b"],
  },
  {
    id: "minimax",
    label: "MiniMax (海螺)",
    baseUrl: "https://api.minimax.chat/v1",
    defaultModel: "abab6.5s-chat",
    models: ["abab6.5s-chat", "MiniMax-Text-01"],
  },
  {
    id: "ollama",
    label: "Ollama (本地)",
    baseUrl: "http://localhost:11434/v1",
    defaultModel: "llama3.2",
    models: ["llama3.2", "qwen2.5"],
  },
  {
    id: "ark",
    label: "火山引擎方舟 (豆包/DeepSeek)",
    baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
    defaultModel: "doubao-1-5-pro-32k-250115",
    models: ["doubao-1-5-pro-32k-250115", "deepseek-r1-250120"],
  },
  {
    id: "custom",
    label: "自定义 (OpenAI 兼容规范)",
    baseUrl: "",
    defaultModel: "",
    models: [],
  },
];

/* ==================== DSH 风格模型目录编辑组件 (图二复刻) ==================== */
function DshModelCatalogEditor({
  models = [],
  defaultModel = "",
  onChangeModels,
  onChangeDefaultModel,
  onRestoreDefaults,
  onFetchModels,
  fetchingModels = false,
  fetchStatus = null,
  onShowNotice = null,
}) {
  const [localNotice, setLocalNotice] = useState(null);

  const updateModel = (idx, patch) => {
    const next = [...models];
    next[idx] = { ...next[idx], ...patch };
    onChangeModels(next);
  };

  const isKnownModelId = (id) => {
    const raw = (id || "").trim().toLowerCase();
    if (!raw) return false;
    const knownExact = [
      "deepseek-chat", "deepseek-reasoner", "deepseek-flash", "deepseek-v4-pro",
      "gpt-4o", "gpt-4o-mini", "gpt-4.1", "o1", "o1-mini", "o1-preview", "o3", "o3-mini", "o4",
      "claude-3-7-sonnet", "claude-3-5-sonnet", "claude-3-opus",
      "qwen-plus", "qwen-max", "qwen-turbo", "qwen2.5",
      "glm-4-plus", "glm-4-flash", "glm-4-long",
      "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"
    ];
    if (knownExact.includes(raw)) return true;
    return ["deepseek-", "gpt-4", "claude-3", "o1-", "o3-", "glm-4"].some((p) => raw.startsWith(p));
  };

  const handleIdChange = (idx, newId) => {
    const m = models[idx];
    const oldId = m?.id || "";
    const isNameSynced = !m?.name || m?.name === oldId;
    const newName = isNameSynced ? newId : m.name;

    let extra = { _idDirty: true };
    if (isKnownModelId(newId) || (!m?.context_window || !m?.max_tokens)) {
      if (newId.trim()) {
        const inf = inferModelMetadata(newId);
        extra = {
          _idDirty: false,
          context_window: inf.context_window,
          max_tokens: inf.max_tokens,
          supports_reasoning: inf.supports_reasoning,
          reasoning_profile: inf.reasoning_profile,
          reasoning_levels: inf.reasoning_levels,
        };
      }
    }

    updateModel(idx, {
      id: newId,
      name: newName,
      ...extra,
    });

    if (!defaultModel || defaultModel === oldId) {
      onChangeDefaultModel(newId);
    }
  };

  const handleIdBlur = (idx) => {
    const m = models[idx];
    const id = (m?.id || "").trim();
    if (!id) return;
    if (m._idDirty || !m.context_window || !m.max_tokens) {
      const inf = inferModelMetadata(id);
      updateModel(idx, {
        _idDirty: false,
        context_window: inf.context_window,
        max_tokens: inf.max_tokens,
        supports_reasoning: inf.supports_reasoning,
        reasoning_profile: inf.reasoning_profile,
        reasoning_levels: inf.reasoning_levels,
      });
    }
  };

  const handleInferModel = (idx) => {
    const m = models[idx];
    if (!m?.id || !m.id.trim()) return;
    const inf = inferModelMetadata(m.id);
    updateModel(idx, {
      name: m.name || inf.name,
      context_window: inf.context_window,
      max_tokens: inf.max_tokens,
      supports_reasoning: inf.supports_reasoning,
      reasoning_profile: inf.reasoning_profile,
      reasoning_levels: inf.reasoning_levels,
    });
  };

  const handleAutoInferAll = () => {
    if (!models || models.length === 0) return;
    const next = models.map((m) => {
      const id = (m?.id || "").trim();
      if (!id) return m;
      const inf = inferModelMetadata(id);
      return {
        ...m,
        context_window: inf.context_window,
        max_tokens: inf.max_tokens,
        supports_reasoning: inf.supports_reasoning,
        reasoning_profile: inf.reasoning_profile,
        reasoning_levels: inf.reasoning_levels,
      };
    });
    onChangeModels(next);
    const msg = `✓ 已一键自适应当前 ${next.length} 个模型的上下文与推理选项`;
    setLocalNotice(msg);
    setTimeout(() => setLocalNotice(null), 4000);
    if (typeof onShowNotice === "function") {
      onShowNotice("success", msg);
    }
  };

  const removeModel = (idx) => {
    const targetId = models[idx]?.id;
    const next = models.filter((_, i) => i !== idx);
    onChangeModels(next);
    if (defaultModel === targetId) {
      onChangeDefaultModel(next[0]?.id || "");
    }
  };

  const handleToggleReasoning = (idx, checked) => {
    if (checked) {
      const curProfile = models[idx]?.reasoning_profile || "deepseek";
      const levels = REASONING_PROFILES[curProfile] || REASONING_PROFILES.deepseek;
      updateModel(idx, {
        supports_reasoning: true,
        reasoning_profile: curProfile,
        reasoning_levels: levels,
      });
    } else {
      updateModel(idx, {
        supports_reasoning: false,
        reasoning_levels: [],
      });
    }
  };

  const handleProfileChange = (idx, profile) => {
    const levels = REASONING_PROFILES[profile] || REASONING_PROFILES.deepseek;
    updateModel(idx, {
      reasoning_profile: profile,
      reasoning_levels: levels,
    });
  };

  const handleAddEmptyCard = () => {
    const newCard = {
      id: "",
      name: "",
      context_window: "128K",
      max_tokens: "8K",
      supports_reasoning: false,
      reasoning_profile: "deepseek",
      reasoning_levels: [],
    };
    onChangeModels([...models, newCard]);
  };

  return (
    <div className="dsh-model-catalog">
      {/* 顶部保留操作栏 */}
      <div className="dsh-catalog-top-bar">
        <div className="dsh-catalog-title-group">
          <div className="dsh-catalog-title-row">
            <span className="dsh-catalog-title">模型目录</span>
            <span className="dsh-catalog-count-badge">已配置 {models.length} 个模型</span>
          </div>
          <span className="dsh-catalog-subtitle">已自定义模型目录</span>
        </div>
        <div className="dsh-catalog-actions">
          <button
            type="button"
            className="dsh-auto-infer-btn"
            onClick={handleAutoInferAll}
            title="根据模型 ID 智能自适应推断所有模型的上下文窗口、最大输出与推理档位"
          >
            🪄 一键自适应规格
          </button>
          <button
            type="button"
            className="dsh-restore-btn"
            onClick={onRestoreDefaults}
            title="恢复当前提供商内置默认模型配置"
          >
            恢复默认模型
          </button>
          <button
            type="button"
            className="dsh-fetch-btn"
            disabled={fetchingModels}
            onClick={onFetchModels}
            title="向远端接口探测并拉取可用模型"
          >
            {fetchingModels && <span className="dsh-spinner" />}
            <span>{fetchingModels ? "正在探测远端模型…" : "⚡ 获取可用模型"}</span>
          </button>
        </div>
      </div>

      {(localNotice || fetchStatus) && (
        <div className={`dsh-fetch-status ${localNotice ? "ok" : (fetchStatus.ok ? "ok" : "err")}`}>
          {localNotice || fetchStatus.msg}
        </div>
      )}

      {/* 模型编辑卡片列表 */}
      <div className="dsh-model-cards-list">
        {models.length === 0 ? (
          <div className="dsh-catalog-empty">暂无模型，请点击上方“获取可用模型”或下方“＋ 添加模型”</div>
        ) : (
          models.map((m, idx) => {
            const isDefault = !!(m.id && m.id === defaultModel);
            const curProfile = m.reasoning_profile || getProfileFromLevels(m.reasoning_levels);
            return (
              <div key={idx} className={`dsh-model-card ${isDefault ? "is-default" : ""}`}>
                {/* 第一行：模型调用 ID、显示名称、设为默认、智能推断与删除 */}
                <div className="dsh-card-row-top">
                  <input
                    type="text"
                    className="dsh-card-input model-id"
                    value={m.id || ""}
                    placeholder="模型 ID (如 deepseek-chat)"
                    onChange={(e) => handleIdChange(idx, e.target.value)}
                    onBlur={() => handleIdBlur(idx)}
                  />
                  <input
                    type="text"
                    className="dsh-card-input model-name"
                    value={m.name || ""}
                    placeholder="显示名称"
                    onChange={(e) => updateModel(idx, { name: e.target.value })}
                  />
                  {isDefault ? (
                    <button
                      type="button"
                      className="dsh-card-def-badge active"
                      disabled
                      title="当前提供商默认模型"
                    >
                      ✓ 默认
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="dsh-card-def-badge"
                      onClick={() => onChangeDefaultModel(m.id)}
                      title="设为此提供商默认模型"
                    >
                      设为默认
                    </button>
                  )}
                  <button
                    type="button"
                    className="dsh-card-icon-btn infer-btn"
                    onClick={() => handleInferModel(idx)}
                    title="自适应此模型上下文与推理配置"
                  >
                    🪄
                  </button>
                  <button
                    type="button"
                    className="dsh-card-icon-btn delete-btn"
                    onClick={() => removeModel(idx)}
                    title="从列表中移除此模型"
                  >
                    🗑️
                  </button>
                </div>

                {/* 第二行：上下文窗口 与 最大输出 token 数 */}
                <div className="dsh-card-row-mid">
                  <div className="dsh-card-field">
                    <label className="dsh-card-label">上下文窗口</label>
                    <input
                      type="text"
                      className="dsh-card-input"
                      value={m.context_window || ""}
                      placeholder="如 128K, 256K"
                      onChange={(e) => updateModel(idx, { context_window: e.target.value })}
                    />
                    <div className="dsh-quick-pills">
                      {["64K", "128K", "256K", "1M"].map((cw) => {
                        const active = (m.context_window || "").toUpperCase() === cw;
                        return (
                          <button
                            key={cw}
                            type="button"
                            className={`dsh-quick-pill ${active ? "active" : ""}`}
                            onClick={() => updateModel(idx, { context_window: cw })}
                          >
                            {cw}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                  <div className="dsh-card-field">
                    <label className="dsh-card-label">最大输出 token 数</label>
                    <input
                      type="text"
                      className="dsh-card-input"
                      value={m.max_tokens || ""}
                      placeholder="如 8K, 32K"
                      onChange={(e) => updateModel(idx, { max_tokens: e.target.value })}
                    />
                    <div className="dsh-quick-pills">
                      {["4K", "8K", "16K", "32K", "64K"].map((mt) => {
                        const active = (m.max_tokens || "").toUpperCase() === mt;
                        return (
                          <button
                            key={mt}
                            type="button"
                            className={`dsh-quick-pill ${active ? "active" : ""}`}
                            onClick={() => updateModel(idx, { max_tokens: mt })}
                          >
                            {mt}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>

                {/* 第三行：推理/思考能力 勾选框与档位选择器 */}
                <div className="dsh-card-row-bottom">
                  <label className="dsh-card-check-label">
                    <input
                      type="checkbox"
                      checked={!!m.supports_reasoning}
                      onChange={(e) => handleToggleReasoning(idx, e.target.checked)}
                    />
                    <span>支持深度思考 / 推理模型</span>
                  </label>
                  {m.supports_reasoning && (
                    <select
                      className="dsh-card-select reasoning-select"
                      value={curProfile}
                      onChange={(e) => handleProfileChange(idx, e.target.value)}
                    >
                      <option value="deepseek">DeepSeek / 通用推理 (none, standard, max)</option>
                      <option value="openai">OpenAI 思考档位 (low, medium, high)</option>
                      <option value="claude">Claude 扩展思考 (budget)</option>
                    </select>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* 模型列表底部：＋ 添加模型 */}
      <button
        type="button"
        className="dsh-add-model-card-btn"
        onClick={handleAddEmptyCard}
        title="添加一个新模型并自定义元数据"
      >
        ＋ 添加模型
      </button>
    </div>
  );
}

/* ==================== DSH 风格模型提供商与 API 配置 ==================== */
function DshModelsPane({ onReloadAll, models }) {
  const [providers, setProviders] = useState([]);
  const [activeProviderId, setActiveProviderId] = useState("");
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [formTab, setFormTab] = useState("preset");

  // Tab 1: 第三方模型提供商状态
  const [presetId, setPresetId] = useState("deepseek");
  const [presetName, setPresetName] = useState("DeepSeek 官方");
  const [presetBaseUrl, setPresetBaseUrl] = useState("https://api.deepseek.com");
  const [presetApiKey, setPresetApiKey] = useState("");
  const [presetHasKey, setPresetHasKey] = useState(false);
  const [presetKeyMasked, setPresetKeyMasked] = useState("");
  const [showPresetKey, setShowPresetKey] = useState(false);
  const [presetCustomOpen, setPresetCustomOpen] = useState(true);
  const [presetModels, setPresetModels] = useState(() =>
    PROVIDER_PRESETS[0].models.map(inferModelMetadata)
  );
  const [presetDefaultModel, setPresetDefaultModel] = useState("deepseek-chat");

  // Tab 2: 自定义模型 API 状态
  const [customProviderId, setCustomProviderId] = useState("");
  const [customName, setCustomName] = useState("");
  const [customBaseUrl, setCustomBaseUrl] = useState("");
  const [customProtocol, setCustomProtocol] = useState("openai");
  const [customApiKey, setCustomApiKey] = useState("");
  const [customHasKey, setCustomHasKey] = useState(false);
  const [customKeyMasked, setCustomKeyMasked] = useState("");
  const [showCustomKey, setShowCustomKey] = useState(false);
  const [customModels, setCustomModels] = useState([]);
  const [customDefaultModel, setCustomDefaultModel] = useState("");

  // 通用状态
  const [makeDefault, setMakeDefault] = useState(false);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [fetchStatus, setFetchStatus] = useState(null);
  const [saving, setSaving] = useState(false);
  const formRef = useRef(null);
  const initialLoadedRef = useRef(false);

  const showNotice = (type, text) => {
    setNotice({ type, text });
    setTimeout(() => setNotice(null), 3500);
  };

  const loadProviders = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api("/v1/settings/providers");
      if (res && res.providers) {
        setProviders(res.providers);
        setActiveProviderId(res.active_provider_id || "");

        // Initial mount auto-load: if default preset exists in configured providers, load its settings
        if (!initialLoadedRef.current) {
          initialLoadedRef.current = true;
          const existing = res.providers.find(
            (p) => p.preset_id === "deepseek" || p.kind === "deepseek"
          );
          if (existing) {
            setEditingId(existing.id);
            setPresetName(existing.name || "DeepSeek 官方");
            setPresetBaseUrl(existing.base_url || "https://api.deepseek.com");
            setPresetApiKey("");
            setPresetHasKey(!!existing.has_key);
            setPresetKeyMasked(existing.api_key_masked || existing.api_key || "");
            const presetDef = PROVIDER_PRESETS.find((x) => x.id === "deepseek") || PROVIDER_PRESETS[0];
            const pModels = (existing.models && existing.models.length > 0)
              ? normalizeModelList(existing.models)
              : (presetDef.models || []).map(inferModelMetadata);
            setPresetModels(pModels);
            setPresetDefaultModel(existing.model || (pModels[0] && pModels[0].id) || "deepseek-chat");
            setMakeDefault(existing.id === res.active_provider_id || !!existing.is_active);
          }
        }
      }
    } catch (err) {
      console.error("loadProviders error:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProviders();
  }, [loadProviders]);

  const handleActivate = async (id) => {
    try {
      const res = await api(`/v1/settings/providers/${id}/activate`, { method: "POST" });
      if (res && res.ok) {
        setActiveProviderId(res.active_provider_id || id);
        await loadProviders();
        if (onReloadAll) await onReloadAll();
        showNotice("success", "✓ 已切换当前默认模型服务提供商");
      }
    } catch (err) {
      showNotice("error", `激活失败: ${err.message || err}`);
    }
  };

  const handleDelete = async (id, name) => {
    if (!window.confirm(`确定要删除提供商 "${name || id}" 吗？`)) return;
    try {
      const res = await api(`/v1/settings/providers/${id}`, { method: "DELETE" });
      if (res && res.ok) {
        if (editingId === id) {
          resetForm();
        }
        await loadProviders();
        if (onReloadAll) await onReloadAll();
        showNotice("success", "✓ 已成功删除提供商");
      }
    } catch (err) {
      showNotice("error", `删除失败: ${err.message || err}`);
    }
  };

  const handleEdit = (p) => {
    setEditingId(p.id);
    setFetchStatus(null);
    const isCustom = p.kind === "custom" || p.preset_id === "custom" || !PROVIDER_PRESETS.some((x) => x.id === (p.preset_id || p.kind));
    if (isCustom) {
      setFormTab("custom");
      setCustomProviderId(p.id);
      setCustomName(p.name || "");
      setCustomBaseUrl(p.base_url || "");
      setCustomProtocol("openai");
      setCustomApiKey("");
      setCustomHasKey(!!p.has_key);
      setCustomKeyMasked(p.api_key_masked || p.api_key || "");
      const pModels = normalizeModelList(p.models);
      setCustomModels(pModels);
      setCustomDefaultModel(p.model || (pModels[0] && pModels[0].id) || "");
    } else {
      setFormTab("preset");
      const pid = p.preset_id || p.kind || "deepseek";
      setPresetId(pid);
      setPresetName(p.name || "");
      setPresetBaseUrl(p.base_url || "");
      setPresetApiKey("");
      setPresetHasKey(!!p.has_key);
      setPresetKeyMasked(p.api_key_masked || p.api_key || "");
      const presetDef = PROVIDER_PRESETS.find((x) => x.id === pid) || PROVIDER_PRESETS[0];
      const pModels = (p.models && p.models.length > 0)
        ? normalizeModelList(p.models)
        : presetDef.models.map(inferModelMetadata);
      setPresetModels(pModels);
      setPresetDefaultModel(p.model || (pModels[0] && pModels[0].id) || presetDef.defaultModel || "");
      setPresetCustomOpen(true);
    }
    setMakeDefault(p.id === activeProviderId || !!p.is_active);
    setTimeout(() => {
      formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 100);
  };

  const handleResetCustomForm = () => {
    setEditingId(null);
    setCustomProviderId("");
    setCustomName("");
    setCustomBaseUrl("");
    setCustomProtocol("openai");
    setCustomApiKey("");
    setCustomHasKey(false);
    setCustomKeyMasked("");
    setCustomModels([]);
    setCustomDefaultModel("");
    setMakeDefault(false);
    setFetchStatus(null);
  };

  const resetForm = () => {
    setEditingId(null);
    setFetchStatus(null);
    setMakeDefault(false);
    if (formTab === "preset") {
      const def = PROVIDER_PRESETS.find((x) => x.id === presetId) || PROVIDER_PRESETS[0];
      setPresetName(def.label);
      setPresetBaseUrl(def.baseUrl);
      setPresetApiKey("");
      setPresetHasKey(false);
      setPresetKeyMasked("");
      setPresetModels(def.models.map(inferModelMetadata));
      setPresetDefaultModel(def.defaultModel);
    } else {
      handleResetCustomForm();
    }
  };

  const handlePresetChange = (pid) => {
    const preset = PROVIDER_PRESETS.find((x) => x.id === pid) || PROVIDER_PRESETS[0];
    setPresetId(pid);
    setFetchStatus(null);

    // Check if providers already has a configured provider for pid
    const existing = providers.find((p) => p.preset_id === pid || p.kind === pid);
    if (existing) {
      setEditingId(existing.id);
      setPresetName(existing.name || preset.label);
      setPresetBaseUrl(existing.base_url || preset.baseUrl || "");
      setPresetApiKey("");
      setPresetHasKey(!!existing.has_key);
      setPresetKeyMasked(existing.api_key_masked || existing.api_key || "");
      const pModels = (existing.models && existing.models.length > 0)
        ? normalizeModelList(existing.models)
        : (preset.models || []).map(inferModelMetadata);
      setPresetModels(pModels);
      setPresetDefaultModel(existing.model || (pModels[0] && pModels[0].id) || preset.defaultModel || "");
      setMakeDefault(existing.id === activeProviderId || !!existing.is_active);
    } else {
      setEditingId(null);
      setPresetName(preset.label);
      setPresetBaseUrl(preset.baseUrl || "");
      setPresetApiKey("");
      setPresetHasKey(false);
      setPresetKeyMasked("");
      const defModels = (preset.models || []).map(inferModelMetadata);
      setPresetModels(defModels);
      setPresetDefaultModel(preset.defaultModel || (defModels[0] && defModels[0].id) || "");
      setMakeDefault(false);
    }
  };

  const handleRestorePresetDefaults = () => {
    const preset = PROVIDER_PRESETS.find((x) => x.id === presetId) || PROVIDER_PRESETS[0];
    const defModels = preset.models.map(inferModelMetadata);
    setPresetModels(defModels);
    setPresetDefaultModel(preset.defaultModel || (defModels[0] && defModels[0].id) || "");
    showNotice("success", `✓ 已恢复 ${preset.label} 默认模型配置`);
  };

  const handleRestoreCustomDefaults = () => {
    const defModels = [
      inferModelMetadata("gpt-4o"),
      inferModelMetadata("deepseek-chat"),
    ];
    setCustomModels(defModels);
    setCustomDefaultModel("gpt-4o");
    showNotice("success", "✓ 已恢复自定义基准默认模型");
  };

  const fetchRemoteModels = async (baseUrl, apiKey, providerKind) => {
    if (!baseUrl || !baseUrl.trim()) {
      showNotice("error", "请先填写 API 地址");
      return;
    }
    setFetchingModels(true);
    setFetchStatus(null);
    try {
      const res = await api("/v1/settings/fetch-models", {
        method: "POST",
        body: {
          base_url: baseUrl.trim(),
          api_key: apiKey ? apiKey.trim() : undefined,
          provider: providerKind || "openai",
        },
      });
      if (res && res.ok && (Array.isArray(res.items) || Array.isArray(res.models))) {
        const fetchedRaw = (Array.isArray(res.items) && res.items.length > 0)
          ? res.items
          : (res.models || []);
        const fetched = normalizeModelList(fetchedRaw);

        if (fetched.length === 0) {
          setFetchStatus({
            ok: true,
            msg: "远端未返回可用模型列表，请手动输入添加",
          });
        } else {
          const successMsg = `✓ 成功获取 ${fetched.length} 个远端可用模型，已自适应参数并同步至列表 (${res.latency_ms || 0}ms)`;
          setFetchStatus({
            ok: true,
            msg: successMsg,
          });
          showNotice("success", successMsg);

          if (formTab === "preset") {
            setPresetModels(fetched);
            const exists = fetched.some((m) => m.id === presetDefaultModel);
            setPresetDefaultModel(exists ? presetDefaultModel : (fetched[0]?.id || ""));
          } else {
            setCustomModels(fetched);
            const exists = fetched.some((m) => m.id === customDefaultModel);
            setCustomDefaultModel(exists ? customDefaultModel : (fetched[0]?.id || ""));
          }
        }
      } else {
        setFetchStatus({
          ok: false,
          msg: `获取失败: ${res?.error || "未能获取远端模型列表"}`,
        });
      }
    } catch (err) {
      setFetchStatus({
        ok: false,
        msg: `请求异常: ${err.message || err}`,
      });
    } finally {
      setFetchingModels(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const cleanModels = (list) => {
        return (list || [])
          .filter((m) => m && (m.id || "").trim())
          .map((m) => {
            const id = m.id.trim();
            const name = (m.name || id).trim();
            const cw = (m.context_window || "128K").trim();
            const mt = (m.max_tokens || "8K").trim();
            const sr = !!m.supports_reasoning;
            const rl = sr
              ? (m.reasoning_levels && m.reasoning_levels.length > 0
                  ? m.reasoning_levels
                  : (REASONING_PROFILES[m.reasoning_profile || "deepseek"] || ["none", "standard", "max"]))
              : [];
            return {
              id,
              name,
              context_window: cw,
              max_tokens: mt,
              supports_reasoning: sr,
              reasoning_levels: rl,
            };
          });
      };

      let payload = {};
      if (formTab === "preset") {
        const preset = PROVIDER_PRESETS.find((x) => x.id === presetId) || PROVIDER_PRESETS[0];
        const bUrl = (presetBaseUrl || preset.baseUrl || "").trim();
        if (!bUrl) {
          showNotice("error", "请填写 API 地址");
          setSaving(false);
          return;
        }
        const mList = cleanModels(presetModels);
        if (mList.length === 0) {
          showNotice("error", "模型目录不能为空，请至少添加一个模型");
          setSaving(false);
          return;
        }
        let targetId = editingId;
        if (!targetId) {
          const existing = providers.find(
            (p) => p.preset_id === presetId || p.kind === presetId
          );
          if (existing) {
            targetId = existing.id;
          } else {
            targetId = presetId;
          }
        }
        payload = {
          id: targetId,
          name: (presetName || preset.label).trim(),
          kind: presetId,
          preset_id: presetId,
          base_url: bUrl,
          models: mList,
          model: presetDefaultModel || mList[0].id || "deepseek-chat",
          is_active: makeDefault || undefined,
        };
        if (presetApiKey && presetApiKey.trim()) {
          payload.api_key = presetApiKey.trim();
        }
      } else {
        const cId = (customProviderId || "").trim();
        if (!cId) {
          showNotice("error", "请填写 Provider ID (如 vertex-relay)");
          setSaving(false);
          return;
        }
        const bUrl = (customBaseUrl || "").trim();
        if (!bUrl) {
          showNotice("error", "请填写 API 地址");
          setSaving(false);
          return;
        }
        const mList = cleanModels(customModels);
        if (mList.length === 0) {
          showNotice("error", "模型目录不能为空，请至少添加一个模型");
          setSaving(false);
          return;
        }
        payload = {
          id: editingId || cId,
          name: (customName || cId).trim(),
          kind: "custom",
          preset_id: "custom",
          base_url: bUrl,
          models: mList,
          model: customDefaultModel || mList[0].id || "",
          is_active: makeDefault || undefined,
        };
        if (customApiKey && customApiKey.trim()) {
          payload.api_key = customApiKey.trim();
        }
      }

      const res = await api("/v1/settings/providers", {
        method: "POST",
        body: payload,
      });

      if (res && res.ok) {
        showNotice("success", `✓ 提供商 "${payload.name}" 保存成功`);

        const savedProvider = res.provider || {};
        const savedId = savedProvider.id || payload.id;
        setEditingId(savedId);

        if (formTab === "preset") {
          const hasKey = savedProvider.has_key !== undefined
            ? !!savedProvider.has_key
            : (!!payload.api_key || presetHasKey);
          setPresetHasKey(hasKey);
          if (savedProvider.api_key_masked) {
            setPresetKeyMasked(savedProvider.api_key_masked);
          } else if (payload.api_key) {
            const rawK = payload.api_key;
            setPresetKeyMasked(
              rawK.length > 8 ? `${rawK.slice(0, 3)}****${rawK.slice(-4)}` : "********"
            );
          }
          setPresetApiKey("");
        } else {
          const hasKey = savedProvider.has_key !== undefined
            ? !!savedProvider.has_key
            : (!!payload.api_key || customHasKey);
          setCustomHasKey(hasKey);
          if (savedProvider.api_key_masked) {
            setCustomKeyMasked(savedProvider.api_key_masked);
          } else if (payload.api_key) {
            const rawK = payload.api_key;
            setCustomKeyMasked(
              rawK.length > 8 ? `${rawK.slice(0, 3)}****${rawK.slice(-4)}` : "********"
            );
          }
          setCustomApiKey("");
        }

        await loadProviders();
        if (onReloadAll) await onReloadAll();
      } else {
        showNotice("error", `保存失败: ${res?.error || "未知错误"}`);
      }
    } catch (err) {
      showNotice("error", `保存失败: ${err.message || err}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="dsh-models-container">
      {/* 顶部：已配置模型提供商列表 */}
      <div className="dsh-section-header">
        <div className="dsh-section-title-row">
          <h3>已配置模型提供商</h3>
          <span className="dsh-count-badge">{providers.length} 个服务商</span>
        </div>
        <p className="hint">已在系统中注册的大模型服务与自定义中转站，默认服务商将作为所有助手的底座基准。</p>
      </div>

      {notice && (
        <div className={`dsh-notice ${notice.type}`}>
          {notice.text}
        </div>
      )}

      <div className="dsh-providers-list">
        {loading && providers.length === 0 ? (
          <div className="dsh-empty-card">正在加载模型提供商列表…</div>
        ) : providers.length === 0 ? (
          <div className="dsh-empty-card">暂无已配置的模型提供商，请在下方添加并配置。</div>
        ) : (
          providers.map((p) => {
            const isDefault = p.id === activeProviderId || !!p.is_active;
            const isCustom = p.kind === "custom" || p.preset_id === "custom";
            return (
              <div key={p.id} className={`dsh-provider-card ${isDefault ? "active" : ""}`}>
                <div className="dsh-card-main">
                  <div className="dsh-card-top-row">
                    <span
                      className={`dsh-status-dot ${p.has_key || p.base_url ? "online" : "offline"}`}
                      title={p.has_key ? "已配置密钥与接口地址" : "未配置密钥"}
                    />
                    <span className="dsh-provider-name">{p.name || p.id}</span>
                    {isDefault && <span className="dsh-badge active">✓ 默认</span>}
                    {isCustom ? (
                      <span className="dsh-badge custom">自定义</span>
                    ) : (
                      <span className="dsh-badge preset">{p.preset_id || p.kind}</span>
                    )}
                  </div>
                  <div className="dsh-card-meta-row">
                    <span className="dsh-meta-url" title={p.base_url}>{p.base_url || "未设置 Base URL"}</span>
                    <span className="dsh-meta-divider">·</span>
                    <span className="dsh-meta-models">{(p.models || []).length} 个模型</span>
                    {p.model && (
                      <>
                        <span className="dsh-meta-divider">·</span>
                        <span className="dsh-meta-default-model">默认: {p.model}</span>
                      </>
                    )}
                  </div>
                </div>
                <div className="dsh-card-actions">
                  {!isDefault && (
                    <button
                      type="button"
                      className="dsh-btn-sm default-btn"
                      onClick={() => handleActivate(p.id)}
                      title="设为全局默认提供商"
                    >
                      设为默认
                    </button>
                  )}
                  <button
                    type="button"
                    className="dsh-btn-sm edit-btn"
                    onClick={() => handleEdit(p)}
                    title="在下方编辑此提供商配置"
                  >
                    编辑
                  </button>
                  <button
                    type="button"
                    className="dsh-btn-sm delete-btn"
                    onClick={() => handleDelete(p.id, p.name)}
                    title="删除此提供商"
                  >
                    删除
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      <div className="settings-divider" style={{ margin: "14px 0 10px" }} />

      {/* 下部：提供商配置表单（复刻用户截图 DSH 双 Tab 结构） */}
      <div className="dsh-form-wrapper" ref={formRef}>
        <div className="dsh-form-header">
          <h4>{editingId ? `编辑模型提供商: ${editingId}` : "添加模型服务商与 API"}</h4>
          {editingId && (
            <span className="dsh-editing-badge">正在编辑模式</span>
          )}
        </div>

        {/* 双 Tab 切换按钮 */}
        <div className="dsh-form-tabs">
          <button
            type="button"
            className={`dsh-form-tab-btn ${formTab === "preset" ? "active" : ""}`}
            onClick={() => {
              setFormTab("preset");
              setFetchStatus(null);
              const curr = providers.find((p) => p.id === editingId);
              if (curr && (curr.kind === "custom" || curr.preset_id === "custom")) {
                handlePresetChange(presetId);
              }
            }}
          >
            第三方模型提供商
          </button>
          <button
            type="button"
            className={`dsh-form-tab-btn ${formTab === "custom" ? "active" : ""}`}
            onClick={() => {
              setFormTab("custom");
              setFetchStatus(null);
              const curr = providers.find((p) => p.id === editingId);
              if (curr && (curr.kind !== "custom" && curr.preset_id !== "custom")) {
                setEditingId(null);
              }
            }}
          >
            自定义模型 API
          </button>
        </div>

        {/* 表单主体 */}
        <div className="dsh-form-card">
          {formTab === "preset" ? (
            <>
              <p className="dsh-form-desc">从内置目录中选择 OpenAI、Anthropic、Kimi 等提供商，填入其 API 密钥即可使用。</p>
              
              <div className="dsh-form-group">
                <label className="dsh-label">提供商</label>
                <select
                  className="dsh-select"
                  value={presetId}
                  onChange={(e) => handlePresetChange(e.target.value)}
                >
                  {PROVIDER_PRESETS.filter((p) => p.id !== "custom").map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="dsh-form-group">
                <label className="dsh-label">API 密钥</label>
                <div className="dsh-input-row">
                  <input
                    type={showPresetKey ? "text" : "password"}
                    className="dsh-input"
                    value={presetApiKey}
                    placeholder={
                      presetHasKey
                        ? `已配置 (${presetKeyMasked || "***"}) · 留空保持不变`
                        : "请输入 API 密钥 (sk-...)"
                    }
                    onChange={(e) => setPresetApiKey(e.target.value)}
                  />
                  <button
                    type="button"
                    className="dsh-btn-sm toggle-key-btn"
                    onClick={() => setShowPresetKey(!showPresetKey)}
                  >
                    {showPresetKey ? "隐藏" : "显示"}
                  </button>
                </div>
              </div>

              {/* 可折叠项 > 自定义设置 */}
              <div className="dsh-collapsible">
                <div
                  className="dsh-collapsible-header"
                  onClick={() => setPresetCustomOpen(!presetCustomOpen)}
                >
                  <span className="dsh-collapsible-title">
                    <span className="dsh-toggle-arrow">{presetCustomOpen ? "▾" : "▸"}</span> 自定义设置
                  </span>
                  <span className="dsh-collapsible-hint">{presetCustomOpen ? "收起" : "展开高级选项"}</span>
                </div>

                {presetCustomOpen && (
                  <div className="dsh-collapsible-body">
                    <div className="dsh-form-group">
                      <label className="dsh-label">API 地址 (Base URL)</label>
                      <input
                        type="text"
                        className="dsh-input"
                        value={presetBaseUrl}
                        placeholder="https://api.deepseek.com"
                        onChange={(e) => setPresetBaseUrl(e.target.value)}
                      />
                      <span className="dsh-subtext">自动智能兼容 /v1 与 /chat/completions 后缀；支持中转代理</span>
                    </div>

                    <DshModelCatalogEditor
                      models={presetModels}
                      defaultModel={presetDefaultModel}
                      onChangeModels={setPresetModels}
                      onChangeDefaultModel={setPresetDefaultModel}
                      onRestoreDefaults={handleRestorePresetDefaults}
                      onFetchModels={() => fetchRemoteModels(presetBaseUrl, presetApiKey, presetId)}
                      fetchingModels={fetchingModels}
                      fetchStatus={fetchStatus}
                      onShowNotice={showNotice}
                    />
                  </div>
                )}
              </div>
            </>
          ) : (
            <>
              <p className="dsh-form-desc">连接中转站、自部署服务或其他兼容 OpenAI / Anthropic 协议的接口，需填写 API 地址、协议和模型。</p>

              {editingId && (
                <div style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "8px 12px",
                  marginBottom: "12px",
                  background: "rgba(99, 102, 241, 0.08)",
                  border: "1px dashed rgba(99, 102, 241, 0.4)",
                  borderRadius: "6px"
                }}>
                  <span style={{ fontSize: "12px", color: "var(--text-sub, #94a3b8)" }}>
                    正在编辑自定义提供商: <strong style={{ color: "var(--text-main, #e2e8f0)" }}>{customName || customProviderId || editingId}</strong>
                  </span>
                  <button
                    type="button"
                    className="dsh-btn-sm"
                    style={{
                      background: "#6366f1",
                      color: "#ffffff",
                      border: "none",
                      padding: "4px 10px",
                      borderRadius: "4px",
                      fontSize: "12px",
                      cursor: "pointer",
                      fontWeight: 500
                    }}
                    onClick={handleResetCustomForm}
                    title="清空当前自定义表单以添加新的提供商"
                  >
                    清空表单 / ＋ 添加新的自定义提供商
                  </button>
                </div>
              )}

              <div className="dsh-form-group">
                <label className="dsh-label">Provider ID (唯一标识)</label>
                <input
                  type="text"
                  className="dsh-input"
                  value={customProviderId}
                  placeholder="例如: vertex-relay, amd, scnet, vllm"
                  disabled={!!editingId}
                  onChange={(e) => setCustomProviderId(e.target.value)}
                />
                <span className="dsh-subtext">英文字母、数字和横杠，用于系统唯一标识</span>
              </div>

              <div className="dsh-form-group">
                <label className="dsh-label">显示名称</label>
                <input
                  type="text"
                  className="dsh-input"
                  value={customName}
                  placeholder="例如: Vertex 中转站, 本地集群"
                  onChange={(e) => setCustomName(e.target.value)}
                />
              </div>

              <div className="dsh-form-group">
                <label className="dsh-label">API 地址 (Base URL)</label>
                <input
                  type="text"
                  className="dsh-input"
                  value={customBaseUrl}
                  placeholder="https://gateway.example.com/v1"
                  onChange={(e) => setCustomBaseUrl(e.target.value)}
                />
                <span className="dsh-subtext">中转站或自部署服务的 API 根路径</span>
              </div>

              <div className="dsh-form-group">
                <label className="dsh-label">API 协议</label>
                <select
                  className="dsh-select"
                  value={customProtocol}
                  onChange={(e) => setCustomProtocol(e.target.value)}
                >
                  <option value="openai">OpenAI Chat Completions (标准兼容)</option>
                  <option value="anthropic">Anthropic Messages</option>
                </select>
              </div>

              <div className="dsh-form-group">
                <label className="dsh-label">API 密钥 (API Key)</label>
                <div className="dsh-input-row">
                  <input
                    type={showCustomKey ? "text" : "password"}
                    className="dsh-input"
                    value={customApiKey}
                    placeholder={
                      customHasKey
                        ? `已配置 (${customKeyMasked || "***"}) · 留空保持不变`
                        : "输入 API 密钥 (无需鉴权可留空)"
                    }
                    onChange={(e) => setCustomApiKey(e.target.value)}
                  />
                  <button
                    type="button"
                    className="dsh-btn-sm toggle-key-btn"
                    onClick={() => setShowCustomKey(!showCustomKey)}
                  >
                    {showCustomKey ? "隐藏" : "显示"}
                  </button>
                </div>
              </div>

              <DshModelCatalogEditor
                models={customModels}
                defaultModel={customDefaultModel}
                onChangeModels={setCustomModels}
                onChangeDefaultModel={setCustomDefaultModel}
                onRestoreDefaults={handleRestoreCustomDefaults}
                onFetchModels={() => fetchRemoteModels(customBaseUrl, customApiKey, customProtocol)}
                fetchingModels={fetchingModels}
                fetchStatus={fetchStatus}
                onShowNotice={showNotice}
              />
            </>
          )}

          <label className="checkline" style={{ marginTop: "4px" }}>
            <input
              type="checkbox"
              checked={makeDefault}
              onChange={(e) => setMakeDefault(e.target.checked)}
            />
            设为当前全局默认提供商
          </label>

          <div className="dsh-form-actions">
            {formTab === "custom" && editingId && (
              <button
                type="button"
                className="dsh-btn"
                style={{
                  marginRight: "auto",
                  borderColor: "rgba(99, 102, 241, 0.6)",
                  color: "#818cf8"
                }}
                onClick={handleResetCustomForm}
              >
                清空表单 / ＋ 添加新的自定义提供商
              </button>
            )}
            <button
              type="button"
              className="dsh-btn cancel"
              onClick={resetForm}
            >
              {editingId ? "取消编辑" : "重置"}
            </button>
            <button
              type="button"
              className="dsh-btn save primary"
              disabled={saving}
              onClick={handleSave}
            >
              {saving ? "正在保存…" : editingId ? "保存修改" : "保存提供商"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function SettingsModal({ tab, setTab, settings, onSave, computerState, onWake, onSleep, vault, onOpenVault, models, pairCode, onGenPairCode, integrations, localDevices, onClose, onReloadAll }) {
  const [local, setLocal] = useState(settings || {});
  const [saved, setSaved] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [applyGlobally, setApplyGlobally] = useState(true);
  const [testStatus, setTestStatus] = useState(null);

  useEffect(() => setLocal(settings || {}), [settings]);
  useEffect(() => { if (tab === "vault") onOpenVault(); }, [tab, onOpenVault]);

  const isUnlimited = Number(local.daily_tokens) === 0;

  const change = (key, value) => setLocal((prev) => ({ ...prev, [key]: value }));

  const handleProviderSelect = (pid) => {
    const preset = PROVIDER_PRESETS.find((p) => p.id === pid);
    if (!preset) return;
    setLocal((prev) => ({
      ...prev,
      llm_provider: pid,
      llm_base_url: preset.baseUrl,
      model: preset.defaultModel || prev.model,
    }));
  };

  const testConnection = async () => {
    setTestStatus({ loading: true, msg: "正在测试连接…" });
    try {
      const res = await api("/v1/settings/test-llm", {
        method: "POST",
        body: {
          provider: local.llm_provider || "deepseek",
          base_url: local.llm_base_url,
          api_key: local.llm_api_key,
          model: local.model,
        },
      });
      if (res.ok) {
        setTestStatus({
          loading: false,
          ok: true,
          msg: `✓ 连接成功 (${res.latency_ms}ms) · 模型响应正常`,
        });
      } else {
        setTestStatus({
          loading: false,
          ok: false,
          msg: `✗ ${res.error || "连接测试未通过"}`,
        });
      }
    } catch (err) {
      setTestStatus({
        loading: false,
        ok: false,
        msg: `✗ 请求失败: ${err.message || err}`,
      });
    }
  };

  const save = async () => {
    const payload = {
      model: local.model,
      daily_tokens: isUnlimited ? 0 : (Number(local.daily_tokens) || 0),
      llm_provider: local.llm_provider || "deepseek",
      llm_base_url: (local.llm_base_url || "").trim(),
      apply_model_globally: applyGlobally,
      computer_fps: Number(local.computer_fps) || 2,
      view_only: !!local.view_only,
      local_enabled: !!local.local_enabled,
      local_approval: local.local_approval !== false,
      memory_enabled: local.memory_enabled !== false,
      local_allow_dirs: local.local_allow_dirs || [],
      search_api_key: (local.search_api_key !== undefined ? local.search_api_key : "").trim(),
    };
    if (local.llm_api_key && local.llm_api_key.trim()) {
      payload.llm_api_key = local.llm_api_key.trim();
    }
    await onSave(payload, applyGlobally);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  const combinedModelOptions = Array.from(new Set([
    local.model,
    ...(models?.items ? models.items.map((m) => m.id) : []),
    ...(PROVIDER_PRESETS.find((p) => p.id === local.llm_provider)?.models || []),
    "deepseek-chat", "deepseek-reasoner", "gpt-4o", "gpt-4.1", "o3-mini",
    "anthropic/claude-3.7-sonnet", "qwen-max"
  ].filter(Boolean)));

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-nav">
          {[
            ["general", "通用"],
            ["models", "模型"],
            ["computer", "电脑"],
            ["vault", "保险库"],
          ].map(([id, label]) => (
            <button key={id} className={tab === id ? "settings-tab on" : "settings-tab"} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>
        <div className="settings-body">
          {tab === "general" && (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <h3 style={{ margin: 0 }}>全局大模型配置</h3>
                  <p className="hint" style={{ margin: "4px 0 0" }}>统一管理底座模型、接口地址与秘钥。所有助手默认继承此处配置。</p>
                </div>
                <button
                  type="button"
                  className="mini-btn"
                  style={{ borderColor: "rgba(56, 189, 248, 0.4)", color: "#38bdf8", whiteSpace: "nowrap" }}
                  onClick={() => setTab("models")}
                >
                  ⚙️ 管理服务商与模型列表
                </button>
              </div>

              <label>服务提供商 (Provider Preset)</label>
              <select
                value={local.llm_provider || "deepseek"}
                onChange={(e) => handleProviderSelect(e.target.value)}
              >
                {PROVIDER_PRESETS.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>

              <label>Base URL 接口地址</label>
              <input
                type="text"
                value={local.llm_base_url || ""}
                placeholder="https://api.deepseek.com"
                onChange={(e) => change("llm_base_url", e.target.value)}
              />
              <span style={{ fontSize: "11px", color: "var(--sub)", marginTop: "-4px" }}>
                自动智能兼容 /v1 与 /chat/completions 后缀；支持任何 OpenAI 兼容规范接口。
              </span>

              <label>API Key 秘钥</label>
              <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                <input
                  type={showKey ? "text" : "password"}
                  style={{ flex: 1 }}
                  value={local.llm_api_key ?? ""}
                  placeholder={local.llm_has_key ? `已配置 (${local.llm_api_key_masked || "***"}) · 留空保持不变` : "请输入 API Key (sk-...)"}
                  onChange={(e) => change("llm_api_key", e.target.value)}
                />
                <button
                  type="button"
                  className="mini-btn"
                  style={{ padding: "8px 12px", minWidth: "60px" }}
                  onClick={() => setShowKey(!showKey)}
                >
                  {showKey ? "隐藏" : "显示"}
                </button>
              </div>

              <label>全局默认模型</label>
              <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                <input
                  list="global-model-presets"
                  style={{ flex: 1 }}
                  value={local.model || ""}
                  placeholder="选择或输入模型名称，如 deepseek-chat"
                  onChange={(e) => change("model", e.target.value)}
                />
                <datalist id="global-model-presets">
                  {combinedModelOptions.map((opt) => (
                    <option key={opt} value={opt} />
                  ))}
                </datalist>
                <button
                  type="button"
                  className="mini-btn"
                  style={{ padding: "8px 14px", whiteSpace: "nowrap", borderColor: "rgba(56, 232, 255, 0.4)", color: "var(--cyan)" }}
                  disabled={testStatus?.loading}
                  onClick={testConnection}
                >
                  {testStatus?.loading ? "测试中…" : "⚡ 测试连接"}
                </button>
              </div>

              {testStatus && (
                <div style={{
                  fontSize: "12px",
                  padding: "6px 10px",
                  borderRadius: "6px",
                  background: testStatus.ok ? "rgba(40, 200, 120, 0.12)" : "rgba(255, 70, 70, 0.12)",
                  color: testStatus.ok ? "#4ade80" : "#f87171",
                  border: `1px solid ${testStatus.ok ? "rgba(40, 200, 120, 0.3)" : "rgba(255, 70, 70, 0.3)"}`,
                }}>
                  {testStatus.msg}
                </div>
              )}

              <label className="checkline" style={{ marginTop: "4px" }}>
                <input
                  type="checkbox"
                  checked={applyGlobally}
                  onChange={(e) => setApplyGlobally(e.target.checked)}
                />
                同时将此模型应用于所有已创建的智能体助手（清除个体独立覆盖）
              </label>

              <div className="settings-divider" />

              <h3>联网搜索 API Key（可选）</h3>
              <p className="hint">Agent 默认采用国内必应/360/搜狗多引擎自动级联搜索（免 Key 运行）。若需配置 Tavily / 博查 / Serper Pro 高阶搜索服务，可在此填入 API Key。</p>
              <div className="key-input-row" style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                <input
                  type={showKey ? "text" : "password"}
                  value={local.search_api_key !== undefined ? local.search_api_key : (local.search_has_key ? local.search_api_key_masked : "")}
                  placeholder={local.search_has_key ? local.search_api_key_masked : "tvly-... / sk-... (可选，留空免Key)"}
                  onChange={(e) => change("search_api_key", e.target.value)}
                  style={{ flex: 1 }}
                />
              </div>

              <div className="settings-divider" />

              <h3>全局每日 Token 额度</h3>
              <p className="hint">控制系统每日全局调用消耗上限，防止意外超支。</p>
              <label className="checkline">
                <input
                  type="checkbox"
                  checked={isUnlimited}
                  onChange={(e) => {
                    if (e.target.checked) {
                      change("daily_tokens", 0);
                    } else {
                      change("daily_tokens", 500000);
                    }
                  }}
                />
                每日 Token 不设上限 (0 = 不限额，推荐)
              </label>

              {!isUnlimited && (
                <>
                  <label>自定义每日 Token 上限</label>
                  <input
                    type="number"
                    value={local.daily_tokens || 500000}
                    onChange={(e) => change("daily_tokens", Math.max(0, parseInt(e.target.value) || 0))}
                  />
                </>
              )}
              <div style={{ fontSize: "11px", color: "var(--sub)" }}>
                今日已消耗：<b style={{ color: "var(--text)" }}>{(local.usage_today_tokens || 0).toLocaleString()}</b> tokens
              </div>

              <div className="modal-actions">
                <button className="primary" onClick={save}>{saved ? "已保存 ✓" : "保存设置"}</button>
              </div>

              <div className="settings-divider" />
              <h3>系统安全与记忆</h3>
              <label className="checkline">
                <input type="checkbox" checked={!!local.view_only}
                  onChange={(e) => change("view_only", e.target.checked)} />
                默认仅观看电脑（关闭后才允许直接操作）
              </label>
              <label className="checkline-block">
                <span>本地允许目录（留空 = 不限制；填了以后越界直接拒绝）</span>
                <textarea className="allow-dirs" rows={2}
                  value={Array.isArray(local.local_allow_dirs) ? local.local_allow_dirs.join("\n") : ""}
                  placeholder={"C:\\Users\\你\\Documents\nD:\\work"}
                  onChange={(e) => change("local_allow_dirs",
                    e.target.value.split("\n").map((s) => s.trim()).filter(Boolean))} />
              </label>
              <label className="checkline">
                <input type="checkbox" checked={local.memory_enabled !== false}
                  onChange={(e) => change("memory_enabled", e.target.checked)} />
                长期记忆（每个 Bot 各自学习、互不共享）
              </label>

              <div className="settings-divider" />
              <h3>本地电脑</h3>
              <p className="hint">允许云端 Agent 访问你这台电脑上的文件、命令和桌面（仅 exe 版可用）。</p>
              <p className={`local-status ${localDevices.length ? "on" : ""}`}>
                {localDevices.length
                  ? `本地桥已连接（${localDevices.length} 台）· 能力：${(localDevices[0].caps || []).join(" / ") || "—"}`
                  : "本地桥未连接：打开 exe 并登录后会自动连接（重连间隔 5 秒）"}
              </p>
              <label className="checkline">
                <input type="checkbox" checked={!!local.local_enabled}
                  onChange={(e) => change("local_enabled", e.target.checked)} />
                允许 Agent 访问本机（默认关闭）
              </label>
              <label className="checkline">
                <input type="checkbox" checked={local.local_approval !== false}
                  onChange={(e) => change("local_approval", e.target.checked)} />
                每次本地操作都需要我批准（建议开启）
              </label>
              <div className="modal-actions"><button className="primary" onClick={save}>{saved ? "已保存 ✓" : "保存"}</button></div>

              <div className="settings-divider" />
              <h3>添加设备</h3>
              <p className="hint">另一台电脑下载 exe 后，在登录页选"配对码"，填服务器地址 + 下面的 6 位码即可绑定。</p>
              {pairCode ? (
                <div className="pair-box">
                  <div className="pair-code">{pairCode.code}</div>
                  <div className="pair-meta">
                    {Math.max(0, Math.ceil((pairCode.expires - Date.now()) / 1000))} 秒内有效 · 一次性
                  </div>
                </div>
              ) : (
                <button className="mini-btn" onClick={onGenPairCode}>生成配对码</button>
              )}
            </>
          )}
          {tab === "models" && (
            <DshModelsPane onReloadAll={onReloadAll} models={models} />
          )}
          {tab === "computer" && (
            <>
              <h3>电脑</h3>
              <div className="kv"><span>状态</span><b>{computerState === "awake" ? "运行中" : computerState === "sleeping" ? "休眠" : "未知"}</b></div>
              <div className="settings-row">
                <button className="mini-btn" onClick={onWake}>唤醒</button>
                <button className="mini-btn" onClick={onSleep}>休眠</button>
              </div>
              <label>预览帧率（fps）</label>
              <input type="number" min="1" max="5" value={local.computer_fps || 2}
                onChange={(e) => change("computer_fps", e.target.value)} />
              <p className="hint">休眠会停掉桌面服务（保留文件与登录态）；唤醒约 3–5 秒。</p>
              <div className="modal-actions"><button className="primary" onClick={save}>{saved ? "已保存 ✓" : "保存"}</button></div>
            </>
          )}
          {tab === "vault" && <VaultPane items={vault} onReload={onOpenVault} />}
        </div>
      </div>
    </div>
  );
}

/* ============================== 保险库 ============================== */
function VaultPane({ items, onReload }) {
  const [kind, setKind] = useState("login");
  const [title, setTitle] = useState("");
  const [fields, setFields] = useState({});
  const [revealed, setRevealed] = useState(null);
  const [err, setErr] = useState("");

  const fieldDefs = {
    login: [["username", "用户名/邮箱"], ["password", "密码", "password"]],
    card: [["number", "卡号"], ["holder", "持卡人"], ["cvv", "安全码", "password"]],
    contact: [["phone", "手机号"], ["email", "邮箱"]],
  };

  const add = async () => {
    if (!title.trim()) { setErr("标题不能为空"); return; }
    try {
      await api("/v1/vault", { method: "POST", body: { kind, title: title.trim(), fields } });
      setTitle(""); setFields({}); setErr("");
      onReload();
    } catch (e) { setErr(String(e.message || e)); }
  };
  const reveal = async (id) => {
    try { setRevealed(await api(`/v1/vault/${id}/reveal`, { method: "POST" })); }
    catch (e) { setErr(String(e.message || e)); }
  };
  const remove = async (id) => {
    try { await api(`/v1/vault/${id}`, { method: "DELETE" }); onReload(); setRevealed(null); }
    catch (e) { setErr(String(e.message || e)); }
  };

  return (
    <>
      <h3>保险库</h3>
      <p className="hint">只存密文。Bot 永远看不到已保存的密码和卡号，只有你点"查看"能取一次。</p>
      <div className="vault-list">
        {items.map((it) => (
          <div key={it.id} className="vault-row">
            <div className="vault-main">
              <div className="vault-title">{it.title}</div>
              <div className="vault-sub">
                {it.kind === "login" ? `登录 · ${it.masked.username}` : it.kind === "card" ? `卡片 · ****${it.masked.last4}` : `联系人 · ${it.masked.phone}`}
              </div>
            </div>
            <button className="mini-btn" onClick={() => reveal(it.id)}>查看</button>
            <button className="mini-btn danger" onClick={() => remove(it.id)}>删除</button>
          </div>
        ))}
        {!items.length && <p className="hint">还没有保存任何内容。</p>}
      </div>
      {revealed && (
        <pre className="preview">{JSON.stringify(revealed.fields, null, 2)}</pre>
      )}
      <div className="vault-add">
        <div className="vault-add-head">新项目</div>
        <select value={kind} onChange={(e) => { setKind(e.target.value); setFields({}); }}>
          <option value="login">登录信息</option>
          <option value="card">卡片</option>
          <option value="contact">联系信息</option>
        </select>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="标题，如 GitHub" />
        {fieldDefs[kind].map(([key, label, type]) => (
          <input key={key} type={type || "text"} value={fields[key] || ""}
            placeholder={label}
            onChange={(e) => setFields((prev) => ({ ...prev, [key]: e.target.value }))} />
        ))}
        {err && <div className="login-error">{err}</div>}
        <button className="primary" onClick={add}>保存到保险库</button>
      </div>
    </>
  );
}

/* ---------------- 工具函数 ---------------- */
function escHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderMarkdown(text) {
  let s = escHtml(text || "");
  s = s.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) => `<pre><code>${code}</code></pre>`);
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  s = s.replace(/^\s{0,3}(#{1,4})\s+(.+)$/gm,
    (_m, h, t) => `<h${h.length + 2}>${t}</h${h.length + 2}>`);
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer">$1</a>');

  // Markdown 表格先提出来，避免被行内解析和段落包裹破坏
  const tables = [];
  s = s.replace(/(?:^\|.*\|\s*$\n?){2,}/gm, (block) => {
    const rows = block.trim().split("\n").map((r) => r.trim());
    if (rows.length < 2) return block;
    const isSep = (r) => /^\|[\s:|-]+\|$/.test(r);
    if (!isSep(rows[1])) return block;
    const cells = (r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    const head = cells(rows[0]);
    const body = rows.slice(2).filter((r) => !isSep(r)).map(cells);
    const html = "<table><thead><tr>" + head.map((c) => `<th>${c}</th>`).join("") +
      "</tr></thead><tbody>" +
      body.map((r) => "<tr>" + head.map((_, i) => `<td>${r[i] ?? ""}</td>`).join("") + "</tr>").join("") +
      "</tbody></table>";
    tables.push(html);
    return `\u0000T${tables.length - 1}\u0000`;
  });

  const lines = s.split("\n");
  const out = [];
  let para = [];
  let inList = false;
  const flushPara = () => {
    if (para.length) { out.push(`<p>${para.join("\n")}</p>`); para = []; }
  };
  for (const line of lines) {
    const tb = /^\u0000T(\d+)\u0000$/.exec(line.trim());
    if (tb) {
      flushPara();
      if (inList) { out.push("</ul>"); inList = false; }
      out.push(tables[Number(tb[1])]);
      continue;
    }
    const m = /^\s*[-*]\s+(.*)$/.exec(line);
    if (m) {
      flushPara();
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${m[1]}</li>`);
      continue;
    }
    if (inList) { out.push("</ul>"); inList = false; }
    if (!line.trim()) { flushPara(); continue; }
    para.push(line);
  }
  flushPara();
  if (inList) out.push("</ul>");
  return out.join("\n");
}

function toMessage(e) {
  return {
    id: e.id,
    kind: e.kind,
    text: e.text ?? e.payload?.text ?? "",
    attachments: e.attachments ?? e.payload?.attachments ?? [],
    ts: e.ts,
    actor: e.actor,
  };
}

// artifact.attached → 消息流里的一条（kind 复用事件名，渲染时单独分支）
function toArtifact(e) {
  const p = e.payload || {};
  return {
    id: e.id,
    kind: "artifact.attached",
    ts: e.ts,
    artifact: {
      artifact_id: p.artifact_id || "",
      name: p.name || "截图",
      kind: p.kind || "image/png",
      bytes: Number(p.bytes || 0),
      url: p.url || (p.artifact_id ? `/v1/artifacts/${p.artifact_id}` : ""),
    },
  };
}
function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
function stateLabel(s) {
  return { running: "进行中", ok: "成功", fail: "失败", waiting: "等你回复" }[s] || s;
}
function statusText(s) {
  return { idle: "空闲", thinking: "正在思考", needs_human: "需要你确认", failed: "执行中断（可继续）" }[s] || s;
}
function join(base, name) {
  return base ? `${base}/${name}` : name;
}
function parentOf(p) {
  const parts = p.split("/");
  parts.pop();
  return parts.join("/");
}
