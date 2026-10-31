import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";

const hqIdentity = process.env.AGENT_OS_TEST_ID;
const tenantIdentity = process.env.AGENT_OS_TEST_TENANT_ID;
const subsidiaryIdentity = process.env.AGENT_OS_TEST_SUBSIDIARY_ID;
if (!hqIdentity || !tenantIdentity || !subsidiaryIdentity)
  throw new Error("Set all three synthetic identity variables from tools/serve_ui_test.py.");

// Credentials are disposable test fixtures; never record login request bodies.
test.use({ trace: "off" });

function gate() {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release, started: false, completed: false };
}

async function json(response) {
  expect(response.ok(), `Isolated API returned ${response.status()}`).toBeTruthy();
  return response.json();
}

async function prepareLogin(page, identity) {
  await page.request.get(`/ui/dev-login?as_id=${encodeURIComponent(identity)}`);
  const me = await json(await page.request.get("/ui/me"));
  const secret = `synthetic-boundary-${randomUUID()}-${randomUUID()}`;
  await json(await page.request.post("/ui/account/secret", { data: { secret } }));
  await json(await page.request.post("/ui/logout"));
  return { me, secret };
}

async function navigate(page, name) {
  await page.getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name, exact: true }).click();
}

async function scopedFixture(page, targetMe, lateStatus, failChats) {
  const control = {
    scope: "A", holdMe: false, expireTask: false, expired: false,
    me: gate(), approvals: gate(), optional: gate(), optionalRequests: 0,
    oldScopeRequests: [],
  };
  const scopes = Object.fromEntries(["A", "B"].map(scope => {
    const prefix = `SCOPE-${scope}-PRIVATE`;
    const employee = { id: `AI-session-${scope}`, name: `${prefix}-employee`, status: "online", model: "mock" };
    const chat = {
      id: `C-session-${scope}`, title: `${prefix}-chat`, kind: "dm",
      employee_id: employee.id, employee_name: employee.name,
    };
    const task = {
      id: `T-session-${scope}`, title: `${prefix}-task`, state: "completed",
      owner_name: employee.name, chat_id: chat.id,
      legal_entity_id: scope === "A" ? "LE-GROUP-HQ" : targetMe.entity_id,
    };
    return [scope, { prefix, employee, chat, task }];
  }));
  const respond = (route, body, status = 200) => route.fulfill({ status, json: body });
  const denied = route => respond(route, { detail: { code: "DeploymentAdminOnly" } }, 403);
  const expired = route => respond(route, { detail: { code: "NotLoggedIn" } }, 401);
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (control.scope === "B" && /\/(?:tasks|chats)\/[TC]-session-A(?:\/|$)/.test(path))
      control.oldScopeRequests.push(path);
  });
  await page.route("**/ui/me", async route => {
    if (control.scope !== "A" || !control.holdMe) return route.continue();
    control.holdMe = false;
    // Capture the real A identity before B logs in; only delay its response.
    const response = await route.fetch();
    const oldMe = await json(response);
    expect(oldMe.identity_id).toBe(hqIdentity);
    control.me.started = true;
    await control.me.promise;
    if (lateStatus === 401) await expired(route);
    else await respond(route, oldMe);
    control.me.completed = true;
  });
  await page.route("**/ui/approvals", async route => {
    const scope = control.scope;
    if (scope === "A" && !control.approvals.started) {
      control.approvals.started = true;
      await control.approvals.promise;
      if (lateStatus === 401) await expired(route);
      else await respond(route, {
        pending: Array.from({ length: 7 }, (_, index) => ({
          id: `AP-session-A-${index}`, agent_name: scopes.A.employee.name,
          tool: "write", target: "synthetic-only.txt", state: "pending",
        })), history: [], count: 7,
      });
      control.approvals.completed = true;
      return;
    }
    return respond(route, { pending: [], history: [], count: 0 });
  });
  for (const endpoint of ["chats", "org", "departments"]) {
    await page.route(`**/ui/${endpoint}`, async route => {
      const scope = control.scope;
      if (scope === "B" && endpoint !== "departments") {
        control.optionalRequests += 1;
        control.optional.started = true;
        await control.optional.promise;
      }
      if (endpoint === "departments" || (scope === "B" && endpoint === "chats" && failChats))
        return denied(route);
      const row = scopes[scope];
      return respond(route, endpoint === "chats" ? { chats: [row.chat] } : {
        roots: [{ id: row.task.legal_entity_id, name: `${row.prefix}-organization`,
          type: "subsidiary", children: [], positions: [
            { id: `P-session-${scope}`, title: "合成岗位", employees: [row.employee] },
          ] }],
      });
    });
  }
  await page.route("**/ui/employees", route => respond(route, { employees: [scopes[control.scope].employee] }));
  await page.route("**/ui/files", route => respond(route, { files: [], total: 0 }));
  await page.route("**/ui/tasks?*", route => respond(route, {
    tasks: [scopes[control.scope].task], total: 1, counts: { completed: 1 }, offset: 0, limit: 20,
  }));
  await page.route(/\/ui\/tasks\/T-session-[AB]$/, async route => {
    if (control.expireTask && route.request().url().endsWith("/T-session-A")) {
      control.expireTask = false;
      control.expired = true;
      return expired(route);
    }
    const scope = route.request().url().endsWith("/T-session-A") ? "A" : "B";
    return respond(route, {
      task: scopes[scope].task, runs: [], criteria: [], artifacts: [], stream: [],
      requirement: `${scopes[scope].prefix}-requirement`, allowed_actions: [],
    });
  });
  await page.route(/\/ui\/chats\/C-session-[AB]\/messages(?:\?.*)?$/, route => respond(route, { messages: [] }));
  await page.route(/\/ui\/chats\/C-session-[AB]\/stream(?:\?.*)?$/, route =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: ": synthetic stream\n\n" }));
  return {
    control, scopes,
    releaseAll() { control.me.release(); control.approvals.release(); control.optional.release(); },
  };
}

async function watchForOldScope(page, prefix) {
  await page.evaluate(prefix => {
    window.__sessionBoundaryLeaks = [];
    const inspect = () => {
      const oldText = document.body.textContent.includes(prefix);
      const oldDraft = [...document.querySelectorAll("input,textarea")]
        .some(element => element.value.includes(prefix));
      const oldApproval = [...document.querySelectorAll(".rail-badge")]
        .some(element => element.textContent.trim() === "7");
      if ((oldText || oldDraft || oldApproval) && window.__sessionBoundaryLeaks.length < 10)
        window.__sessionBoundaryLeaks.push({ oldText, oldDraft, oldApproval });
    };
    new MutationObserver(inspect).observe(document.body, { subtree: true, childList: true, characterData: true });
    inspect();
  }, prefix);
}

async function expectCleanB(page, fixture, targetMe, originalUrl, marker) {
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await expect(page.getByTitle("账号信息", { exact: true })).toContainText(targetMe.name);
  await expect(page.locator("body")).not.toContainText(fixture.scopes.A.prefix);
  await expect(page.locator(".task-inspector")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "进入控制台", exact: true })).toHaveCount(0);
  await expect(page.locator(".rail-badge")).toHaveCount(0);
  await expect(page).toHaveURL(originalUrl);
  expect(await page.evaluate(() => window.__sessionBoundaryDocument)).toBe(marker);
  expect(await page.evaluate(() => window.__sessionBoundaryLeaks)).toEqual([]);
  expect(fixture.control.oldScopeRequests).toEqual([]);
  expect((await json(await page.request.get("/ui/me"))).identity_id).toBe(targetMe.identity_id);
}

for (const scenario of [
  { name: "another tenant with failed optional reads and late success", identity: tenantIdentity, lateStatus: 200, failChats: true },
  { name: "another tenant with delayed optional reads and late 401", identity: tenantIdentity, lateStatus: 401, failChats: false },
  { name: "another entity in the same tenant with late success", identity: subsidiaryIdentity, lateStatus: 200, failChats: false },
]) {
  test(`same-page login isolates ${scenario.name}`, async ({ page }) => {
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const { me: targetMe, secret } = await prepareLogin(page, scenario.identity);
    const fixture = await scopedFixture(page, targetMe, scenario.lateStatus, scenario.failChats);
    try {
      await page.goto(`/ui/dev-login?as_id=${encodeURIComponent(hqIdentity)}&tab=overview`);
      const originalUrl = page.url();
      const marker = randomUUID();
      await page.evaluate(value => { window.__sessionBoundaryDocument = value; }, marker);
      await expect(page.getByText(fixture.scopes.A.task.title, { exact: true })).toBeVisible();
      await expect(page.locator(".team-member")).toContainText(fixture.scopes.A.employee.name);
      await expect.poll(() => fixture.control.approvals.started).toBe(true);
      await navigate(page, "协作会话");
      await expect(page.locator(".main .topbar .who")).toHaveText(fixture.scopes.A.chat.title);
      await page.getByRole("textbox", { name: "任务内容", exact: true }).fill(`${fixture.scopes.A.prefix}-unsent-draft`);
      await navigate(page, "工作台");
      await expect(page.getByRole("button", { name: "重新加载", exact: true })).toBeVisible();
      fixture.control.holdMe = true;
      await page.getByRole("button", { name: "重新加载", exact: true }).click();
      await expect.poll(() => fixture.control.me.started).toBe(true);
      await page.getByRole("textbox", { name: "搜索任务", exact: true }).fill(fixture.scopes.A.task.title);
      const row = page.getByRole("row").filter({ hasText: fixture.scopes.A.task.title });
      await row.getByRole("button", { name: "查看", exact: true }).click();
      const inspector = page.locator(".task-inspector");
      await expect(inspector).toBeVisible();
      await expect(inspector).toContainText(fixture.scopes.A.employee.name);
      await expect(inspector.getByText("已完成", { exact: true })).toBeVisible();

      // A real API 401 activates the expiry path while A's inspector is mounted.
      fixture.control.expireTask = true;
      await page.evaluate(() => window.dispatchEvent(new Event("oa-tasks-changed")));
      await expect.poll(() => fixture.control.expired).toBe(true);
      await expect(page.getByRole("button", { name: "进入控制台", exact: true })).toBeVisible();
      await expect(inspector).toHaveCount(0);
      await watchForOldScope(page, fixture.scopes.A.prefix);
      fixture.control.scope = "B";
      await page.getByPlaceholder("ID-xxxxxxxxxxxx").fill(scenario.identity);
      await page.locator('input[type="password"]').fill(secret);
      await page.getByRole("button", { name: "进入控制台", exact: true }).click();
      await expect.poll(() => fixture.control.optionalRequests).toBeGreaterThanOrEqual(2);
      // B's optional data has not arrived: A must already be fully unmounted.
      await expectCleanB(page, fixture, targetMe, originalUrl, marker);
      fixture.control.optional.release();
      await expect(page.getByRole("status").filter({ hasText: "部分工作空间数据暂时不可用" })).toBeVisible();
      await expect(page.getByText(fixture.scopes.B.task.title, { exact: true })).toBeVisible();
      await expect(page.getByRole("textbox", { name: "搜索任务", exact: true })).toHaveValue("");

      fixture.control.me.release();
      fixture.control.approvals.release();
      await expect.poll(() => fixture.control.me.completed && fixture.control.approvals.completed).toBe(true);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await expectCleanB(page, fixture, targetMe, originalUrl, marker);
      await navigate(page, "组织与团队");
      await expect(page.locator(".org-card")).toContainText(`${fixture.scopes.B.prefix}-organization`);
      await expect(page.locator("body")).not.toContainText(fixture.scopes.A.prefix);
      await navigate(page, "协作会话");
      if (scenario.failChats) {
        await expect(page.locator(".chat-item")).toHaveCount(0);
        await expect(page.getByText("暂无会话，请先添加 AI 员工。", { exact: true })).toBeVisible();
      } else {
        await expect(page.locator(".main .topbar .who")).toHaveText(fixture.scopes.B.chat.title);
        await expect(page.getByRole("textbox", { name: "任务内容", exact: true })).toHaveValue("");
        await expect(page.locator(".chat-item[aria-pressed='true']")).toContainText(fixture.scopes.B.employee.name);
      }
      await expectCleanB(page, fixture, targetMe, originalUrl, marker);
      expect(errors).toEqual([]);
    } finally { fixture.releaseAll(); }
  });
}
