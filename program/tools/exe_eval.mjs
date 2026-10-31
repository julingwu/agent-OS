// 连到已运行的 Tauri WebView2，执行 JS；可选截图。
// 用法：node tools/exe_eval.mjs "<js expression>" [port] [shot.png]
import { writeFileSync } from "node:fs";

const PORT = Number(process.argv[3] || 9333);
const expr = process.argv[2];
const shotPath = process.argv[4];

const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const page = targets.find((t) => t.type === "page");
if (!page) {
  console.log("no page target");
  process.exit(1);
}
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
const out = await send("Runtime.evaluate", {
  expression: expr, awaitPromise: true, returnByValue: true,
});
console.log(JSON.stringify(out.result, null, 2).slice(0, 2000));
if (shotPath) {
  const shot = await send("Page.captureScreenshot", { format: "png" });
  if (shot.result?.data) {
    writeFileSync(shotPath, Buffer.from(shot.result.data, "base64"));
    console.log("saved", shotPath);
  }
}
ws.close();
