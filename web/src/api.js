// In the browser the shell is served by FastAPI, so relative URLs just work.
// In the Tauri build the page runs from tauri://localhost, so it must talk to
// the local backend explicitly and carry a Bearer token (cross-site cookies die).
const DESKTOP = location.hostname === "tauri.localhost" || location.protocol === "tauri:";
const ORIGIN = DESKTOP ? "http://127.0.0.1:8077" : "";
const BASE = ORIGIN + "/ui";

const TOKEN_KEY = "oa_token";
const TENANT_KEY = "oa_tenant";
export const auth = {
  get: () => localStorage.getItem(TOKEN_KEY) || "",
  set: (t) => localStorage.setItem(TOKEN_KEY, t),
  tenant: () => localStorage.getItem(TENANT_KEY) || "",
  setTenant: (t) => { if (t) localStorage.setItem(TENANT_KEY, t); },
  clear: () => { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(TENANT_KEY); },
};

const ERROR_TEXT = {
  GateNotPassed: "审批未通过：项目已挂起，等待您的决策",
  StageSkipped: "阶段顺序未满足：请先完成上一阶段",
  NotRetryable: "当前状态不能重试",
  TaskNotFound: "任务不存在或无权访问",
  ProjectNotFound: "项目不存在或无权访问",
  RunNotFound: "运行记录不存在或无权访问",
  TenantMismatch: "租户上下文不匹配，请重新登录",
  NotLoggedIn: "登录已失效，请重新登录",
  BadCredentials: "身份 ID 或密钥不正确",
  ApprovalNotFound: "审批单不存在或无权访问",
  FileNotFound: "文件不存在或已被清理",
  ChatNotFound: "会话不存在或无权访问",
  McpServerNotFound: "MCP 工具不存在",
  EmployeeNotFound: "员工不存在",
  EvalRejected: "评测未启动：请检查预算或基准用例",
  EmptyMessage: "消息不能为空",
  UnknownAction: "这个操作暂不支持",
  BadAction: "操作参数不正确",
};

async function req(path, opts = {}) {
  const headers = { "content-type": "application/json" };
  const token = auth.get();
  if (token) headers.authorization = `Bearer ${token}`;
  const tenant = auth.tenant();
  if (tenant) headers["x-tenant-id"] = tenant;
  const res = await fetch(BASE + path, {
    credentials: DESKTOP ? "omit" : "same-origin",
    ...opts,
    headers: { ...headers, ...(opts.headers || {}) },
  });
  if (res.status === 401) {
    auth.clear();
    throw Object.assign(new Error("unauthorized"), { code: 401 });
  }
  if (!res.ok) {
    let detail = {};
    try { detail = await res.json(); } catch { /* ignore */ }
    const code = detail?.detail?.code;
    throw new Error(ERROR_TEXT[code] || detail?.detail?.message || code || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  me: () => req("/me"),
  login: async (identity_id, secret) => {
    const r = await req("/login", { method: "POST", body: JSON.stringify({ identity_id, secret }) });
    if (r.token) auth.set(r.token);
    if (r.tenant_id) auth.setTenant(r.tenant_id);
    return r;
  },
  logout: async () => {
    try { await req("/logout", { method: "POST" }); } finally { auth.clear(); }
  },
  org: () => req("/org"),
  chats: () => req("/chats"),
  employees: () => req("/employees"),
  createGroup: (name, ids) =>
    req("/groups", { method: "POST", body: JSON.stringify({ name, employee_ids: ids }) }),
  messages: (chatId, after = 0) => req(`/chats/${chatId}/messages?after=${after}`),
  send: (chatId, text) =>
    req(`/chats/${chatId}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  task: (taskId) => req(`/tasks/${taskId}`),
  action: (taskId, action, note) =>
    req(`/tasks/${taskId}/action`, { method: "POST", body: JSON.stringify({ action, note }) }),
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
  fileUrl: (id) => `${ORIGIN}/ui/files/${id}/download`,
  kb: (entity) => req(`/kb${entity ? `?entity=${entity}` : ""}`),
  uploadKb: (entity, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch(`${BASE}/kb?entity=${entity}`, {
      method: "POST",
      headers: auth.get() ? { authorization: `Bearer ${auth.get()}` } : {},
      body: fd,
    }).then((r) => r.json());
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
  previewUrl: (id) => `${ORIGIN}/ui/files/${id}/preview?t=${encodeURIComponent(auth.get())}`,
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
    const r = await fetch(`${BASE}/chats/${chatId}/attachments`, {
      method: "POST", body: fd, credentials: "include",
      headers: auth.get() ? { authorization: `Bearer ${auth.get()}` } : {},
    });
    return r.json();
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
  experiences: (kind, state) => {
    const p = new URLSearchParams();
    if (kind) p.set("kind", kind);
    if (state) p.set("state", state);
    return req(`/experiences${p.toString() ? `?${p}` : ""}`);
  },
  previewExperience: (text, entity) =>
    req("/experiences/preview", { method: "POST", body: JSON.stringify({ text, entity }) }),
});

// V3.0 经验审核 / MCP 工具扩展
Object.assign(api, {
  reviewExperience: (id, action, note) =>
    req(`/experiences/${id}/review`, { method: "POST", body: JSON.stringify({ action, note }) }),
  mcpServers: () => req("/mcp"),
  addMcp: (body) => req("/mcp", { method: "POST", body: JSON.stringify(body) }),
  patchMcp: (id, body) => req(`/mcp/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteMcp: (id) => req(`/mcp/${id}`, { method: "DELETE" }),
  testMcp: (id) => req(`/mcp/${id}/test`, { method: "POST", body: "{}" }),
  mcpBindings: (agentId) => req(`/mcp/bindings/${agentId}`),
  bindMcp: (agentId, server_ids) =>
    req(`/mcp/bindings/${agentId}`, { method: "PUT", body: JSON.stringify({ server_ids }) }),
});

// V3.1 SMTP 真实发件 / 项目与任务重试
Object.assign(api, {
  smtp: () => req("/smtp"),
  saveSmtp: (body) => req("/smtp", { method: "PUT", body: JSON.stringify(body) }),
  testSmtp: (to) => req("/smtp/test", { method: "POST", body: JSON.stringify({ to }) }),
  retryTask: (id) => req(`/tasks/${id}/retry`, { method: "POST", body: "{}" }),
  retryProject: (id) => req(`/projects/${id}/retry`, { method: "POST", body: "{}" }),
  approveGate: (pid, gid) => req(`/projects/${pid}/gates/${gid}/approve`,
                                 { method: "POST", body: "{}" }),
});

Object.assign(api, {
  createExperience: (body) => req("/experiences", { method: "POST", body: JSON.stringify(body) }),
});
