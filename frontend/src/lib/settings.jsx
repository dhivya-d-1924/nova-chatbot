import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { applyTheme } from "./theme";
import { useToast } from "./toast";

const DEFAULTS = { theme: "dark", voice_lang: "", auto_read: false, agent_tools: true, agent_mode: "" };
const SettingsContext = createContext(null);

/** User preferences stored on the server so they follow the user to every device. */
export function SettingsProvider({ initial, children }) {
  const [settings, setSettings] = useState({ ...DEFAULTS, ...(initial || {}) });
  const toast = useToast();
  const latest = useRef(settings);
  latest.current = settings;

  useEffect(() => { applyTheme(settings.theme); }, [settings.theme]);
  useEffect(() => {
    if (settings.theme !== "system") return undefined;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => applyTheme("system");
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, [settings.theme]);

  const update = useCallback(async (changes) => {
    const before = latest.current;
    setSettings((s) => ({ ...s, ...changes }));
    try {
      const saved = await api.saveSettings(changes);
      setSettings((s) => ({ ...s, ...saved }));
    } catch (e) {
      setSettings(before);
      toast.error(`Couldn't save your settings: ${e.message}`);
    }
  }, [toast]);

  const value = useMemo(() => ({ settings, update }), [settings, update]);
  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export const useSettings = () => useContext(SettingsContext);
