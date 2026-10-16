import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, Headset, Loader2, ShieldAlert, SlidersHorizontal } from "lucide-react";
import { api } from "../lib/api";
import { LANGUAGES, useVoice } from "../lib/voice";

function Switch({ checked, onChange, label }) {
  return (
    <button
      type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
      className={`relative h-7 w-12 shrink-0 rounded-full transition-colors ${checked ? "bg-accent" : "bg-ink/20"}`}
    >
      <span className={`absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition-all ${checked ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

export default function ToolsMenu({ tools, conversationId }) {
  const { list, agents = [], enabled, setEnabled, agent = "", setAgent = () => {} } = tools;
  const voice = useVoice();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [state, setState] = useState({ busy: false, done: false, error: null });
  const root = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    const onDown = (e) => root.current && !root.current.contains(e.target) && setOpen(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("mousedown", onDown); };
  }, [open]);

  const askHuman = async (e) => {
    e.preventDefault();
    setState({ busy: true, done: false, error: null });
    try {
      await api.handoff(note, conversationId);
      setNote("");
      setState({ busy: false, done: true, error: null });
    } catch (err) {
      setState({ busy: false, done: false, error: err.message });
    }
  };

  const on = enabled && list.length > 0;
  const modeLabel = agents.find((a) => a.id === agent)?.label || "Auto";
  const modes = [{ id: "", label: "Auto", description: "The assistant decides, and can hand parts of a task to specialists." }, ...agents];

  return (
    <div ref={root} className="relative">
      <button
        type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="dialog"
        className={`inline-flex min-h-9 items-center gap-2 rounded-full border px-3 text-xs font-medium transition-colors ${on ? "border-volt/40 bg-volt/10 hover:bg-volt/20" : "border-line/15 text-muted hover:bg-ink/10"}`}
      >
        <SlidersHorizontal size={14} className={on ? "text-volt" : ""} />
        {modeLabel} · tools {on ? "on" : "off"}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            role="dialog" aria-label="Assistant settings"
            initial={{ opacity: 0, y: 8, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 6 }} transition={{ duration: 0.15 }}
            className="glass thin-scroll absolute bottom-full left-0 z-30 mb-2 max-h-[70dvh] w-[min(23rem,calc(100vw-2rem))] overflow-y-auto rounded-2xl bg-panel p-4 shadow-2xl"
          >
            <p className="font-display text-sm font-semibold">Assistant mode</p>
            <div role="radiogroup" aria-label="Assistant mode" className="mt-2 space-y-1.5">
              {modes.map((m) => (
                <button
                  key={m.id || "auto"} type="button" role="radio" aria-checked={agent === m.id} onClick={() => setAgent(m.id)}
                  className={`flex min-h-11 w-full flex-col rounded-xl border px-3 py-2 text-left transition-colors ${agent === m.id ? "border-accent bg-accent/15" : "border-line/10 hover:bg-ink/5"}`}
                >
                  <span className="text-sm font-medium">{m.label}</span>
                  <span className="text-xs text-muted">{m.description.charAt(0).toUpperCase() + m.description.slice(1)}{m.description.endsWith(".") ? "" : "."}</span>
                </button>
              ))}
            </div>

            <div className="mt-4 flex items-start justify-between gap-3 border-t border-line/10 pt-3">
              <div>
                <p className="font-display text-sm font-semibold">Let the assistant use tools</p>
                <p className="mt-1 text-xs text-muted">Replies take a little longer and appear all at once.</p>
              </div>
              <Switch checked={enabled} onChange={setEnabled} label="Use agent tools" />
            </div>
            {list.length === 0 ? (
              <p className="mt-3 text-xs text-muted">No tools are available right now.</p>
            ) : (
              <ul className="mt-3 space-y-1.5 text-sm">
                {list.map((t) => (
                  <li key={t.name} className="flex items-center justify-between gap-2">
                    <span className="truncate">{t.label}</span>
                    {t.sensitive && (
                      <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-amber-400/15 px-2 py-0.5 text-[11px] font-medium text-amber-400"><ShieldAlert size={11} /> asks first</span>
                    )}
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-4 border-t border-line/10 pt-3">
              <p className="font-display text-sm font-semibold">Voice</p>
              {voice.input || voice.output ? (
                <>
                  <label htmlFor="voice-lang" className="mt-2 block text-xs text-muted">Language for speaking and listening</label>
                  <select id="voice-lang" value={voice.lang} onChange={(e) => voice.setLang(e.target.value)}
                    className="mt-1 min-h-10 w-full rounded-xl border border-line/15 bg-panel px-2 text-sm">
                    {LANGUAGES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
                  </select>
                  {voice.output && (
                    <div className="mt-3 flex items-center justify-between gap-3">
                      <span className="text-sm">Read replies aloud</span>
                      <Switch checked={voice.autoRead} onChange={voice.setAutoRead} label="Read replies aloud" />
                    </div>
                  )}
                </>
              ) : <p className="mt-1 text-xs text-muted">This browser doesn't support voice. Chrome and Edge do.</p>}
            </div>

            <form onSubmit={askHuman} className="mt-4 border-t border-line/10 pt-3">
              <label htmlFor="human-note" className="flex items-center gap-2 text-sm font-medium"><Headset size={15} /> Talk to a human</label>
              <textarea
                id="human-note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={1000} required placeholder="What do you need help with?"
                className="mt-2 w-full resize-none rounded-xl border border-line/15 bg-ink/5 p-2.5 text-sm placeholder:text-muted/70 focus:border-accent focus:outline-none"
              />
              <div className="mt-2 flex items-center justify-between gap-2">
                <p aria-live="polite" className={`text-xs ${state.error ? "text-red-400" : "text-emerald-400"}`}>
                  {state.done && <span className="inline-flex items-center gap-1"><CheckCircle2 size={13} /> Sent. A person will reply in this chat.</span>}
                  {state.error}
                </p>
                <button type="submit" disabled={state.busy || !note.trim()} className="btn-gradient inline-flex min-h-9 items-center gap-2 rounded-lg px-3 text-xs font-semibold disabled:opacity-50">
                  {state.busy && <Loader2 size={13} className="animate-spin" />} Send request
                </button>
              </div>
            </form>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
