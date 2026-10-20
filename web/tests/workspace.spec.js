import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

const identity = process.env.AGENT_OS_TEST_ID;
if (!identity?.trim()) {
  throw new Error("Set AGENT_OS_TEST_ID to the test_identity_id printed by tools/serve_ui_test.py.");
}
const runtimeProblems = new WeakMap();
const expectedApiProblems = new WeakMap();

async function json(response) {
  expect(response.ok(), `Isolated test API returned ${response.status()}`).toBeTruthy();
  return response.json();
}

async function taskView(page, id) {
  return json(await page.request.get(`/ui/tasks/${encodeURIComponent(id)}`));
}

async function waitForState(page, id, state) {
  await expect.poll(async () => (await taskView(page, id)).task.state, {
    message: `Task should persist as ${state}`,
    timeout: 60_000,
    intervals: [250, 500, 1000],
  }).toBe(state);
}

async function noHorizontalOverflow(page) {
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    body: document.body.scrollWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(dimensions.body, "Body must fit the viewport").toBeLessThanOrEqual(dimensions.viewport + 1);
  expect(dimensions.document, "Document must fit the viewport").toBeLessThanOrEqual(dimensions.viewport + 1);
}

async function visibleControl(page, locator) {
  await expect(locator).toBeVisible();
  await expect(locator).toBeInViewport();
  const bounds = await locator.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds.x).toBeGreaterThanOrEqual(-1);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(page.viewportSize().width + 1);
}

async function navigate(page, name) {
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name, exact: true }).click();
  await expect(page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name, exact: true })).toHaveAttribute("aria-current", "page");
}

async function saveScreenshot(page, testInfo, name) {
  const directory = process.env.AGENT_OS_TEST_SCREENSHOT_DIR;
  const target = directory ? path.resolve(directory, name) : testInfo.outputPath(name);
  await fs.mkdir(path.dirname(target), { recursive: true });
  await page.screenshot({ path: target, fullPage: true, animations: "disabled", type: "png" });
}

test.beforeEach(async ({ page }) => {
  const problems = [];
  runtimeProblems.set(page, problems);
  expectedApiProblems.set(page, new Set());
  page.on("pageerror", error => problems.push(`Page error: ${error.message}`));
  page.on("response", response => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/ui/") && response.status() >= 400) {
      const problem = `${response.request().method()} ${url.pathname}: ${response.status()}`;
      if (!expectedApiProblems.get(page).has(problem)) problems.push(problem);
    }
  });
  await page.goto(`/ui/dev-login?as_id=${encodeURIComponent(identity)}&tab=overview`);
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible();
  const me = await json(await page.request.get("/ui/me"));
  expect(me.identity_id, "Use only the synthetic test identity").toBe(identity);
  const settings = await json(await page.request.get("/ui/settings"));
  expect(settings.api_key_set, "Browser regression must run with model keys removed").toBe(false);
});

test.afterEach(async ({ page }) => {
  expect(runtimeProblems.get(page), "No page errors or unexpected API 4xx/5xx responses").toEqual([]);
});

for (const width of [390, 768, 1440]) {
  test.describe(`${width}px responsive workspace`, () => {
    test.use({ viewport: { width, height: width === 390 ? 844 : 1000 } });

    test("workspace, task center, roles, and chat keep their primary controls", async ({ page }, testInfo) => {
      await expect(page.locator(".metric-grid")).toHaveAttribute("aria-busy", "false");
      await visibleControl(page, page.locator(".page-heading").getByRole("button", { name: "新建任务" }));
      await noHorizontalOverflow(page);
      const screenshotDirectory = process.env.AGENT_OS_TEST_SCREENSHOT_DIR;
      const screenshotPath = screenshotDirectory ? path.resolve(screenshotDirectory, `ui-${width}.png`) : testInfo.outputPath(`ui-${width}.png`);
      await fs.mkdir(path.dirname(screenshotPath), { recursive: true });
      await page.screenshot({ path: screenshotPath, fullPage: true, animations: "disabled" });

      await navigate(page, "任务中心");
      await expect(page.getByRole("heading", { name: "任务中心", exact: true })).toBeVisible();
      await visibleControl(page, page.locator(".page-heading").getByRole("button", { name: "新建任务" }));
      await visibleControl(page, page.getByRole("textbox", { name: "搜索任务" }));
      await noHorizontalOverflow(page);

      await navigate(page, "角色库");
      await expect(page.locator(".right .roles")).toBeVisible();
      await expect(page.locator(".rail .roles")).toHaveCount(0);
      await visibleControl(page, page.locator(".right").getByRole("button", { name: "新建角色" }));
      await expect(page.locator(".role-card").first()).toBeVisible();
      await noHorizontalOverflow(page);

      await navigate(page, "协作会话");
      await visibleControl(page, page.getByRole("textbox", { name: "任务内容" }));
      await visibleControl(page, page.getByRole("button", { name: "发送", exact: true }));
      if (width <= 960) {
        const picker = page.getByRole("combobox", { name: "选择协作会话" });
        await visibleControl(page, picker);
        const choices = await picker.locator("option").evaluateAll(options => options.map(option => ({ value: option.value, label: option.label })));
        expect(choices.length).toBeGreaterThan(1);
        const choice = choices.find(option => /文案/.test(option.label)) || choices[1];
        await picker.selectOption(choice.value);
        await expect(picker).toHaveValue(choice.value);
        await expect(page.locator(".topbar .who")).toContainText(choice.label);
      } else {
        await expect(page.getByRole("complementary", { name: "协作会话列表" })).toBeVisible();
      }
      await noHorizontalOverflow(page);
    });
  });
}

test("new task dialog traps Tab, closes on Escape, and restores focus", async ({ page }) => {
  const opener = page.locator(".page-heading").getByRole("button", { name: "新建任务" });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "创建新任务" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  await expect.poll(() => dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
  const first = dialog.getByRole("button", { name: "关闭新建任务" });
  const last = dialog.getByRole("button", { name: "取消", exact: true });
  await first.focus();
  await page.keyboard.press("Shift+Tab");
  await expect(last).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(first).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test("150 percent page zoom and reduced motion keep navigation and the task dialog usable", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.evaluate(() => { document.documentElement.style.zoom = "1.5"; });
  await visibleControl(page, page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "任务中心", exact: true }));
  await navigate(page, "任务中心");
  await expect(page.getByRole("heading", { name: "任务中心", exact: true })).toBeVisible();
  const opener = page.locator(".page-heading").getByRole("button", { name: "新建任务" });
  await visibleControl(page, opener);
  await noHorizontalOverflow(page);
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "创建新任务" });
  await expect(dialog).toBeVisible();
  await visibleControl(page, dialog.getByLabel("任务需求"));
  await dialog.getByLabel("任务需求").fill("放大页面后，仍能阅读任务表单并使用键盘关闭。");
  await visibleControl(page, dialog.getByRole("button", { name: "创建并执行" }));
  await expect(dialog.getByRole("button", { name: "创建并执行" })).toBeEnabled();
  await page.keyboard.press("Tab");
  await expect.poll(() => dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  await noHorizontalOverflow(page);
});

test("task pagination shows loading and retry after failure, and late search results cannot replace the latest query", async ({ page }) => {
  let releaseFailure, releaseSlow;
  const holdFailure = new Promise(resolve => { releaseFailure = resolve; });
  const holdSlow = new Promise(resolve => { releaseSlow = resolve; });
  let secondPageRequests = 0, slowRequested = false;
  const task = (name, index = 0) => ({
    id: `BROWSER-${name}-${index}`, title: `${name} 任务 ${index}`, state: "completed",
    owner_name: "合成数据", created_at: "2026-10-20 00:00:00",
  });
  // This single HTTP 503 is deliberately injected by the regression scenario.
  expectedApiProblems.get(page).add("GET /ui/tasks: 503");
  await page.route("**/ui/tasks?*", async route => {
    const parameters = new URL(route.request().url()).searchParams;
    const query = parameters.get("q");
    const offset = Number(parameters.get("offset") || 0);
    if (query === "slow") {
      slowRequested = true;
      await holdSlow;
    }
    if (!query && offset === 20 && ++secondPageRequests === 1) {
      await holdFailure;
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: { message: "分页回归：第二页暂时不可用" } }) });
    }
    const tasks = query ? [task(query)] : Array.from({ length: 20 }, (_, index) => task(offset ? "第二页" : "第一页", index));
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tasks, counts: { completed: 41 }, total: query ? 1 : 41, limit: 20, offset }) });
  });
  try {
    await navigate(page, "任务中心");
    const table = page.getByRole("table", { name: "任务列表" });
    const completedMetric = page.locator(".metric-card").filter({ hasText: "已完成任务" }).locator(".metric-value");
    await expect(table.getByText("第一页 任务 0", { exact: true })).toBeVisible();
    await expect(completedMetric).toContainText("41");
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await expect.poll(() => secondPageRequests).toBe(1);
    await expect(table).toHaveAttribute("aria-busy", "true");
    await expect(table.locator(".task-table-row")).toHaveCount(0);
    await expect(table.getByRole("status")).toContainText("正在获取任务");
    await expect(page.locator(".pagination")).toContainText("正在加载第 2 页");
    await expect(completedMetric).toContainText("41");
    await expect(page.locator(".metric-grid .skeleton-number")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "下一页", exact: true })).toBeDisabled();
    releaseFailure();
    await expect(page.getByRole("alert")).toContainText("第二页暂时不可用");
    await expect(table).toContainText("任务列表加载失败");
    await expect(table.locator(".task-table-row")).toHaveCount(0);
    await expect(table.getByText(/从第一项任务开始|当前分类暂无任务|没有找到匹配的任务/)).toHaveCount(0);
    await expect(page.locator(".pagination")).toContainText("第 2 页加载失败");
    await expect(completedMetric).toContainText("41");
    await page.getByRole("alert").getByRole("button", { name: "重试", exact: true }).click();
    await expect(table.getByText("第二页 任务 0", { exact: true })).toBeVisible();
    await expect(page.locator(".pagination")).toContainText("共 41 项 · 第 2 页");
    await expect(page.getByRole("alert")).toHaveCount(0);

    const search = page.getByRole("textbox", { name: "搜索任务" });
    await search.fill("slow");
    await expect.poll(() => slowRequested).toBe(true);
    await expect(table.locator(".task-table-row")).toHaveCount(0);
    await search.fill("fast");
    await expect(table.getByText("fast 任务 0", { exact: true })).toBeVisible();
    await expect(page.locator(".pagination")).toContainText("共 1 项 · 第 1 页");
    const slowResponse = page.waitForResponse(response => new URL(response.url()).searchParams.get("q") === "slow");
    releaseSlow();
    await (await slowResponse).finished();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(table.getByText("fast 任务 0", { exact: true })).toBeVisible();
    await expect(table.getByText("slow 任务 0", { exact: true })).toHaveCount(0);
    await expect(completedMetric).toContainText("41");
    await expect(page.locator(".metric-grid .skeleton-number")).toHaveCount(0);
  } finally {
    releaseFailure(); releaseSlow();
  }
});

test("all management navigation opens working content without runtime or API errors", async ({ page }) => {
  for (const [name, heading] of [
    ["组织与团队", "组织架构"],
    ["审批中心", "审批中心"],
    ["用量与费用", "财务总览 · FinOps"],
    ["评测中心", "离线评测与回归"],
    ["组织经验", "组织经验"],
    ["工具与集成", "工具与集成"],
    ["工作产物", "文件库"],
  ]) {
    await test.step(name, async () => {
      await navigate(page, name);
      await expect(page.locator(".right .topbar .who")).toHaveText(heading);
      await expect.poll(() => page.locator(".right").innerText()).not.toMatch(/^(载入中|正在加载)[…\s]*$/);
      await expect(page.locator(".right .stream, .right .main").first()).toBeVisible();
      await noHorizontalOverflow(page);
    });
  }
});

test("keyboard Enter and Space open the attachment chooser and uploads persist after reload", async ({ page }) => {
  await navigate(page, "协作会话");
  await page.locator(".sidebar .chat-item").filter({ hasText: "资深文案" }).click();
  const name = `browser-upload-${Date.now()}.csv`;
  const bytes = Buffer.from("department,total\nMarketing,1200\nEngineering,3000\n", "utf8");
  const uploadButton = page.getByRole("button", { name: "上传附件", exact: true });
  await uploadButton.focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await expect(uploadButton).toBeFocused();
  const uploaded = page.waitForResponse(response => /\/ui\/chats\/[^/]+\/attachments$/.test(new URL(response.url()).pathname) && response.request().method() === "POST");
  const chooserPromise = page.waitForEvent("filechooser");
  await page.keyboard.press("Enter");
  await (await chooserPromise).setFiles({ name, mimeType: "text/csv", buffer: bytes });
  const result = await json(await uploaded);
  expect(result.bytes).toBe(bytes.length);
  expect(result.chars).toBeGreaterThan(0);
  await expect(page.locator(".stream").getByText(`[附件] ${name}`, { exact: true })).toBeVisible();
  await expect(uploadButton).toBeEnabled();
  await uploadButton.focus();
  const secondChooser = page.waitForEvent("filechooser");
  await page.keyboard.press("Space");
  const secondUploaded = page.waitForResponse(response => /\/attachments$/.test(new URL(response.url()).pathname) && response.request().method() === "POST");
  const secondName = name.replace(".csv", ".txt");
  await (await secondChooser).setFiles({ name: secondName, mimeType: "text/plain", buffer: Buffer.from("Keyboard attachment regression\n") });
  expect((await secondUploaded).ok()).toBeTruthy();
  await expect(page.locator(".stream").getByText(`[附件] ${secondName}`, { exact: true })).toBeVisible();
  await page.reload();
  await navigate(page, "协作会话");
  await page.locator(".sidebar .chat-item").filter({ hasText: "资深文案" }).click();
  await expect(page.locator(".stream").getByText(`[附件] ${name}`, { exact: true })).toBeVisible();
  await expect(page.locator(".stream").getByText(`[附件] ${secondName}`, { exact: true })).toBeVisible();
});

test("parked tasks show authorized approval routes and update after resume or rejection", async ({ page }) => {
  const chats = (await json(await page.request.get("/ui/chats"))).chats;
  const chat = chats.find(item => item.kind === "dm" && /文案/.test(item.employee_name || item.title));
  expect(chat).toBeTruthy();
  let phase = "parked", approvalVisible = true;
  const pausedTask = () => {
    const waiting = phase === "parked";
    return {
      id: "T-browser-approval", title: "需要审批的合成文案任务", legal_entity_id: "LE-GROUP-HQ",
      owner_name: "资深文案", chat_id: chat.id, state: phase === "rejected" ? "failed" : "in_progress",
      latest_run: { id: "RUN-browser-approval", run_seq: 1, runner: "mock_acp", usage_tokens: 420, state: waiting ? "awaiting_approval" : phase === "rejected" ? "failed" : "running" },
      awaiting_approval: waiting, pending_approval: waiting && approvalVisible ? { id: "AP-browser", state: "pending" } : null,
      allowed_actions: phase === "rejected" ? ["retry"] : ["abort"],
    };
  };
  const respond = (route, body) => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/ui/tasks?*", route => {
    const task = pausedTask();
    return respond(route, { tasks: [task], counts: { [task.state]: 1 }, total: 1, limit: 20, offset: 0 });
  });
  await page.route("**/ui/tasks/T-browser-approval", route => {
    const task = pausedTask();
    return respond(route, { task, ...{ latest_run: task.latest_run, awaiting_approval: task.awaiting_approval, pending_approval: task.pending_approval },
      runs: [task.latest_run], criteria: [], stream: [], artifacts: [], owner: { id: chat.employee_id, preset: "standard" } });
  });
  await page.route(`**/ui/chats/${chat.id}/messages?*`, route => respond(route, { messages: [
    { id: 999001, chat_id: chat.id, role: "agent", author: "资深文案", kind: "card", task_id: "T-browser-approval", content: "等待所需操作审批后继续。", meta: "{}" },
  ] }));
  await page.route(/\/ui\/approvals(?:\?.*)?$/, route => respond(route, {
    pending: phase === "parked" ? [{ id: "AP-browser", state: "pending", agent_name: "资深文案", agent_id: chat.employee_id, tool: "write", resource_name: "合成测试资源", run_id: "RUN-browser-approval", task_id: "T-browser-approval", target: "synthetic/article.md" }] : [], history: [], count: phase === "parked" ? 1 : 0,
  }));
  await page.route("**/ui/approvals/AP-browser", route => {
    const approved = route.request().postDataJSON().approve;
    phase = approved ? "resumed" : "rejected";
    return respond(route, { ok: true, state: approved ? "approved" : "rejected", resumable: approved });
  });
  const openTask = async () => {
    await navigate(page, "任务中心");
    const row = page.getByRole("row").filter({ hasText: "需要审批的合成文案任务" });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "查看", exact: true }).click();
    return page.getByRole("dialog").filter({ has: page.locator(".task-inspector-meta", { hasText: "T-browser-approval" }) });
  };
  let inspector = await openTask();
  await expect(page.getByRole("row").filter({ hasText: "需要审批的合成文案任务" })).toContainText("待审批");
  await expect(inspector.getByText("待审批", { exact: true })).toBeVisible();
  await expect(inspector.locator(".spin")).toHaveCount(0);
  await inspector.getByRole("button", { name: "前往审批中心", exact: true }).click();
  await expect(inspector).toHaveCount(0);
  await expect(page.locator(".right .topbar .who")).toHaveText("审批中心");
  await page.getByRole("button", { name: "允许并断点续跑", exact: true }).click();
  inspector = await openTask();
  await expect(inspector.getByText("执行中", { exact: true })).toBeVisible();
  await expect(inspector.getByRole("button", { name: "前往审批中心", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");

  phase = "parked";
  await navigate(page, "协作会话");
  await page.locator(".sidebar .chat-item").filter({ hasText: "资深文案" }).click();
  const showDetail = page.getByRole("button", { name: "显示运行详情", exact: true });
  if (await showDetail.isVisible()) await showDetail.click();
  const detail = page.getByRole("complementary", { name: "运行详情" });
  await expect(detail.locator(".head .pill")).toHaveText("待审批");
  await expect(detail.locator(".spin")).toHaveCount(0);
  await expect(page.locator(".right .stream .card").getByText("待审批", { exact: true })).toBeVisible();
  await detail.getByRole("button", { name: "前往审批中心", exact: true }).click();
  await page.getByRole("button", { name: "拒绝", exact: true }).click();
  inspector = await openTask();
  await expect(inspector.getByText("执行失败", { exact: true })).toBeVisible();
  await expect(inspector.getByRole("button", { name: "修改后重试", exact: true })).toBeVisible();
  await expect(inspector.locator(".spin")).toHaveCount(0);
  await page.keyboard.press("Escape");
  phase = "parked"; approvalVisible = false;
  await navigate(page, "工作台");
  inspector = await openTask();
  await expect(inspector.getByText("待审批", { exact: true })).toBeVisible();
  await expect(inspector.getByRole("button", { name: "前往审批中心", exact: true })).toHaveCount(0);
});

test("create, inspect, reject, retry, accept, and reload a mock content task", async ({ page }, testInfo) => {
  const nonce = `浏览器回归${Date.now()}`;
  const previousTasks = new Set((await json(await page.request.get("/ui/tasks?limit=200"))).tasks.map(task => task.id));
  await page.locator(".page-heading").getByRole("button", { name: "新建任务" }).click();
  const createDialog = page.getByRole("dialog", { name: "创建新任务" });
  await createDialog.getByLabel("交给谁来完成").selectOption({ label: "资深文案 · 直接执行任务" });
  await createDialog.getByLabel("任务需求").fill(`写一篇秋日护肤的公众号文案，600-1000字，输出 article.md。任务标记：${nonce}`);
  const sent = page.waitForResponse(response => /\/ui\/chats\/[^/]+\/messages$/.test(new URL(response.url()).pathname) && response.request().method() === "POST");
  await createDialog.getByRole("button", { name: "创建并执行" }).click();
  expect((await sent).ok()).toBeTruthy();
  await expect(createDialog).toHaveCount(0);
  let id;
  await expect.poll(async () => {
    const tasks = (await json(await page.request.get("/ui/tasks?limit=200"))).tasks;
    id = tasks.find(task => !previousTasks.has(task.id) && task.title.includes(nonce))?.id;
    return Boolean(id);
  }, { timeout: 30_000, intervals: [250, 500, 1000] }).toBe(true);
  await waitForState(page, id, "pending_review");
  const firstView = await taskView(page, id);
  expect(firstView.runs[0].runner).toBe("mock_acp");
  expect(firstView.artifacts.length).toBeGreaterThan(0);

  await navigate(page, "任务中心");
  await page.getByRole("textbox", { name: "搜索任务" }).fill(nonce);
  const row = page.getByRole("row").filter({ hasText: nonce });
  await expect(row).toHaveCount(1);
  await row.getByRole("button", { name: "查看" }).click();
  const inspector = page.getByRole("dialog").filter({ has: page.locator(".task-inspector-meta", { hasText: id }) });
  await expect(inspector).toBeVisible();
  await expect(inspector.getByRole("button", { name: "验收通过", exact: true })).toBeVisible();
  await saveScreenshot(page, testInfo, "ui-task-inspector.png");
  await inspector.getByRole("button", { name: "预览", exact: true }).first().click();
  const preview = page.locator(".modal.preview");
  await expect(preview).toHaveAttribute("role", "dialog");
  await expect(preview.frameLocator("iframe").locator("body")).toContainText("秋日护肤");
  await saveScreenshot(page, testInfo, "ui-task-preview.png");
  await preview.frameLocator("iframe").locator("body").click({ position: { x: 15, y: 15 } });
  await expect(preview.locator("iframe")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(preview).toHaveCount(0);
  await expect(inspector).toBeVisible();
  await inspector.getByRole("button", { name: "预览", exact: true }).first().click();
  await expect(preview).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await preview.getByRole("link", { name: "下载", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("article.md");
  expect(await download.failure()).toBeNull();
  expect(await fs.readFile(await download.path(), "utf8")).toContain("秋日护肤");
  await page.keyboard.press("Escape");
  await expect(preview).toHaveCount(0);
  await expect(inspector).toBeVisible();

  await inspector.getByRole("button", { name: "驳回", exact: true }).click();
  const rejectDialog = page.getByRole("dialog", { name: "驳回任务" });
  await expect(rejectDialog).toBeVisible();
  await expect(rejectDialog.getByRole("button", { name: "确认驳回" })).toBeDisabled();
  await rejectDialog.getByLabel("驳回原因").fill("请保持秋日护肤主题，并明确温和清洁和保湿步骤。");
  await rejectDialog.getByRole("button", { name: "确认驳回" }).click();
  await expect(rejectDialog).toHaveCount(0);
  await waitForState(page, id, "rejected");
  await inspector.getByRole("button", { name: "修改后重试", exact: true }).click();
  const retryDialog = page.getByRole("dialog", { name: "修改后重试" });
  await retryDialog.getByLabel("补充修改要求（可选）").fill("保留主题，明确护肤步骤并重新交付。");
  await retryDialog.getByRole("button", { name: "开始重试" }).click();
  await expect(retryDialog).toHaveCount(0);
  await waitForState(page, id, "pending_review");
  const retried = await taskView(page, id);
  expect(retried.runs.length).toBe(firstView.runs.length + 1);
  expect(retried.runs[0].runner).toBe("mock_acp");

  await inspector.getByRole("button", { name: "验收通过", exact: true }).click();
  const acceptDialog = page.getByRole("dialog", { name: "确认验收" });
  await acceptDialog.getByLabel("验收说明（可选）").fill("已预览及下载文案，内容可读且符合秋日护肤任务要求。");
  await acceptDialog.getByRole("button", { name: "确认验收", exact: true }).click();
  await expect(acceptDialog).toHaveCount(0);
  await waitForState(page, id, "completed");
  await expect(inspector.getByText("验收已通过，任务已完成")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await navigate(page, "任务中心");
  await page.getByRole("textbox", { name: "搜索任务" }).fill(nonce);
  const persisted = page.getByRole("row").filter({ hasText: nonce });
  await expect(persisted).toContainText("已完成");
  await persisted.getByRole("button", { name: "查看" }).click();
  await expect(page.getByText("验收已通过，任务已完成")).toBeVisible();
  expect((await taskView(page, id)).task.state).toBe("completed");
  await page.getByRole("button", { name: "关闭任务详情" }).click();
  await navigate(page, "工作台");
  await expect(page.getByRole("row").filter({ hasText: nonce })).toBeVisible();
  await saveScreenshot(page, testInfo, "ui-populated-1440.png");
  await page.setViewportSize({ width: 390, height: 844 });
  await navigate(page, "任务中心");
  await expect(page.getByRole("row").filter({ hasText: nonce })).toBeVisible();
  await noHorizontalOverflow(page);
  await saveScreenshot(page, testInfo, "ui-populated-390.png");
});
