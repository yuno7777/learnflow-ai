"use client";

import * as React from "react";

import { Button } from "@/components/ui";

type Theme = "light" | "dark";

const STORAGE_KEY = "learnflow.theme";
const THEME_CHANGED = "learnflow:theme-changed";
const DARK_QUERY = "(prefers-color-scheme: dark)";

function apply(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
}

/**
 * Reads the stored preference, falling back to the OS setting. The initial class is applied
 * by an inline script in the layout so the first paint is already correct — doing it here
 * only would flash light before hydration.
 */
function readTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
}

function subscribeTheme(onChange: () => void) {
  const media = window.matchMedia(DARK_QUERY);
  media.addEventListener("change", onChange);
  window.addEventListener("storage", onChange);
  window.addEventListener(THEME_CHANGED, onChange);
  return () => {
    media.removeEventListener("change", onChange);
    window.removeEventListener("storage", onChange);
    window.removeEventListener(THEME_CHANGED, onChange);
  };
}

function updateTheme(theme: Theme) {
  localStorage.setItem(STORAGE_KEY, theme);
  window.dispatchEvent(new Event(THEME_CHANGED));
}

export function useTheme(): [Theme, (next: Theme) => void] {
  const theme = React.useSyncExternalStore<Theme>(subscribeTheme, readTheme, () => "light");
  React.useEffect(() => apply(readTheme()), [theme]);
  return [theme, updateTheme];
}

export function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      onClick={() => setTheme(next)}
    >
      <span aria-hidden>{theme === "dark" ? "☀" : "☾"}</span>
    </Button>
  );
}

/** Runs before hydration to prevent a flash of the wrong theme. */
export const THEME_BOOTSTRAP = `
(function(){try{
  var s=localStorage.getItem('${STORAGE_KEY}');
  var d=s?s==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.classList.toggle('dark',d);
  document.documentElement.style.colorScheme=d?'dark':'light';
}catch(e){}})();
`;
