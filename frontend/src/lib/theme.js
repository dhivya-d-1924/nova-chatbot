import { useCallback, useEffect, useState } from "react";

const prefersDark = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches;
export const resolveTheme = (t) => (t === "system" ? (prefersDark() ? "dark" : "light") : t === "light" ? "light" : "dark");

/** Apply a theme preference ("dark" | "light" | "system"). The resolved value is cached locally only to avoid a flash on load. */
export function applyTheme(pref) {
  const root = document.documentElement;
  const next = resolveTheme(pref);
  if (root.classList.contains(next)) return;
  root.classList.add("theme-transition");
  root.classList.remove("dark", "light");
  root.classList.add(next);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", next === "dark" ? "#060816" : "#f4f6ff");
  try { localStorage.setItem("theme", next); } catch { /* storage unavailable */ }
  setTimeout(() => root.classList.remove("theme-transition"), 450);
}

/** For screens without a signed-in user (sign-in page): local-only theme toggle. */
export function useTheme() {
  const read = () => (document.documentElement.classList.contains("light") ? "light" : "dark");
  const [theme, setTheme] = useState(read);
  useEffect(() => { setTheme(read()); }, []);
  const toggle = useCallback(() => {
    const next = read() === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
  }, []);
  return [theme, toggle];
}
