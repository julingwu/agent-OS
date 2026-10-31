import { test, expect } from "@playwright/test";
import { createHash, createHmac, randomUUID } from "node:crypto";

const identity = process.env.AGENT_OS_TEST_ID;
if (!identity?.trim()) throw new Error("Set AGENT_OS_TEST_ID to the synthetic identity printed by tools/serve_ui_test.py.");
// A normal-login fixture uses a disposable credential. Never retain it in a trace.
test.use({ trace: "off" });

async function json(response) {
  expect(response.ok(), `Synthetic API returned ${response.status()}`).toBeTruthy();
  return response.json();
}

async function signed(page, secret, pathname, data) {
  const body = JSON.stringify(data);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const nonce = randomUUID();
  const canonical = ["POST", pathname, "", timestamp, nonce, createHash("sha256").update(body).digest("hex")].join("\n");
  const signature = createHmac("sha256", secret).update(canonical).digest("hex");
  return json(await page.request.post(pathname, { data: body, headers: {
    "content-type": "application/json", "X-OA-Key": identity, "X-OA-Ts": timestamp,
    "X-OA-Nonce": nonce, "X-OA-Sig": signature,
  } }));
}

const external = "https://preview-egress.invalid";
const artifactHtml = `<!doctype html><html><head>
<style>@font-face{font-family:externalProbe;src:url('${external}/font.woff2')}body{color:rgb(12,34,56);font-family:externalProbe;background-image:url('${external}/background.png')}</style>
<link rel="stylesheet" href="${external}/stylesheet.css"><script src="${external}/script.js"></script>
</head><body><h1>Preview credential isolation</h1><button id="inside">Focus inside preview</button>
<img src="${external}/image.png"><audio preload="auto" src="${external}/audio.mp3"></audio>
<form action="${external}/form" method="post"><input name="probe" value="synthetic"></form>
<pre id="security-observation"></pre><script>
const observed={query:location.search,parentBlocked:false,cookieBlocked:false,inlineScript:true};
try{parent.localStorage.getItem('oa_token')}catch{observed.parentBlocked=true}
try{document.cookie}catch{observed.cookieBlocked=true}
document.getElementById('security-observation').textContent=JSON.stringify(observed);
fetch('${external}/fetch').catch(()=>{});
try{document.forms[0].submit()}catch{}
try{window.open('${external}/popup')}catch{}
</script></body></html>`;

for (const authentication of ["cookie", "bearer"]) {
  test(`untrusted preview uses a single-artifact ticket and blocks external resources (${authentication})`, async ({ page }) => {
    // Bootstrap is allowed only in the dedicated synthetic server. The page
    // subsequently logs out and performs the real login form flow.
    await page.request.get(`/ui/dev-login?as_id=${encodeURIComponent(identity)}`);
    const who = await json(await page.request.get("/ui/me"));
    expect(who.identity_id).toBe(identity);
    expect((await json(await page.request.get("/ui/settings"))).api_key_set).toBe(false);
    const secret = `synthetic-preview-${randomUUID()}-${randomUUID()}`;
    await json(await page.request.post("/ui/account/secret", { data: { secret } }));
    await page.request.post("/ui/logout");
    await page.goto("/app/");
    await page.locator('.login-card input:not([type="password"])').fill(identity);
    await page.locator('.login-card input[type="password"]').fill(secret);
    const loginResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/ui/login" && response.request().method() === "POST");
    await page.getByRole("button", { name: "进入控制台", exact: true }).click();
    const session = (await json(await loginResponse)).token;
    expect(Boolean(session)).toBe(true);
    await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible();

    const task = await signed(page, secret, "/tasks/from_sentence", { text: `预览安全回归 ${authentication} ${Date.now()}：展示一个 HTML 页面。` });
    const run = await signed(page, secret, "/verbs/spawn_run", { task_id: task.task_id, run_seq: 1 });
    const chats = (await json(await page.request.get("/ui/chats"))).chats;
    const chat = chats.find(value => value.kind === "dm") || chats[0];
    expect(Boolean(chat)).toBe(true);
    const name = `preview-security-${authentication}-${Date.now()}.html`;
    const attachment = await json(await page.request.post(`/ui/chats/${chat.id}/attachments`, {
      multipart: { file: { name, mimeType: "text/html", buffer: Buffer.from(artifactHtml) } },
    }));
    const artifact = await signed(page, secret, "/verbs/attach_artifact", { run_id: run.run_id, art_seq: 1, file_path: attachment.path });
    const second = await signed(page, secret, "/verbs/attach_artifact", { run_id: run.run_id, art_seq: 2, file_path: attachment.path });

    if (authentication === "cookie") await page.evaluate(() => localStorage.removeItem("oa_token"));
    else await page.context().clearCookies();
    const externalRequests = [];
    await page.route(`${external}/**`, route => { externalRequests.push(route.request().resourceType()); return route.abort(); });
    await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "工作产物", exact: true }).click();
    const file = page.locator(".file-row").filter({ hasText: name });
    await expect(file).toHaveCount(1);
    const issuance = page.waitForResponse(response => new URL(response.url()).pathname === `/ui/files/${second.artifact_id}/preview-ticket`);
    await file.getByRole("button", { name: "预览", exact: true }).click();
    const issuedResponse = await issuance;
    const issued = await json(issuedResponse);
    const header = issuedResponse.request().headers().authorization;
    expect(authentication === "bearer" ? header === `Bearer ${session}` : !header).toBe(true);
    const preview = page.locator(".modal.preview");
    const iframe = preview.locator("iframe");
    await expect(iframe).toHaveAttribute("sandbox", "allow-scripts");
    const frame = preview.frameLocator("iframe");
    await expect(frame.locator("#security-observation")).not.toBeEmpty();
    const observed = JSON.parse(await frame.locator("#security-observation").textContent());
    expect(observed.inlineScript).toBe(true);
    expect(observed.parentBlocked).toBe(true);
    expect(observed.cookieBlocked).toBe(true);
    expect(observed.query.includes(session)).toBe(false);
    const parameters = new URLSearchParams(observed.query);
    expect(parameters.has("t")).toBe(false);
    expect(parameters.get("ticket") === issued.ticket).toBe(true);
    expect((await iframe.getAttribute("src")).includes(session)).toBe(false);
    expect(await frame.locator("body").evaluate(element => getComputedStyle(element).color)).toBe("rgb(12, 34, 56)");
    await page.waitForLoadState("networkidle");
    expect(externalRequests, "No external image/script/style/font/media/fetch/form/popup request may leave the frame").toEqual([]);

    const auth = { authorization: `Bearer ${issued.ticket}` };
    expect((await page.request.post(`/ui/tasks/${task.task_id}/action`, { headers: auth, data: { action: "abort" } })).status()).toBe(401);
    expect((await page.request.get(`/ui/files/${artifact.artifact_id}/preview`, { params: { ticket: issued.ticket } })).status()).toBe(401);
    expect((await page.request.get(`/ui/files/${second.artifact_id}/download`, { headers: auth })).status()).toBe(401);
    await frame.getByRole("button", { name: "Focus inside preview" }).click();
    await expect(iframe).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(preview).toHaveCount(0);
    await expect(file.getByRole("button", { name: "预览", exact: true })).toBeFocused();
    expect(externalRequests).toEqual([]);
  });
}
