"use client";

import * as React from "react";

import { Button } from "@/components/ui";

type Theme = "light" | "dark";

const STORAGE_KEY = "learnflow.theme";

function apply(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
}

/**
 * Reads the stored preference, falling back to the OS setting. The initial class is applied
 * by an inline script in the layout so the first paint is already correct — doing it here
 * only would flash light before hydration.
 */
export function useTheme(): [Theme, (next: Theme) => void] {
  const [theme, setTheme] = React.useState<Theme>("light");

  React.useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY) as Theme | null;
    const initial =
      stored ??
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    setTheme(initial);
    apply(initial);
  }, []);

  const update = React.useCallback((next: Theme) => {
    setTheme(next);
    localStorage.setItem(STORAGE_KEY, next);
    apply(next);
  }, []);

  return [theme, update];
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
