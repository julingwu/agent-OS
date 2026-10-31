import { useEffect } from "react";

const DIALOGS = ".mask .modal, .drawer, .detail[role=dialog]";
const CONTROLS = "button, a[href], input:not([type=hidden]), select, textarea, iframe, summary, [tabindex], [contenteditable=true]";

function visible(element) {
  if (!element?.isConnected || element.closest("[hidden], [inert], [aria-hidden=true]")) return false;
  const style = getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
}

function controls(dialog) {
  return [...dialog.querySelectorAll(CONTROLS)].filter(element =>
    !element.disabled && element.tabIndex >= 0 && visible(element));
}

function focus(element) {
  if (element?.isConnected) element.focus({ preventScroll: true });
}

// Existing dialogs keep their own closing callbacks. This component supplies
// keyboard behavior for the topmost dialog, including nested file previews.
export default function DialogAccessibility() {
  useEffect(() => {
    const records = new Map();
    let top = null, updating = false, queued = false, disposed = false, serial = 0;
    let lastFocused = document.activeElement;

    const prepare = dialog => {
      const opener = dialog.contains(document.activeElement) ? lastFocused : document.activeElement;
      const undo = [];
      const set = (element, name, value) => {
        const previous = element.getAttribute(name);
        if (previous === value) return;
        element.setAttribute(name, value);
        undo.push(() => previous === null ? element.removeAttribute(name) : element.setAttribute(name, previous));
      };
      if (!dialog.hasAttribute("role")) set(dialog, "role", "dialog");
      set(dialog, "aria-modal", "true");
      if (!dialog.hasAttribute("tabindex")) set(dialog, "tabindex", "-1");
      if (!dialog.hasAttribute("aria-label") && !dialog.hasAttribute("aria-labelledby")) {
        const heading = dialog.querySelector("h1, h2, h3, h4, .who, .ttl");
        if (heading) {
          if (!heading.id) set(heading, "id", `agent-os-dialog-title-${++serial}`);
          set(dialog, "aria-labelledby", heading.id);
        } else {
          set(dialog, "aria-label", "对话框");
        }
      }
      records.set(dialog, { opener, restore: () => undo.reverse().forEach(fn => fn()) });
    };

    const focusInside = dialog => {
      const autofocus = dialog.querySelector("[autofocus]");
      focus(autofocus && visible(autofocus) && !autofocus.disabled ? autofocus : controls(dialog)[0] || dialog);
    };

    const synchronize = () => {
      if (disposed || updating) return;
      updating = true;
      try {
        const dialogs = [...document.querySelectorAll(DIALOGS)].filter(visible).sort((a, b) => {
          const layer = element => parseInt(getComputedStyle(element.closest(".mask") || element).zIndex, 10) || 0;
          return layer(a) - layer(b);
        });
        const previousTop = top;
        let restoreFocus = null;
        for (const [dialog, record] of records) {
          if (!dialogs.includes(dialog)) {
            if (dialog === previousTop) restoreFocus = record.opener;
            record.restore();
            records.delete(dialog);
          }
        }
        dialogs.forEach(dialog => { if (!records.has(dialog)) prepare(dialog); });
        top = dialogs.at(-1) || null;
        if (top !== previousTop) {
          if (restoreFocus?.isConnected && (!top || top.contains(restoreFocus))) focus(restoreFocus);
          else if (top && !top.contains(document.activeElement)) focusInside(top);
        }
      } finally { updating = false; }
    };

    const keydown = event => {
      if (event.defaultPrevented || event.isComposing) return;
      synchronize();
      if (!top) return;
      if (event.key === "Escape") {
        const backdrop = top.closest(".mask") ||
          (top.previousElementSibling?.matches(".drawer-mask") ? top.previousElementSibling : null);
        const close = backdrop || [...top.querySelectorAll("button")].find(button => {
          const label = button.getAttribute("aria-label") || button.title || "";
          return !button.disabled && (/关闭|取消|close|dismiss/i.test(label) ||
            /^(关闭|取消|知道了|×|✕|✖)$/.test(button.textContent.trim()));
        });
        if (close) {
          event.preventDefault(); event.stopPropagation();
          close.click();
        }
      } else if (event.key === "Tab") {
        const items = controls(top);
        const index = items.indexOf(document.activeElement);
        if (!items.length) {
          event.preventDefault(); focus(top);
        } else if (index < 0 || (event.shiftKey && index === 0) || (!event.shiftKey && index === items.length - 1)) {
          event.preventDefault(); focus(event.shiftKey ? items.at(-1) : items[0]);
        }
      }
    };

    const focusin = event => {
      if (updating) return;
      synchronize();
      if (top && !top.contains(event.target)) focusInside(top);
      else lastFocused = event.target;
    };

    const observer = new MutationObserver(() => {
      if (queued) return;
      queued = true;
      queueMicrotask(() => { queued = false; synchronize(); });
    });
    observer.observe(document.body, { childList: true, subtree: true });
    document.addEventListener("keydown", keydown, true);
    document.addEventListener("focusin", focusin, true);
    synchronize();

    return () => {
      disposed = true;
      observer.disconnect();
      document.removeEventListener("keydown", keydown, true);
      document.removeEventListener("focusin", focusin, true);
      const opener = records.get(top)?.opener;
      records.forEach(record => record.restore());
      focus(opener);
    };
  }, []);
  return null;
}
