import { test, expect } from "@playwright/test";

const identity = process.env.AGENT_OS_TEST_ID;
if (!identity) throw new Error("Use the synthetic identity from tools/serve_ui_test.py.");

function gate() {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
}

async function toolsFixture(page) {
  const employees = [
    { id: "binding-employee-a", name: "授权验证员工甲" },
    { id: "binding-employee-b", name: "授权验证员工乙" },
  ];
  const servers = [
    { id: "binding-server-a", name: "授权验证工具甲", url: "https://a.example.invalid/mcp", enabled: true },
    { id: "binding-server-b", name: "授权验证工具乙", url: "https://b.example.invalid/mcp", enabled: true },
  ];
  const bindings = new Map(employees.map((employee, index) => [employee.id, [servers[index].id]]));
  const reads = [];
  const writes = [];
  const pendingReads = new Map();
  const failedReads = new Set();
  let pendingWrite;
  const allGates = [];
  await page.route("**/ui/employees", route => route.fulfill({ json: { employees } }));
  await page.route("**/ui/mcp", route => route.fulfill({ json: { servers } }));
  await page.route("**/ui/smtp", route => route.fulfill({ json: {} }));
  await page.route(/\/ui\/mcp\/bindings\/[^/?]+$/, async route => {
    const employee = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-1));
    if (route.request().method() === "GET") {
      reads.push(employee);
      const pending = pendingReads.get(employee);
      pendingReads.delete(employee);
      if (pending) await pending.promise;
      if (failedReads.delete(employee)) {
        await route.fulfill({ status: 503, json: { detail: { message: "授权读取暂时失败" } } });
      } else {
        await route.fulfill({ json: { server_ids: bindings.get(employee) || [] } });
      }
    } else {
      const payload = route.request().postDataJSON();
      writes.push({ employee, ...payload });
      const pending = pendingWrite;
      pendingWrite = undefined;
      if (pending) await pending.promise;
      bindings.set(employee, [...payload.server_ids]);
      await route.fulfill({ json: { ok: true } });
    }
  });
  const hold = () => { const result = gate(); allGates.push(result); return result; };
  return {
    employees, servers, bindings, reads, writes,
    holdRead(employee) { const result = hold(); pendingReads.set(employee, result); return result; },
    failRead(employee) { failedReads.add(employee); },
    holdWrite() { pendingWrite = hold(); return pendingWrite; },
    releaseAll() { allGates.forEach(item => item.release()); },
  };
}

async function openTools(page) {
  await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "工具与集成", exact: true }).click();
}

test.beforeEach(async ({ page }) => {
  await page.goto(`/ui/dev-login?as_id=${encodeURIComponent(identity)}&tab=overview`);
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible();
});

test("delayed employee bindings cannot be saved empty and saving fixes the employee context", async ({ page }) => {
  const fixture = await toolsFixture(page);
  const [first, second] = fixture.employees;
  const [toolA, toolB] = fixture.servers;
  try {
    await openTools(page);
    const select = page.getByLabel("选择员工");
    const save = page.getByRole("button", { name: "保存员工授权", exact: true });
    const checkA = page.getByRole("checkbox", { name: toolA.name, exact: true });
    const checkB = page.getByRole("checkbox", { name: toolB.name, exact: true });
    await expect(checkA).toBeChecked();
    await expect(save).toBeEnabled();
    const read = fixture.holdRead(second.id);
    await select.selectOption(second.id);
    await expect.poll(() => fixture.reads.includes(second.id)).toBe(true);
    await expect(page.getByText("正在读取员工授权…", { exact: true })).toBeVisible();
    await expect(save).toBeDisabled();
    await expect(checkA).toBeDisabled();
    await expect(checkB).toBeDisabled();
    expect(fixture.writes).toEqual([]);
    expect(fixture.bindings.get(second.id)).toEqual([toolB.id]);

    read.release();
    await expect(checkB).toBeChecked();
    await expect(checkA).not.toBeChecked();
    await expect(save).toBeEnabled();
    await checkA.check();
    const write = fixture.holdWrite();
    await save.click();
    await expect.poll(() => fixture.writes.length).toBe(1);
    await expect(select).toBeDisabled();
    await expect(save).toBeDisabled();
    await expect(checkA).toBeDisabled();
    expect(fixture.writes[0]).toEqual({ employee: second.id, server_ids: [toolB.id, toolA.id] });
    expect(fixture.bindings.get(first.id)).toEqual([toolA.id]);
    write.release();
    await expect(page.getByRole("status")).toContainText("员工工具授权已保存");
    await expect(select).toBeEnabled();
  } finally { fixture.releaseAll(); }
});

test("failed employee bindings remain read-only until a successful retry", async ({ page }) => {
  const fixture = await toolsFixture(page);
  const second = fixture.employees[1];
  const toolB = fixture.servers[1];
  try {
    await openTools(page);
    const save = page.getByRole("button", { name: "保存员工授权", exact: true });
    await expect(save).toBeEnabled();
    fixture.failRead(second.id);
    await page.getByLabel("选择员工").selectOption(second.id);
    await expect(page.getByRole("alert")).toContainText("授权读取暂时失败");
    await expect(save).toBeDisabled();
    for (const server of fixture.servers)
      await expect(page.getByRole("checkbox", { name: server.name, exact: true })).toBeDisabled();
    expect(fixture.writes).toEqual([]);
    expect(fixture.bindings.get(second.id)).toEqual([toolB.id]);
    await page.getByRole("button", { name: "重试读取授权", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("checkbox", { name: toolB.name, exact: true })).toBeChecked();
    await expect(save).toBeEnabled();
    expect(fixture.writes).toEqual([]);
  } finally { fixture.releaseAll(); }
});

test("a late binding response cannot overwrite the currently selected employee", async ({ page }) => {
  const fixture = await toolsFixture(page);
  const [first, second] = fixture.employees;
  const [toolA, toolB] = fixture.servers;
  try {
    await openTools(page);
    const select = page.getByLabel("选择员工");
    const save = page.getByRole("button", { name: "保存员工授权", exact: true });
    await expect(save).toBeEnabled();
    const read = fixture.holdRead(second.id);
    await select.selectOption(second.id);
    await expect.poll(() => fixture.reads.includes(second.id)).toBe(true);
    await select.selectOption(first.id);
    await expect(save).toBeEnabled();
    const response = page.waitForResponse(result =>
      result.url().endsWith(`/ui/mcp/bindings/${second.id}`) && result.request().method() === "GET");
    read.release();
    await response;
    await expect(select).toHaveValue(first.id);
    await expect(page.getByRole("checkbox", { name: toolA.name, exact: true })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: toolB.name, exact: true })).not.toBeChecked();
    await save.click();
    await expect.poll(() => fixture.writes.length).toBe(1);
    expect(fixture.writes[0]).toEqual({ employee: first.id, server_ids: [toolA.id] });
    expect(fixture.bindings.get(second.id)).toEqual([toolB.id]);
  } finally { fixture.releaseAll(); }
});
