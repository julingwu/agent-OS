// 与服务端通信的最小客户端。
// 浏览器预览与 Tauri 内嵌共用；以后 Tauri 版会把这两层换成 Rust 命令（证书固定）。

const LS_KEY = "agentos.conn";

function getDefaultBaseUrl() {
  if (typeof window !== "undefined" && window.location?.origin && !window.location.origin.startsWith("file:")) {
    return window.location.origin;
  }
  return "http://127.0.0.1:8077";
}

export function getConn() {
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY) || "null");
    if (saved && saved.baseUrl) {
      return { baseUrl: saved.baseUrl, token: saved.token || "local" };
    }
  } catch {
    /* ignore */
  }
  return { baseUrl: getDefaultBaseUrl(), token: "local" };
}

export function setConn(conn) {
  localStorage.setItem(LS_KEY, JSON.stringify(conn));
}

export function clearConn() {
  localStorage.removeItem(LS_KEY);
}

export function baseUrl() {
  return (getConn()?.baseUrl || getDefaultBaseUrl()).replace(/\/$/, "");
}

export function token() {
  return getConn()?.token || "local";
}

export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`${baseUrl()}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token()}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || data.message || detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

export function wsUrl(path, params = {}) {
  const u = new URL(baseUrl());
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  const q = new URLSearchParams({ ...params, token: token() });
  u.pathname = path;
  u.search = q.toString();
  return u.toString();
}
