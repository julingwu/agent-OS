// In the browser the shell is served by FastAPI, so relative URLs just work.
// In the Tauri build the page runs from tauri://localhost, so it must talk to
// the local backend explicitly and carry a Bearer token (cross-site cookies die).
const DESKTOP = location.hostname === "tauri.localhost" || location.protocol === "tauri:";
const ORIGIN = DESKTOP ? "http://127.0.0.1:8077" : "";
const BASE = ORIGIN + "/ui";

const TOKEN_KEY = "oa_token";
let sessionEpoch = 0;
let sessionScope = "";
let loginAttempt = 0;
let pendingLogout = null;
const staleSession = () => Object.assign(new Error("工作空间会话已切换。"), {
  code: 0, reason: "SessionChanged", stale: true,
});
const advanceSession = () => { sessionScope = ""; return ++sessionEpoch; };
export const scopeOf = me => JSON.stringify([
  me.identity_id, me.tenant_id, me.entity_id, me.role, !!me.can_manage_deployment,
]);
export const auth = {
  get: () => localStorage.getItem(TOKEN_KEY) || "",
  set: (t) => { localStorage.setItem(TOKEN_KEY, t); advanceSession(); },
  clear: () => { localStorage.removeItem(TOKEN_KEY); advanceSession(); },
  epoch: () => sessionEpoch,
  bindScope: (me) => {
    const scope = scopeOf(me);
    if (scope !== sessionScope) { sessionScope = scope; sessionEpoch += 1; }
    return sessionEpoch;
  },
};

window.addEventListener("storage", event => {
  if (event.key !== TOKEN_KEY && event.key !== null) return;
  advanceSession();
  window.dispatchEvent(new Event("oa-auth-changed"));
});

async function req(path, opts = {}) {
  const headers = opts.body instanceof FormData ? {} : { "content-type": "application/json" };
  const token = auth.get();
  const epoch = auth.epoch();
  const current = () => epoch === auth.epoch() && token === auth.get();
  if (token) headers.authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(BASE + path, {
      credentials: DESKTOP ? "omit" : "same-origin",
      ...opts,
      headers: { ...headers, ...(opts.headers || {}) },
    });
  } catch {
    if (!current()) throw staleSession();
    throw Object.assign(new Error("无法连接工作空间，请确认服务已启动后重试。"), { code: 0 });
  }
  if (!current()) throw staleSession();
  if (!res.ok) {
    let detail = {};
    try { detail = await res.json(); } catch { /* ignore */ }
    if (!current()) throw staleSession();
    const code = detail?.detail?.code;
    const messages = {
      BadCredentials: "身份 ID 或密钥不正确", NotLoggedIn: "登录已过期，请重新登录。",
      TaskActionNotAllowed: "任务状态已更新，请刷新后再操作。", NotRetryable: "当前任务状态不支持重试。",
      EvidenceRequired: "请先查看工作产物，并补充验收说明。", ScopeDenied: "当前账号没有执行此操作的权限。",
      CrossEntityForbidden: "当前账号无法操作这个工作空间的任务。", FileGone: "产物文件已不存在，请重新执行任务。",
      FileNotFound: "找不到这个文件，可能已被移除。", TaskNotFound: "找不到这个任务。",
      Forbidden: "当前账号没有执行此操作的权限。", AdminRequired: "此功能需要工作空间管理员权限。",
      TenantMismatch: "工作空间与当前账号不匹配，请重新登录。",
      DeploymentAdminOnly: "此配置由部署管理员统一管理。",
      ProjectNotFound: "项目不存在或无权访问。", ChatNotFound: "会话不存在或无权访问。",
      McpServerNotFound: "工具连接不存在或无权访问。", EmployeeNotFound: "员工不存在或无权访问。",
      MandatoryCheckFailed: "强制规则检查未通过，请修改交付内容后重试。",
      ExperienceVersionChanged: "经验已被修改，请刷新并查看新内容后重新审核。",
      ExperienceAlreadyReviewed: "经验已被审核，请刷新查看最新结果。",
      ProjectRecoveryRequired: "请提交修改后的需求重新检查项目。",
      RevisionRequired: "请先修改完整需求，再重新检查。",
      ProjectNotRetryable: "项目状态已变化，请刷新后重试。",
    };
    if (res.status === 401 && path !== "/login") {
      auth.clear();
      window.dispatchEvent(new CustomEvent("oa-auth-expired", { detail: { epoch: auth.epoch() } }));
    }
    const message = messages[code] || detail?.detail?.message || (typeof detail?.detail === "string" ? detail.detail : null);
    throw Object.assign(new Error(message || (res.status === 401 ? "登录已过期，请重新登录。" : `操作未完成（${res.status}），请刷新后重试。`)), { code: res.status, reason: code });
  }
  const result = await res.json();
  if (!current()) throw staleSession();
  return result;
}

export const api = {
  me: () => req("/me"),
  login: async (identity_id, secret) => {
    const attempt = ++loginAttempt;
    // A logout response clears the HttpOnly cookie even if its JS result is
    // stale. Finish it before issuing another login that sets a new cookie.
    let logout;
    do {
      logout = pendingLogout;
      if (logout) await logout;
    } while (logout !== pendingLogout);
    if (attempt !== loginAttempt) throw staleSession();
    const epoch = auth.epoch();
    const r = await req("/login", { method: "POST", body: JSON.stringify({ identity_id, secret }) });
    if (attempt !== loginAttempt || epoch !== auth.epoch()) throw staleSession();
    auth.set(r.token || "");
    return r;
  },
  logout: async () => {
    const token = auth.get();
    auth.clear();
    window.dispatchEvent(new CustomEvent("oa-auth-expired", { detail: { epoch: auth.epoch() } }));
    const request = req("/logout", { method: "POST",
      headers: token ? { authorization: `Bearer ${token}` } : {} });
    pendingLogout = request.catch(() => {});
    try {
      return await request;
    } catch (error) {
      if (!error.stale) throw error;
    }
  },
  org: () => req("/org"),
  chats: () => req("/chats"),
  employees: () => req("/employees"),
  createGroup: (name, ids) =>
    req("/groups", { method: "POST", body: JSON.stringify({ name, employee_ids: ids }) }),
  messages: (chatId, after = 0) => req(`/chats/${chatId}/messages?after=${after}`),
  messageStreamUrl: (chatId, after = 0) => {
    const params = new URLSearchParams({ after: String(after) });
    if (DESKTOP && auth.get()) params.set("t", auth.get());
    return `${BASE}/chats/${encodeURIComponent(chatId)}/stream?${params}`;
  },
  send: (chatId, text) =>
    req(`/chats/${chatId}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  task: (taskId) => req(`/tasks/${taskId}`),
  tasks: (limit = 50, options = {}) => req(`/tasks?${new URLSearchParams({ limit, ...options })}`),
  action: (taskId, action, note) =>
    req(`/tasks/${taskId}/action`, { method: "POST", body: JSON.stringify({ action, note }) }),
  retryTask: (taskId, note = "") =>
    req(`/tasks/${taskId}/retry`, { method: "POST", body: JSON.stringify({ note }) }),
  addMember: (payload) =>
    req("/members", { method: "POST", body: JSON.stringify(payload) }),
};

// ---------------------------------------------------------------- v1.1 管理面
Object.assign(api, {
  about: () => req("/about"),
  account: () => req("/account"),
  changeSecret: (secret) => req("/account/secret", { method: "POST", body: JSON.stringify({ secret }) }),
  settings: () => req("/settings"),
  saveSettings: (payload) => req("/settings", { method: "PUT", body: JSON.stringify(payload) }),
  alerts: () => req("/alerts?unacked=1"),
  ackAlert: (id) => req(`/alerts/${id}/ack`, { method: "POST" }),
  files: () => req("/files"),
  deleteFile: (id) => req(`/files/${id}`, { method: "DELETE" }),
  fileUrl: (id) => `${ORIGIN}/ui/files/${id}/download${DESKTOP && auth.get() ? `?t=${encodeURIComponent(auth.get())}` : ""}`,
  kb: (entity) => req(`/kb${entity ? `?entity=${entity}` : ""}`),
  uploadKb: (entity, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return req(`/kb?entity=${encodeURIComponent(entity)}`, { method: "POST", body: fd });
  },
  deleteKb: (id) => req(`/kb/${id}`, { method: "DELETE" }),
  jobs: () => req("/jobs"),
  createJob: (payload) => req("/jobs", { method: "POST", body: JSON.stringify(payload) }),
  deleteJob: (id) => req(`/jobs/${id}`, { method: "DELETE" }),
  runJob: (id) => req(`/jobs/${id}/run`, { method: "POST" }),
  groupMembers: (chatId) => req(`/groups/${chatId}/members`),
  invite: (chatId, ids) => req(`/groups/${chatId}/members`, { method: "POST", body: JSON.stringify({ employee_ids: ids }) }),
  kick: (chatId, empId) => req(`/groups/${chatId}/members/${empId}`, { method: "DELETE" }),
  leaveGroup: (chatId) => req(`/groups/${chatId}/leave`, { method: "POST" }),
  editEmployee: (id, payload) => req(`/employees/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  fireEmployee: (id) => req(`/employees/${id}/fire`, { method: "POST" }),
});

// 后端是否已经醒来（桌面壳会自动拉起 uvicorn，这里只问「端口通不通」）
export async function ping() {
  try {
    await fetch(`${BASE}/about`, { method: "GET" });
    return true;                     // 401 也算通：服务在，只是没登录
  } catch {
    return false;
  }
}

// 预览：后端用 CSP sandbox 把产物关进不透明来源，前端再用 iframe 套一层
Object.assign(api, {
  previewTicket: (id) => req(`/files/${encodeURIComponent(id)}/preview-ticket`, { method: "POST", body: "{}" }),
  previewUrl: (id, ticket) => {
    if (!ticket) throw new Error("预览授权尚未准备好，请重试。");
    return `${ORIGIN}/ui/files/${encodeURIComponent(id)}/preview?${new URLSearchParams({ embed: "1", ticket })}`;
  },
});

// 设计确认卡（两阶段交付）
Object.assign(api, {
  approveDesign: (taskId) => req(`/designs/${taskId}/approve`, { method: "POST", body: "{}" }),
  rejectDesign: (taskId) => req(`/designs/${taskId}/reject`, { method: "POST", body: "{}" }),
});

// 紧急熔断（cordis 全权限运行时常驻）
Object.assign(api, {
  killRun: (runId) => req(`/runs/${runId}/kill`, { method: "POST", body: "{}" }),
});

// M15 角色库
Object.assign(api, {
  roles: () => req("/roles"),
  createRole: (body) => req("/roles", { method: "POST", body: JSON.stringify(body) }),
});

// M15.1 调人卡
Object.assign(api, {
  requestRole: (chatId, roleId) =>
    req(`/groups/${chatId}/request_role`, { method: "POST", body: JSON.stringify({ role_id: roleId }) }),
  decideRole: (chatId, msgId, approve) =>
    req(`/groups/${chatId}/role_requests/${msgId}/decide`,
        { method: "POST", body: JSON.stringify({ approve }) }),
});

// v1.5 项目
Object.assign(api, {
  projects: () => req("/projects"),
  project: (id) => req(`/projects/${id}`),
  flowTemplates: () => req("/flow-templates"),
  stageDetail: (pid, key) => req(`/projects/${pid}/stages/${key}`),
  assignStage: (pid, key, employee_id) =>
    req(`/projects/${pid}/stages/${key}/assign`, { method: "POST", body: JSON.stringify({ employee_id }) }),
  runDiagnosis: (runId) => req(`/runs/${runId}/diagnosis`),
  // SSE / 下载链接不能用自定义头，服务端同时接受 ?t=<token>（与预览票据同一入口）
  withToken: (path) => {
    const t = auth.get();
    return BASE + path + (t ? (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(t) : "");
  },
  streamUrl: (path) => api.withToken(path),
  stageFileUrl: (pid, key, id) => api.withToken(`/projects/${pid}/stages/${key}/files/${id}`),
});

// 项目级开关（v1.6.1）
Object.assign(api, {
  patchProject: (id, body) => req(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
});

// V2.0
Object.assign(api, {
  patchChat: (id, body) => req(`/chats/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteChat: (id) => req(`/chats/${id}`, { method: "DELETE" }),
  departments: () => req("/departments"),
  createDept: (name, entity_id = "LE-GROUP-HQ") =>
    req("/departments", { method: "POST", body: JSON.stringify({ name, entity_id }) }),
  renameDept: (id, name) => req(`/departments/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  deleteDept: (id) => req(`/departments/${id}`, { method: "DELETE" }),
  patchRole: (id, body) => req(`/roles/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteRole: (id) => req(`/roles/${id}`, { method: "DELETE" }),
  upload: async (chatId, file) => {
    const fd = new FormData(); fd.append("file", file);
    return req(`/chats/${chatId}/attachments`, { method: "POST", body: fd });
  },
});

// V2.2 链路追踪
Object.assign(api, {
  runTrace: (runId) => req(`/runs/${runId}/trace`),
  spanAudit: (spanId) => req(`/traces/spans/${spanId}/audit`),
});

// V2.3 审批中心 / 资源目录 / 越权演练
Object.assign(api, {
  approvals: () => req("/approvals"),
  decideApproval: (id, approve) =>
    req(`/approvals/${id}`, { method: "POST", body: JSON.stringify({ approve }) }),
  resources: () => req("/resources"),
  seedResources: () => req("/resources/seed", { method: "POST", body: "{}" }),
  drill: (body) => req("/resources/drill", { method: "POST", body: JSON.stringify(body || {}) }),
  revoke: (agentId, resourceId, permission) =>
    req("/rbac/revoke", { method: "POST",
      body: JSON.stringify({ agent_id: agentId, resource_id: resourceId, permission }) }),
  grant: (agentId, resourceId, permission) =>
    req("/rbac/grant", { method: "POST",
      body: JSON.stringify({ agent_id: agentId, resource_id: resourceId, permission }) }),
});

// V2.4 FinOps / 评测 / 组织经验
Object.assign(api, {
  finops: (days = 30) => req(`/finops?days=${days}`),
  evals: () => req("/evals"),
  evalBaseline: (runId, name) =>
    req("/evals/baselines", { method: "POST", body: JSON.stringify({ run_id: runId, name }) }),
  evalRegress: (body) =>
    req("/evals/regress", { method: "POST", body: JSON.stringify(body || {}) }),
  evalRunOne: (setId, body) =>
    req(`/evals/${setId}/run`, { method: "POST", body: JSON.stringify(body || {}) }),
  evalBatch: (id) => req(`/evals/batches/${id}`),
  experiences: (kind, state) => req(`/experiences?${new URLSearchParams({ ...(kind ? { kind } : {}), ...(state ? { state } : {}) })}`),
  previewExperience: (text, entity) =>
    req("/experiences/preview", { method: "POST", body: JSON.stringify({ text, entity }) }),
});

Object.assign(api, {
  createExperience: body => req("/experiences", { method: "POST", body: JSON.stringify(body) }),
  editExperience: (id, body) => req(`/experiences/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  reviewExperience: (id, action, note, expected_version) => req(`/experiences/${id}/review`, { method: "POST", body: JSON.stringify({ action, note, expected_version }) }),
  retryProject: (id, requirement) => req(`/projects/${id}/retry`, { method: "POST", body: JSON.stringify({ requirement }) }),
  mcpServers: () => req("/mcp"),
  addMcp: body => req("/mcp", { method: "POST", body: JSON.stringify(body) }),
  patchMcp: (id, body) => req(`/mcp/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteMcp: id => req(`/mcp/${id}`, { method: "DELETE" }),
  testMcp: id => req(`/mcp/${id}/test`, { method: "POST" }),
  mcpBindings: id => req(`/mcp/bindings/${id}`),
  bindMcp: (id, server_ids) => req(`/mcp/bindings/${id}`, { method: "PUT", body: JSON.stringify({ server_ids }) }),
  smtp: () => req("/smtp"),
  saveSmtp: body => req("/smtp", { method: "PUT", body: JSON.stringify(body) }),
  testSmtp: to => req("/smtp/test", { method: "POST", body: JSON.stringify({ to }) }),
  wecomRobot: () => req("/wecom/robot"),
  saveWecomRobot: body => req("/wecom/robot", { method: "PUT", body: JSON.stringify(body) }),
  testWecomRobot: () => req("/wecom/robot/test", { method: "POST" }),
  notifications: () => req("/notifications"),
});
