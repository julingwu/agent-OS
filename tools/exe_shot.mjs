// 连接已运行的 Tauri exe（WebView2 远程调试端口），注入真实连接信息后截图。
// 用法: node tools/exe_shot.mjs <port> <out.png> <baseUrl> <token>
import { writeFileSync } from "node:fs";

const [, , portArg, out, baseUrl, token] = process.argv;
const PORT = Number(portArg || 9223);

async function json(path) {
  const res = await fetch(`http://127.0.0.1:${PORT}${path}`);
  return res.json();
}

const targets = await json("/json/list");
const page = targets.find((t) => t.type === "page");
if (!page) throw new Error("no page target");
console.log("target:", page.url, "|", page.title);

const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const mid = ++id;
    pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method, params }));
    setTimeout(() => {
      if (pending.has(mid)) { pending.delete(mid); reject(new Error(`timeout ${method}`)); }
    }, 15000);
  });

await new Promise((resolve, reject) => {
  ws.onopen = resolve;
  ws.onerror = reject;
  setTimeout(() => reject(new Error("ws connect timeout")), 8000);
});
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    const fn = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? fn(Promise.reject(new Error(JSON.stringify(msg.error)))) : fn(msg.result);
  }
};

await send("Runtime.enable");
const conn = { baseUrl, token };
const injected = await send("Runtime.evaluate", {
  expression: `localStorage.setItem("agentos.conn", ${JSON.stringify(JSON.stringify(conn))}); "ok"`,
});
console.log("inject:", JSON.stringify(injected));
await send("Page.enable");
await send("Page.reload", { ignoreCache: true });
await new Promise((r) => setTimeout(r, 9000));

const state = await send("Runtime.evaluate", {
  expression: `JSON.stringify({
    href: location.href, ready: document.readyState,
    text: (document.body.innerText || "").slice(0, 200),
    root: document.getElementById("root") ? document.getElementById("root").children.length : -1
  })`,
  returnByValue: true,
});
console.log("state:", state.result?.value);

const shot = await send("Page.captureScreenshot", { format: "png" });
writeFileSync(out, Buffer.from(shot.data, "base64"));
console.log("saved", out);
process.exit(0);
