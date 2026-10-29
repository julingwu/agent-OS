import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, auth, ping, scopeOf } from "./api.js";
import Workspace from "./Workspace.jsx";
import NewTaskModal from "./NewTaskModal.jsx";
import MissionControl from "./MissionControl.jsx";
import MaintenanceView from "./MaintenanceView.jsx";
import DialogAccessibility from "./DialogAccessibility.jsx";
import ExtensionsView from "./ExtensionsView.jsx";
import GovernanceView from "./GovernanceView.jsx";
import CyberFactorioDeck from "./CyberFactorioDeck.jsx";
import "./styles.css";

import { cls, initials, Icon } from "./views/common.jsx";
import {
  Login, SettingsModal, AccountModal, HelpModal, AboutModal, AlertBanner, ContextMenu,
} from "./views/ShellModals.jsx";
import {
  OrgView, AddMember, NewGroup, EmployeePanel, RolesView, NewDept,
} from "./views/OrgRolesView.jsx";
import { ChatView, RunDetail, GroupMenu } from "./views/ChatsView.jsx";
import { TaskInspectorModal } from "./views/TasksView.jsx";
import { ApprovalsView } from "./views/ApprovalsView.jsx";
import { FilesTab } from "./views/ArtifactsView.jsx";
import { FinOpsView } from "./views/UsageFinOpsView.jsx";
import { EvalsView } from "./views/EvalsExpView.jsx";

/* ------------------------------------------------------------------ shell */
export function App() {
  const [session, setSession] = useState(null);
  const [booted, setBooted] = useState(false);
  const [bootMsg, setBootMsg] = useState("载入中…");
  const generation = useRef(0);

  const refreshSession = useCallback(async () => {
    const request = ++generation.current;
    const epoch = auth.epoch();
    const current = () => request === generation.current && epoch === auth.epoch();
    try {
      // 桌面启动可能需要等后端；切换会话后旧启动不能继续请求或挂载 UI。
      for (let i = 0; i < 30; i++) {
        if (!current()) return null;
        if (await ping()) break;
        if (!current()) return null;
        setBootMsg(i > 1 ? "正在叫醒本地服务…" : "载入中…");
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      if (!current()) return null;
      const me = await api.me();
      if (!current()) return null;
      const verified = { me, epoch: auth.bindScope(me) };
      setSession(verified);
      return verified;
    } catch (error) {
      if (!current() || error.stale) return null;
      if (error.code === 401) setSession(null);
      else setBootMsg(error.message || "工作空间暂时无法加载，请刷新重试。");
      return null;
    } finally {
      if (request === generation.current) setBooted(true);
    }
  }, []);

  useEffect(() => {
    const expired = (e) => {
      if (e.detail?.epoch !== undefined && e.detail.epoch !== auth.epoch()) return;
      generation.current += 1; auth.clear(); setSession(null); setBooted(true);
    };
    const changed = () => {
      generation.current += 1; setSession(null); setBooted(false); refreshSession();
    };
    window.addEventListener("oa-auth-expired", expired);
    window.addEventListener("oa-auth-changed", changed);
    refreshSession();
    return () => {
      generation.current += 1;
      window.removeEventListener("oa-auth-expired", expired);
      window.removeEventListener("oa-auth-changed", changed);
    };
  }, [refreshSession]);

  if (!booted) return <div className="empty"><span className="spin" /> {bootMsg}</div>;
  if (!session) return <Login onDone={refreshSession} />;
  return (
    <AuthenticatedWorkspace
      key={`${session.epoch}:${scopeOf(session.me)}`}
      me={session.me}
      sessionEpoch={session.epoch}
      refreshSession={refreshSession}
    />
  );
}

export function AuthenticatedWorkspace({ me, sessionEpoch, refreshSession }) {
  const [bootError, setBootError] = useState("");
  const [tab, setTab] = useState(() => {
    const selected = new URLSearchParams(location.search).get("tab");
    const valid = [
      "cyberdeck", "overview", "projects", "tasks", "chats", "files",
      "approvals", "org", "roles", "finops", "evals", "experience",
      "extensions", "maintenance",
    ];
    return valid.includes(selected) ? selected : "cyberdeck";
  });
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
  const [theme, setTheme] = useState(localStorage.getItem("oa_theme") || "light");
  const [showDetail, setShowDetail] = useState(() =>
    localStorage.getItem("oa_detail") === null ? window.innerWidth > 1200 : localStorage.getItem("oa_detail") !== "0"
  );
  const [ctx, setCtx] = useState(null);
  const [depts, setDepts] = useState([]);
  const [depting, setDepting] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const [taskCreating, setTaskCreating] = useState(false);
  const [focusedTask, setFocusedTask] = useState(null);
  const [chatQuery, setChatQuery] = useState("");
  const mounted = useRef(true);
  const bootSequence = useRef(0);
  const approvalSequence = useRef(0);
  const sessionCurrent = useCallback(() => mounted.current && sessionEpoch === auth.epoch(), [sessionEpoch]);

  useEffect(() => () => { mounted.current = false; }, []);

  const refreshApprovals = useCallback(async () => {
    if (!sessionCurrent()) return;
    const request = ++approvalSequence.current;
    try {
      const result = await api.approvals();
      if (sessionCurrent() && request === approvalSequence.current) {
        setPendingCount((result.pending || []).length);
      }
    } catch { /* 未登录/离线时静默 */ }
  }, [sessionCurrent]);

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
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
    };
  }, []);

  async function patchChat(id, body) {
    if (!sessionCurrent()) return;
    await api.patchChat(id, body);
    if (!sessionCurrent()) return;
    const c = await api.chats();
    if (sessionCurrent()) setChats(c.chats);
  }

  async function renameDept(d) {
    const name = window.prompt("部门新名称", d.name);
    if (!name || name === d.name || !sessionCurrent()) return;
    await api.renameDept(d.id, name);
    if (!sessionCurrent()) return;
    const result = await api.departments();
    if (sessionCurrent()) setDepts(result.departments);
  }

  async function removeDept(d) {
    const ok = window.confirm(`删除部门「${d.name}」？\n部门下 ${d.headcount} 名员工会转到「未分配」。`);
    if (!ok || !sessionCurrent()) return;
    await api.deleteDept(d.id);
    if (!sessionCurrent()) return;
    const departments = await api.departments();
    if (!sessionCurrent()) return;
    setDepts(departments.departments);
    const organization = await api.org();
    if (sessionCurrent()) setOrg(organization);
  }

  async function removeChat(chat) {
    const ok = window.confirm(`删除「${chat.title}」？\n会立即停止该会话正在运行的任务，并清理沙箱临时目录。`);
    if (!ok || !sessionCurrent()) return;
    await api.deleteChat(chat.id);
    if (!sessionCurrent()) return;
    const c = await api.chats();
    if (!sessionCurrent()) return;
    setChats(c.chats);
    if (active?.id === chat.id) setActive(c.chats[0] || null);
  }

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("oa_theme", theme);
  }, [theme]);

  const boot = useCallback(
    async (verifyIdentity = true) => {
      if (!sessionCurrent()) return;
      const request = ++bootSequence.current;
      const current = () => sessionCurrent() && request === bootSequence.current;
      try {
        setBootError("");
        if (verifyIdentity) {
          const verified = await refreshSession();
          if (!verified || !current()) return;
        }
        const [c, o, dp] = await Promise.allSettled([api.chats(), api.org(), api.departments()]);
        if (!current()) return;
        const failed = [c, o, dp].filter((result) => result.status === "rejected");
        const expired = failed.find((result) => result.reason.code === 401);
        if (expired) throw expired.reason;
        if (c.status === "fulfilled") {
          setChats(c.value.chats);
          setActive((prev) => c.value.chats.find((chat) => chat.id === prev?.id) || c.value.chats[0] || null);
        }
        if (o.status === "fulfilled") setOrg(o.value);
        if (dp.status === "fulfilled") setDepts(dp.value.departments);
        if (failed.length) setBootError("部分工作空间数据暂时不可用，可以刷新重试。");
      } catch (e) {
        if (current() && !e.stale && e.code !== 401) {
          setBootError(e.message || "工作空间暂时无法加载，请刷新重试。");
        }
      }
    },
    [refreshSession, sessionCurrent]
  );

  useEffect(() => { boot(false); }, [boot]);

  useEffect(() => {
    if (!me) return;
    refreshApprovals();
    const t = setInterval(refreshApprovals, 5000);
    return () => clearInterval(t);
  }, [me, refreshApprovals]);

  // 注意：所有 hook 必须在 early return 之前，否则 React #310（v1.3 踩过）
  useEffect(() => {
    localStorage.setItem("oa_detail", showDetail ? "1" : "0");
  }, [showDetail]);

  const navItems = [
    ["cyberdeck", "spark", "赛博指挥舱"],
    ["overview", "overview", "工作台"],
    ["projects", "flow", "总装流水线"],
    ["tasks", "tasks", "任务中心"],
    ["chats", "chat", "协作会话"],
    ["files", "files", "工作产物"],
    ["approvals", "shield", "审批中心"],
    ["org", "org", "组织与团队"],
    ["roles", "role", "角色库"],
    ["finops", "wallet", "用量与费用"],
    ["evals", "flask", "评测中心"],
    ["experience", "brain", "组织经验"],
    ["extensions", "gear", "工具与集成"],
    ["maintenance", "refresh", "系统维护"],
  ].filter(([key]) => (key !== "extensions" && key !== "maintenance") || ["ceo", "system"].includes(me.role));

  const pageName = navItems.find(([key]) => key === tab)?.[2] || "工作台";
  const chatLayout = tab === "chats";
  const openChat = (chat) => { setActive(chat); setTab("chats"); };
  const openApprovals = () => { setFocusedTask(null); setShowDetail(false); setTab("approvals"); };

  const returnToMainUI = (e) => {
    if (e && e.preventDefault) e.preventDefault();
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: "SWITCH_TO_CHAT" }, "*");
    } else {
      window.location.href = "/ui/";
    }
  };

  const navButton = ([key, icon, label]) => (
    <button
      key={key}
      title={label}
      aria-label={label}
      aria-current={tab === key ? "page" : undefined}
      className={cls("rail-btn", tab === key && "active")}
      onClick={() => setTab(key)}
    >
      <Icon name={icon} /><span className="nav-label">{label}</span>
      {key === "approvals" && pendingCount > 0 && <span className="rail-badge">{pendingCount}</span>}
    </button>
  );

  return (
    <div className={cls("shell", chatLayout && "chat-layout", (!chatLayout || !showDetail) && "no-detail")}>
      <DialogAccessibility />
      <nav className="rail" aria-label="主导航">
        <div className="workspace-brand">
          <span className="brand-mark"><Icon name="spark" /></span>
          <span className="brand-copy"><b>Agent OS</b><small>AI WORKSPACE</small></span>
        </div>
        <div
          className="workspace-switcher"
          onClick={returnToMainUI}
          style={{ cursor: "pointer" }}
          title="点击返回 AI 团队主界面（对话与电脑协作）"
        >
          <Icon name="org" /><div><b>我的工作空间</b><small>← 返回 AI 团队主界面</small></div>
          <span className="spacer" /><Icon name="chevron" />
        </div>
        <div className="nav-section-label">工作空间</div>
        {navItems.slice(0, 6).map(navButton)}
        <div className="nav-section-label second">管理与洞察</div>
        {navItems.slice(6).map(navButton)}
        <div className="grow" />
        <div className="nav-footer">
          <button className="rail-btn" title="添加 AI 员工" onClick={() => setAdding(true)}>
            <Icon name="plus" /><span className="nav-label">添加 AI 员工</span>
          </button>
          {me.can_manage_deployment && (
            <button className="rail-btn" title="工作空间设置" onClick={() => setShowSettings(true)}>
              <Icon name="gear" /><span className="nav-label">工作空间设置</span>
            </button>
          )}
          <div className="rail-account">
            <button title="账号信息" onClick={() => setShowAccount(true)}>
              <span className="avatar">{initials(me.name)}</span>
              <span className="account-copy"><b>{me.name}</b><small>工作空间管理员</small></span>
            </button>
            <button
              className="icon-btn"
              title={theme === "dark" ? "切换浅色主题" : "切换深色主题"}
              aria-label={theme === "dark" ? "切换浅色主题" : "切换深色主题"}
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              <Icon name={theme === "dark" ? "sun" : "moon"} />
            </button>
          </div>
        </div>
      </nav>

      {chatLayout && (
        <aside className="sidebar" aria-label="协作会话列表">
          <div className="panel-head">
            <span className="ttl">协作会话</span><span className="spacer" />
            <button className="icon-btn" title="创建协作群" aria-label="创建协作群" onClick={() => setGrouping(true)}>
              <Icon name="plus" />
            </button>
          </div>
          <label className="sidebar-search">
            <Icon name="search" />
            <input aria-label="搜索会话" placeholder="搜索会话…" value={chatQuery} onChange={(e) => setChatQuery(e.target.value)} />
          </label>
          <div className="list">
            {chats
              .filter((c) => `${c.title} ${c.employee_name || ""}`.toLowerCase().includes(chatQuery.toLowerCase()))
              .map((c) => (
                <button
                  key={c.id}
                  className={cls("chat-item", active?.id === c.id && "active", c.pinned && "pinned")}
                  aria-pressed={active?.id === c.id}
                  onClick={() => setActive(c)}
                  onContextMenu={(e) =>
                    openCtx(e, [
                      { label: c.pinned ? "取消置顶" : "置顶", onClick: () => patchChat(c.id, { pinned: c.pinned ? 0 : 1 }) },
                      { label: "隐藏", onClick: () => patchChat(c.id, { hidden: 1 }) },
                      { sep: true },
                      { label: "删除会话", danger: true, onClick: () => removeChat(c) },
                    ])
                  }
                >
                  <span className={cls("avatar", c.kind === "ceo" && "ceo")}>
                    {c.kind === "ceo" ? "CEO" : initials(c.employee_name || c.title)}
                  </span>
                  <span className="meta">
                    <span className="name">{c.employee_name || c.title}{c.kind === "ceo" && <span className="tag-ceo">调度台</span>}</span>
                    <span className="preview">{c.last_message || "开始一项新任务"}</span>
                  </span>
                </button>
              ))}
            {!chats.length && <div className="empty">暂无会话，请先添加 AI 员工。</div>}
          </div>
          <div className="sidebar-bottom-note"><span className="live-dot" /> 进度与消息自动更新</div>
        </aside>
      )}

      <ContextMenu ctx={ctx} />
      {depting && <NewDept onClose={() => setDepting(false)} onDone={boot} />}
      <div className={cls("right", tab === "cyberdeck" && "cyberdeck-view")}>
        <header className="app-topbar">
          <div className="app-breadcrumb">
            <Icon name="org" /><span className="breadcrumb-workspace">我的工作空间</span>
            <Icon name="chevron" /><b>{pageName}</b>
          </div>
          <span className="spacer" />
          {chatLayout && (
            <select
              className="mobile-chat-picker"
              aria-label="选择协作会话"
              value={active?.id || ""}
              onChange={(e) => setActive(chats.find((c) => c.id === e.target.value))}
            >
              {chats.map((c) => (<option value={c.id} key={c.id}>{c.employee_name || c.title}</option>))}
            </select>
          )}
          <a
            href="/ui/"
            className="btn-return-workspace"
            onClick={returnToMainUI}
            title="返回 AgentOS 主界面（角色助手对话、实时电脑桌面操控、技能赋予）"
          >
            ← 返回 AI 团队主界面
          </a>
          <span className="connection-label"><span className="live-dot" /> 本地工作空间</span>
          <button className="icon-btn" title="使用帮助" aria-label="使用帮助" onClick={() => setShowHelp(true)}>
            <Icon name="help" />
          </button>
        </header>
        {bootError && (
          <div className="hint" role="status">
            {bootError} <button className="btn ghost" onClick={boot}>重新加载</button>
          </div>
        )}
        {me.can_manage_deployment && <AlertBanner tenantId={me.tenant_id || "LE-GROUP-HQ"} />}
        {tab === "cyberdeck" ? (
          <CyberFactorioDeck
            me={me}
            Icon={Icon}
            onOpenChat={openChat}
            onNavigate={setTab}
            onReturnMainUI={returnToMainUI}
            onNavigateTab={setTab}
            onOpenAddMember={() => setAdding(true)}
            pendingCount={pendingCount}
          />
        )
          : tab === "overview" || tab === "tasks" ? (
            <Workspace
              key={tab} chats={chats} pendingCount={pendingCount} Icon={Icon}
              onChat={openChat} onSelectTask={setFocusedTask} onNavigate={setTab}
              onNewTask={() => setTaskCreating(true)} tasksOnly={tab === "tasks"}
            />
          ) : tab === "projects" ? <MissionControl Icon={Icon} onOpenChat={openChat} me={me} />
          : tab === "finops" ? <FinOpsView />
          : tab === "evals" ? <EvalsView />
          : tab === "experience" ? <GovernanceView me={me} />
          : tab === "extensions" ? (["ceo", "system"].includes(me.role) ? <ExtensionsView /> : <div className="empty" role="status">请由管理员配置工具连接。</div>)
          : tab === "maintenance" ? (["ceo", "system"].includes(me.role) ? <MaintenanceView /> : <div className="empty" role="status">仅管理员可执行系统清理。</div>)
          : tab === "roles" ? <RolesView onChanged={boot} />
          : tab === "org" ? (
            <>
              <div className="org-actions">
                <span>组织与团队</span>
                {me.can_manage_deployment && (
                  <button className="btn" onClick={() => setDepting(true)}>
                    <Icon name="plus" /> 新建部门
                  </button>
                )}
                <button className="btn primary" onClick={() => setAdding(true)}>
                  <Icon name="plus" /> 添加员工
                </button>
              </div>
              <OrgView
                org={org} onPick={(emp) => setEmpPanel(emp)} departments={depts}
                onRenameDept={me.can_manage_deployment ? renameDept : undefined}
                onDeleteDept={me.can_manage_deployment ? removeDept : undefined}
              />
            </>
          ) : tab === "approvals" ? <ApprovalsView onChanged={refreshApprovals} />
          : tab === "files" ? <FilesTab />
          : active ? (
            <ChatView
              key={active.id} chat={active} onSent={boot} onApproval={openApprovals}
              onMore={(c) => setGroupMenu(c)} onHelp={() => setShowHelp(true)}
              onAbout={() => setShowAbout(true)} onToggleDetail={() => setShowDetail((v) => !v)}
              showDetail={showDetail}
            />
          ) : (
            <div className="main"><div className="empty">添加一位 AI 员工，开始协作。</div></div>
          )}
        {empPanel && tab === "org" && (
          <EmployeePanel
            emp={empPanel} onClose={() => setEmpPanel(null)} onChanged={boot}
            onOpenChat={(employee) => {
              const chat = chats.find((item) => item.employee_id === employee.id);
              if (chat) { openChat(chat); setEmpPanel(null); }
            }}
          />
        )}
      </div>
      {chatLayout && active && showDetail && (
        <>
          <div className="drawer-mask detail-backdrop" onClick={() => setShowDetail(false)} />
          <RunDetail key={active.id} chat={active} show={showDetail} onApproval={openApprovals} onToggle={() => setShowDetail(false)} />
        </>
      )}
      {taskCreating && (
        <NewTaskModal
          chats={chats} Icon={Icon} onClose={() => setTaskCreating(false)}
          onCreated={(c) => {
            if (!sessionCurrent()) return;
            setTaskCreating(false); openChat(c); boot();
          }}
        />
      )}
      {focusedTask && (
        <TaskInspectorModal
          task={focusedTask} chats={chats} onClose={() => setFocusedTask(null)}
          onApproval={openApprovals}
          onAct={() => { window.dispatchEvent(new Event("oa-tasks-changed")); boot(); }}
          onOpenChat={(c) => { openChat(c); setFocusedTask(null); }}
        />
      )}
      {adding && <AddMember org={org} onClose={() => setAdding(false)} onDone={boot} />}
      {grouping && (
        <NewGroup
          onClose={() => setGrouping(false)}
          onDone={async (chatId) => {
            if (!sessionCurrent()) return;
            await boot();
            if (!sessionCurrent()) return;
            const c = await api.chats();
            if (!sessionCurrent()) return;
            setChats(c.chats);
            const g = c.chats.find((x) => x.id === chatId);
            if (g) setActive(g);
          }}
        />
      )}
      {groupMenu && <GroupMenu chat={groupMenu} onClose={() => setGroupMenu(null)} onChanged={boot} />}
      {showSettings && <SettingsModal theme={theme} setTheme={setTheme} onClose={() => setShowSettings(false)} onChanged={boot} />}
      {showAccount && <AccountModal me={me} onClose={() => setShowAccount(false)} />}
      {showHelp && <HelpModal onClose={() => setShowHelp(false)} />}
      {showAbout && <AboutModal onClose={() => setShowAbout(false)} />}
    </div>
  );
}

async function bootConsole() {
  // exe/网页已经用设备令牌登录过；这里用一次性 sso 票据换工作台签名 token，
  // 免得进 /app/ 还要再登录一次。
  const url = new URL(location.href);
  const ticket = url.searchParams.get("sso");
  if (ticket) {
    try {
      const res = await fetch(`/v1/legacy-sso/exchange?ticket=${encodeURIComponent(ticket)}`);
      if (res.ok) {
        const data = await res.json();
        if (data.token) localStorage.setItem("oa_token", data.token);
      }
    } catch {
      /* 换票失败就退回普通登录页 */
    }
    url.searchParams.delete("sso");
    history.replaceState({}, "", url.pathname + (url.search || "") + url.hash);
  }
  createRoot(document.getElementById("root")).render(<App />);
}

bootConsole();
