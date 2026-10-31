import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";

const hqIdentity = process.env.AGENT_OS_TEST_ID;
const tenantIdentity = process.env.AGENT_OS_TEST_TENANT_ID;
const subsidiaryIdentity = process.env.AGENT_OS_TEST_SUBSIDIARY_ID;
if (!hqIdentity || !tenantIdentity || !subsidiaryIdentity) {
  throw new Error("Set all three synthetic identity variables from tools/serve_ui_test.py.");
}
// The form fixture uses only a disposable synthetic credential.
test.use({ trace: "off" });

async function json(response) {
  expect(response.ok(), `Isolated API returned ${response.status()}`).toBeTruthy();
  return response.json();
}

async function login(page, identity) {
  await page.goto(`/ui/dev-login?as_id=${encodeURIComponent(identity)}&tab=overview`);
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  const me = await json(await page.request.get("/ui/me"));
  expect(me.identity_id).toBe(identity);
  return me;
}

async function navigate(page, name) {
  await page.getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name, exact: true }).click();
}

test("another tenant stays signed in across reload and sees only its tasks and organization", async ({ page }) => {
  const errors = [];
  const apiErrors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.request.get(`/ui/dev-login?as_id=${encodeURIComponent(tenantIdentity)}`);
  const secret = `synthetic-tenant-${randomUUID()}-${randomUUID()}`;
  await json(await page.request.post("/ui/account/secret", { data: { secret } }));
  await json(await page.request.post("/ui/logout"));
  await page.goto("/app/");
  await page.getByPlaceholder("ID-xxxxxxxxxxxx").fill(tenantIdentity);
  await page.locator('input[type="password"]').fill(secret);
  page.on("response", response => {
    if (new URL(response.url()).pathname.startsWith("/ui/") && response.status() >= 400)
      apiErrors.push(`${response.status()} ${new URL(response.url()).pathname}`);
  });
  await page.getByRole("button", { name: "进入控制台", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  const me = await json(await page.request.get("/ui/me"));
  expect(me.identity_id).toBe(tenantIdentity);
  expect(me.tenant_id).toBe("TENANT-UI-BETA");
  expect(me.can_manage_deployment).toBe(false);
  await expect(page.getByRole("button", { name: "工作空间设置", exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible();
  expect((await json(await page.request.get("/ui/me"))).identity_id).toBe(tenantIdentity);
  await navigate(page, "任务中心");
  await expect(page.getByText("隔离租户专属任务", { exact: true })).toBeVisible();
  await expect(page.getByText("总部专属任务", { exact: true })).toHaveCount(0);
  const tasks = (await json(await page.request.get("/ui/tasks"))).tasks;
  expect(tasks.length).toBeGreaterThan(0);
  expect(tasks.every(task => task.legal_entity_id === "LE-UI-BETA")).toBe(true);
  await navigate(page, "组织与团队");
  await expect(page.locator(".org-card").getByText("隔离租户组织", { exact: true })).toBeVisible();
  await expect(page.getByText("集团总部", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "新建部门", exact: true })).toHaveCount(0);
  const departments = (await json(await page.request.get("/ui/departments"))).departments;
  expect(departments.map(row => row.id)).toEqual(["LE-UI-BETA"]);
  expect(errors).toEqual([]);
  expect(apiErrors).toEqual([]);
});

test("a department permission error does not invalidate a valid session", async ({ page }) => {
  await page.route("**/ui/departments", route => route.fulfill({
    status: 403, contentType: "application/json",
    body: JSON.stringify({ detail: { code: "DeploymentAdminOnly" } }),
  }));
  await login(page, tenantIdentity);
  await expect(page.getByRole("status").filter({ hasText: "部分工作空间数据暂时不可用" })).toBeVisible();
  await navigate(page, "任务中心");
  await expect(page.getByText("隔离租户专属任务", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  expect((await json(await page.request.get("/ui/me"))).identity_id).toBe(tenantIdentity);
});

test("a subsidiary CEO in the HQ tenant cannot change deployment configuration", async ({ page }) => {
  const me = await login(page, subsidiaryIdentity);
  expect(me.tenant_id).toBe("TENANT-HQ");
  expect(me.entity_id).toBe("LE-UI-SUB");
  expect(me.can_manage_deployment).toBe(false);
  await expect(page.getByRole("button", { name: "工作空间设置", exact: true })).toHaveCount(0);
  const before = await json(await page.request.get("/ui/settings"));
  const denied = await page.request.put("/ui/settings", { data: { theme: "unauthorized" } });
  expect(denied.status()).toBe(403);
  expect((await denied.json()).detail.code).toBe("DeploymentAdminOnly");
  const resource = await page.request.post("/ui/rbac/resources", {
    data: { id: "RES-BROWSER-DENIED", kind: "api", name: "denied" },
  });
  expect(resource.status()).toBe(403);
  expect(await json(await page.request.get("/ui/settings"))).toEqual(before);
  await page.reload();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  expect((await json(await page.request.get("/ui/me"))).identity_id).toBe(subsidiaryIdentity);
  await login(page, hqIdentity);
  const resources = await json(await page.request.get("/ui/rbac/resources"));
  expect(resources.resources.some(row => row.id === "RES-BROWSER-DENIED")).toBe(false);
});
