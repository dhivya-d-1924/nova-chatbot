import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { useSettings } from "../lib/settings";

/** Tools and specialist agents this user may use. On/off and mode are synced user settings. */
export function useTools(setTools, setAgent) {
  const { settings, update } = useSettings();
  const [list, setList] = useState([]);
  const [agents, setAgents] = useState([]);
  const [error, setError] = useState(null);

  const reload = useCallback(() =>
    api.tools().then((d) => { setList(d.tools || []); setAgents(d.agents || []); setError(null); })
      .catch((e) => { setList([]); setAgents([]); setError(e.message); }), []);
  useEffect(() => { reload(); }, [reload]);

  const enabled = settings.agent_tools !== false;
  const agent = agents.some((a) => a.id === settings.agent_mode) ? settings.agent_mode : "";
  useEffect(() => { setTools(enabled && list.length > 0); }, [enabled, list, setTools]);
  useEffect(() => { setAgent(agent); }, [agent, setAgent]);

  const setEnabled = useCallback((on) => update({ agent_tools: on }), [update]);
  const setMode = useCallback((id) => update({ agent_mode: id }), [update]);

  return { list, agents, enabled, setEnabled, agent, setAgent: setMode, reload, error };
}
