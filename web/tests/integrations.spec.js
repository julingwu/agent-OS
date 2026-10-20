import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

const identity = process.env.AGENT_OS_TEST_ID;
if (!identity) throw new Error("Use the synthetic identity from tools/serve_ui_test.py.");
const created = new WeakMap();
const problems = new WeakMap();

async function json(response) {
  expect(response.ok(), `Isolated API returned ${response.status()}`).toBeTruthy();
  return response.json();
}
async function navigate(page, name) {
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name, exact: true }).click();
}
async function fits(page) {
  const size = await page.evaluate(() => ({ width: innerWidth, body: document.body.scrollWidth, doc: document.documentElement.scrollWidth }));
  expect(size.body).toBeLessThanOrEqual(size.width + 1);
  expect(size.doc).toBeLessThanOrEqual(size.width + 1);
}
async function capture(page, testInfo, name) {
  const target = process.env.AGENT_OS_TEST_SCREENSHOT_DIR
    ? path.resolve(process.env.AGENT_OS_TEST_SCREENSHOT_DIR, name) : testInfo.outputPath(name);
  await fs.mkdir(path.dirname(target), { recursive: true });
  await page.screenshot({ path: target, fullPage: true, animations: "disabled" });
}
test.beforeEach(async ({ page }) => {
  created.set(page, { experiences: [], servers: [] });
  problems.set(page, []);
  page.on("pageerror", error => problems.get(page).push(error.message));
  page.on("response", response => {
    if (new URL(response.url()).pathname.startsWith("/ui/") && response.status() >= 400)
      problems.get(page).push(`${response.request().method()} ${new URL(response.url()).pathname}: ${response.status()}`);
  });
  await page.goto(`/ui/dev-login?as_id=${encodeURIComponent(identity)}&tab=overview`);
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible();
});
test.afterEach(async ({ page }) => {
  for (const id of created.get(page).experiences) {
    const row = (await json(await page.request.get("/ui/experiences"))).experiences.find(row => row.id === id);
    if (row?.state === "pending") await page.request.post(`/ui/experiences/${id}/review`, {
      data: { action: "reject", expected_version: row.version, note: "Browser fixture cleanup" },
    });
  }
  for (const id of created.get(page).servers) await page.request.delete(`/ui/mcp/${id}`);
  expect(problems.get(page), "No runtime or unexpected API errors").toEqual([]);
});

test("experience review, retrieval and edits enforce the pending boundary", async ({ page }) => {
  const title = `浏览器审核 ${Date.now()}`;
  await navigate(page, "组织经验");
  await page.getByRole("textbox", { name: "经验标题", exact: true }).fill(title);
  await page.getByRole("textbox", { name: "经验内容", exact: true }).fill("交付文案应先检查事实来源，再核对名称与日期。");
  await page.getByRole("textbox", { name: "检索标签" }).fill("文案,事实来源");
  await page.getByRole("checkbox", { name: "作为强制规则" }).check();
  await page.getByRole("textbox", { name: "禁止出现的用语" }).fill(`browser_forbidden_${Date.now()}`);
  await page.getByRole("button", { name: "提交待审核", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("待审核");
  const all = await json(await page.request.get("/ui/experiences"));
  const item = all.experiences.find(item => item.title === title);
  expect(item.state).toBe("pending");
  created.get(page).experiences.push(item.id);
  const article = page.locator("article.exp-row").filter({ has: page.getByText(title, { exact: true }) });
  await page.getByRole("textbox", { name: "新任务需求" }).fill("写一份文案，核对事实来源");
  await page.getByRole("button", { name: "预览引用", exact: true }).click();
  await expect(page.locator(".exp-preview")).toContainText("将引用 0 条经验");
  await article.getByRole("textbox", { name: `审核说明：${title}`, exact: true }).fill("规则清楚，可用于后续任务");
  await article.getByRole("button", { name: "审核通过", exact: true }).click();
  await expect(article.locator(".pill")).toHaveText("已生效");
  await page.getByRole("button", { name: "预览引用", exact: true }).click();
  await expect(page.locator(".exp-preview")).toContainText("交付文案应先检查事实来源");
  await article.getByRole("button", { name: "编辑经验", exact: true }).click();
  await page.getByRole("textbox", { name: "经验内容", exact: true }).fill("修改规则之后，应再次人工审核。");
  await page.getByRole("button", { name: "提交待审核", exact: true }).click();
  await expect(article.locator(".pill")).toHaveText("待审核");
  const revised = (await json(await page.request.get("/ui/experiences"))).experiences.find(row => row.id === item.id);
  expect(revised.version).toBe(2);
  await page.getByRole("button", { name: "预览引用", exact: true }).click();
  await expect(page.locator(".exp-preview")).toContainText("将引用 0 条经验");
});

test("tool binding and SMTP configuration save without exposing credentials or sending mail", async ({ page }) => {
  const name = `浏览器工具 ${Date.now()}`;
  await navigate(page, "工具与集成");
  await page.getByRole("textbox", { name: "连接名称" }).fill(name);
  await page.getByRole("textbox", { name: "服务地址" }).fill("https://tools.example.invalid/mcp");
  await page.getByLabel("访问密钥", { exact: true }).fill("synthetic-browser-tool-credential");
  await page.getByRole("button", { name: "添加连接", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("工具连接已保存");
  const server = (await json(await page.request.get("/ui/mcp"))).servers.find(row => row.name === name);
  created.get(page).servers.push(server.id);
  expect(server.has_key).toBe(true);
  expect(server.api_key).toBeUndefined();
  expect(await page.getByLabel("访问密钥", { exact: true }).inputValue()).toBe("");
  const selected = await page.getByLabel("选择员工").inputValue();
  expect(selected).toBeTruthy();
  await page.getByRole("checkbox", { name, exact: true }).check();
  await page.getByRole("button", { name: "保存员工授权", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("员工工具授权已保存");
  expect((await json(await page.request.get(`/ui/mcp/bindings/${selected}`))).server_ids).toContain(server.id);
  const card = page.locator("article.integration-item").filter({ has: page.getByText(name, { exact: true }) });
  await card.getByRole("button", { name: "停用", exact: true }).click();
  await expect(card.locator(".pill")).toHaveText("已停用");
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByRole("textbox", { name: "服务地址" }).fill("https://different.example.invalid/mcp");
  await page.getByRole("button", { name: "保存连接", exact: true }).click();
  await expect(card).toContainText("未设置访问凭据");
  await page.getByRole("textbox", { name: "SMTP 服务器" }).fill("smtp.example.invalid");
  await page.getByRole("spinbutton", { name: "端口", exact: true }).fill("587");
  await page.getByRole("textbox", { name: "登录账号" }).fill("browser@example.invalid");
  await page.getByRole("textbox", { name: "发件人地址" }).fill("browser@example.invalid");
  await page.getByLabel("邮箱授权码", { exact: true }).fill("synthetic-browser-mail-credential");
  await page.getByRole("button", { name: "保存邮件配置", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("邮件配置已保存");
  const settings = await json(await page.request.get("/ui/smtp"));
  expect(settings.pass_set).toBe(true);
  expect(settings.password).toBeUndefined();
  expect(settings.gateway_delivery).toBe("outbox_only");
  await expect(page.getByLabel("邮箱授权码", { exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "发送测试邮件", exact: true })).toBeDisabled();
});

for (const width of [390, 768, 1440]) {
  test.describe(`${width}px integration surfaces`, () => {
    test.use({ viewport: { width, height: width === 390 ? 844 : 1000 } });
    test("governance and tool forms remain usable", async ({ page }, testInfo) => {
      await navigate(page, "组织经验");
      await expect(page.getByRole("textbox", { name: "经验标题", exact: true })).toBeVisible();
      await fits(page);
      await capture(page, testInfo, `governance-${width}.png`);
      await navigate(page, "工具与集成");
      await expect(page.getByRole("textbox", { name: "连接名称" })).toBeVisible();
      await fits(page);
      await capture(page, testInfo, `integrations-${width}.png`);
    });
  });
}
