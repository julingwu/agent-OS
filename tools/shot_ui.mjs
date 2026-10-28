// 用 Edge/Chrome 的 CDP 截一张"登录后"的界面图（注入 localStorage 后重载）。
// 用法：node tools/shot_ui.mjs <url> <out.png> <baseUrl> <token>
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import { createServer } from "node:net";

const [, , url, out, baseUrl, token, jsExpr] = process.argv;
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = Number(process.env.SHOT_PORT || 9333);
const external = Boolean(process.env.SHOT_PORT);
const profile = "C:\\oaagent-m0\\build\\cdp-profile";

function waitPort(port, timeoutMs = 15000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      const s = createServer();
      s.once("error", () => {
        s.close();
        resolve(); // 端口已被占用 = 浏览器起来了
      });
      s.once("listening", () => {
        s.close();
        if (Date.now() - start > timeoutMs) reject(new Error("timeout"));
        else setTimeout(tick, 200);
      });
      s.listen(port, "127.0.0.1");
    };
    tick();
  });
}

async function json(path) {
  const res = await fetch(`http://127.0.0.1:${PORT}${path}`);
  return res.json();
}

let child = null;
if (!external) {
  child = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--ignore-certificate-errors",
    `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    "--window-size=1600,900", "--hide-scrollbars", url,
  ], { stdio: "ignore" });
}

try {
  await new Promise((r) => setTimeout(r, 2500));
  let ver;
  for (let i = 0; i < 30; i++) {
    try { ver = await json("/json/version"); break; } catch { await new Promise((r) => setTimeout(r, 300)); }
  }
  if (!ver) throw new Error("browser not reachable");

  const targets = await json("/json/list");
  const page = targets.find((t) => t.type === "page");
  if (!page) throw new Error("no page target");

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const send = (method, params = {}) =>
    new Promise((resolve) => {
      const mid = ++id;
      pending.set(mid, resolve);
      ws.send(JSON.stringify({ id: mid, method, params }));
    });

  await new Promise((r) => (ws.onopen = r));
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  };

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Runtime.evaluate", {
    expression: process.env.OA_TOKEN
      ? `localStorage.setItem("oa_token", ${JSON.stringify(process.env.OA_TOKEN)}); location.reload();`
      : `localStorage.setItem("agentos.conn", ${JSON.stringify(JSON.stringify({ baseUrl, token }))}); location.reload();`,
  });
  await new Promise((r) => setTimeout(r, 5000));
  if (jsExpr) {
    await send("Runtime.evaluate", { expression: jsExpr, awaitPromise: true });
    await new Promise((r) => setTimeout(r, 1200));
  }
  const shot = await send("Page.captureScreenshot", { format: "png" });
  if (!shot.result?.data) throw new Error("no screenshot data");
  writeFileSync(out, Buffer.from(shot.result.data, "base64"));
  console.log("saved", out);
} finally {
  if (child) child.kill();
}
