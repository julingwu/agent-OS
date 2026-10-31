// Theme management for AgentOS
// Supports 3 options: 'system' | 'dark' | 'light'

import { useEffect, useState } from "react";

const THEME_KEY = "agentos.theme";

export function getStoredTheme() {
  try {
    const val = localStorage.getItem(THEME_KEY);
    if (val === "dark" || val === "light" || val === "system") {
      return val;
    }
  } catch {
    /* ignore */
  }
  return "system";
}

export function resolveEffectiveTheme(pref) {
  const p = pref || getStoredTheme();
  if (p === "light") return "light";
  if (p === "dark") return "dark";
  // system
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return "dark";
}

export function applyTheme(pref) {
  const p = pref || getStoredTheme();
  const effective = resolveEffectiveTheme(p);
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("data-theme", effective);
    document.documentElement.setAttribute("data-theme-preference", p);
    document.documentElement.style.colorScheme = effective;
  }
  return effective;
}

export function saveTheme(pref) {
  try {
    localStorage.setItem(THEME_KEY, pref);
  } catch {
    /* ignore */
  }
  return applyTheme(pref);
}

export function useTheme() {
  const [themePreference, setThemePreference] = useState(getStoredTheme);
  const [effectiveTheme, setEffectiveTheme] = useState(() => resolveEffectiveTheme(getStoredTheme()));

  useEffect(() => {
    const eff = applyTheme(themePreference);
    setEffectiveTheme(eff);

    if (themePreference !== "system") return;

    const mql = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    if (!mql) return;

    const handler = () => {
      const updated = applyTheme("system");
      setEffectiveTheme(updated);
    };

    if (mql.addEventListener) {
      mql.addEventListener("change", handler);
      return () => mql.removeEventListener("change", handler);
    } else if (mql.addListener) {
      mql.addListener(handler);
      return () => mql.removeListener(handler);
    }
  }, [themePreference]);

  const changeTheme = (nextTheme) => {
    saveTheme(nextTheme);
    setThemePreference(nextTheme);
  };

  return {
    themePreference,
    effectiveTheme,
    changeTheme,
  };
}
