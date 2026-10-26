import React, { useMemo } from "react";
import { marked } from "marked";

// Model output is untrusted. marked drops raw HTML (renderer.html = "") and
// safeHtml() strips script/iframe/style + inline handlers as a second pass.
marked.use({ renderer: { html: () => "" } });
marked.setOptions({ breaks: true, gfm: true });

export function safeHtml(text) {
  const raw = marked.parse(text || "");
  return raw
    .replace(/<(script|style|iframe|object|embed)[\s\S]*?<\/\1>/gi, "")
    .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "")
    .replace(/javascript:/gi, "");
}

export const initials = (s = "") => (s || "AI").trim().slice(0, 2);
export const cls = (...a) => a.filter(Boolean).join(" ");
export function safeJson(s) { try { return s ? JSON.parse(s) : null; } catch { return null; } }

export function Markdown({ text }) {
  const html = useMemo(() => safeHtml(text), [text]);
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}

export const ICONS = {
  overview: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  tasks: "M8 3h8v4H8zM7 5H5v16h14V5h-2M8 12h8M8 16h5",
  arrow: "M5 12h14M14 7l5 5-5 5",
  chevron: "M9 5l7 7-7 7",
  search: "M21 21l-5-5M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0z",
  refresh: "M20 7v5h-5M4 17v-5h5M6.1 6a7 7 0 0 1 11.5-.5L20 9M4 15l2.4 3.5A7 7 0 0 0 18 18",
  spark: "m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4z",
  complete: "M20 11v1a8 8 0 1 1-4.7-7.3M9 11l3 3 8-8",
  chat: "M21 12a8 8 0 0 1-8 8H7l-4 3v-7a8 8 0 0 1 8-8h2a8 8 0 0 1 8 4z",
  org: "M12 3v4M12 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM5 21v-3a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v3",
  files: "M4 5a2 2 0 0 1 2-2h5l2 3h5a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 4.6 15a2 2 0 1 1 0-4 1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 12 4.6a2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 19.4 11a2 2 0 1 1 0 4z",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9a2.5 2.5 0 1 1 3.6 2.3c-.7.4-1.1 1-1.1 1.7M12 17h.01",
  panel: "M4 5h16v14H4zM15 5v14",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  close: "M6 6l12 12M18 6L6 18",
  plus: "M12 5v14M5 12h14",
  play: "M6 4l14 8-14 8z",
  check: "M4 12l5 5L20 6",
  alert: "M12 8v5M12 16h.01M10.3 3.9 2.6 17a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  role: "M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4 21v-1a5 5 0 0 1 5-5h6a5 5 0 0 1 5 5v1M16 3.5l2 2 3-3",
  shield: "M12 21s7-3.5 7-9V6l-7-3-7 3v6c0 5.5 7 9 7 9zM9.5 12l1.8 1.8 3.4-3.6",
  wallet: "M3 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v2h2v6h-2v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM16 12h.01",
  flask: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3M7.5 15h9",
  brain: "M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0 0 6v1a3 3 0 0 0 5 2.2M15 4a3 3 0 0 1 3 3v1a3 3 0 0 1 0 6v1a3 3 0 0 1-5 2.2M12 5v14",
  flow: "M4 6h4M4 12h6M4 18h9M14 6h6M14 6l-3 3M20 18H11M20 18l-3-3",
};

export function Icon({ name, size = "" }) {
  return (
    <svg className={cls("icon", size)} viewBox="0 0 24 24" aria-hidden="true">
      <path d={ICONS[name] || ICONS.chat} />
    </svg>
  );
}

export function Ring({ pct, used, cap }) {
  const r = 26, c = 2 * Math.PI * r;
  const off = c * (1 - Math.min(1, Math.max(0, pct / 100)));
  return (
    <div className="ring-wrap">
      <svg className="ring" width="66" height="66" viewBox="0 0 66 66">
        <defs>
          <linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#5eead4" />
            <stop offset="100%" stopColor="#38bdf8" />
          </linearGradient>
        </defs>
        <circle className="track" cx="33" cy="33" r={r} />
        <circle className="val" cx="33" cy="33" r={r}
                strokeDasharray={c} strokeDashoffset={off} />
      </svg>
      <div>
        <div className="ring-num">{Math.round(pct)}%</div>
        <div className="ring-sub">{(used / 1000).toFixed(1)}k / {(cap / 1000).toFixed(0)}k</div>
      </div>
    </div>
  );
}

export const STATE_LABEL = { passed: "通过", failed: "不过", pending: "待定", na: "未启用" };
export const STATE_CLASS = { passed: "var(--ok)", failed: "var(--bad)", pending: "var(--warn)", na: "var(--muted)" };

export function formatTokensCN(n) {
  const num = Number(n || 0);
  if (num >= 100_000_000) return `${(num / 100_000_000).toFixed(num % 100_000_000 === 0 ? 0 : 2)} 亿 tokens`;
  if (num >= 10_000) return `${(num / 10_000).toFixed(num % 10_000 === 0 ? 0 : 1)} 万 tokens`;
  return `${num.toLocaleString()} tokens`;
}

