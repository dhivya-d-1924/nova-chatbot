import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Brain, Plus, Trash2, X } from "lucide-react";
import IconButton from "./IconButton";
import { api } from "../lib/api";
import { useDialog } from "../lib/hooks";
import { overlay, spring } from "../lib/motion";

export default function MemoryPanel({ open, onClose, onChanged }) {
  const [data, setData] = useState(null); // {enabled, limit, items}
  const [error, setError] = useState(null);
  const [text, setText] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);
  const dialog = useRef(null);
  useDialog(open, onClose, dialog);

  const load = useCallback(async () => {
    try { setData(await api.memory()); setError(null); } catch (e) { setError(e.message); }
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    setConfirmClear(false);
    load();
  }, [open, load]);

  const act = async (fn, after) => {
    try { await fn(); after?.(); await load(); onChanged?.(); } catch (e) { setError(e.message); }
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          {...overlay}
          className="fixed inset-0 z-[55] flex items-end justify-center bg-black/60 backdrop-blur-sm sm:items-center sm:p-4"
          onMouseDown={(e) => e.target === e.currentTarget && onClose()}
        >
          <motion.div
            ref={dialog} role="dialog" aria-modal="true" aria-labelledby="memory-title"
            initial={{ opacity: 0, y: 40 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 24 }} transition={spring}
            className="glass flex max-h-[90dvh] w-full max-w-lg flex-col rounded-t-3xl bg-panel pb-[env(safe-area-inset-bottom)] shadow-2xl sm:rounded-3xl"
          >
            <div className="flex items-start gap-3 p-5 pb-3">
              <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/15 text-accent"><Brain size={20} /></span>
              <div className="min-w-0 flex-1">
                <h2 id="memory-title" className="font-display text-lg font-semibold">Memory</h2>
                <p className="mt-1 text-sm text-muted">Let the assistant remember short facts you share, like your preferences, and use them in future chats.</p>
              </div>
              <button type="button" onClick={onClose} aria-label="Close" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-muted hover:bg-ink/10 hover:text-ink"><X size={20} /></button>
            </div>

            <div className="thin-scroll min-h-0 flex-1 space-y-4 overflow-y-auto px-5 pb-5">
              {error && <p role="alert" className="flex items-start gap-2 rounded-xl border border-red-400/40 bg-red-500/10 p-3 text-sm"><AlertTriangle size={16} className="mt-0.5 shrink-0 text-red-400" /> {error}</p>}

              {!data ? <div className="h-24 animate-pulse rounded-2xl bg-ink/10" aria-busy="true" /> : (
                <>
                  <div className="flex items-start justify-between gap-4 rounded-2xl border border-line/15 p-4">
                    <div>
                      <p className="text-sm font-semibold">Remember things about me</p>
                      <p className="mt-1 text-xs text-muted">
                        {data.enabled
                          ? "On. The assistant may save facts you share or ask it to remember. It is told never to save passwords, health or financial details."
                          : "Off. Nothing is saved or used. Turning it on is your choice, and you can turn it off or delete everything at any time."}
                      </p>
                    </div>
                    <button
                      type="button" role="switch" aria-checked={data.enabled} aria-label="Remember things about me"
                      onClick={() => act(() => api.setMemoryEnabled(!data.enabled))}
                      className={`relative h-7 w-12 shrink-0 rounded-full transition-colors ${data.enabled ? "bg-accent" : "bg-ink/20"}`}
                    >
                      <span className={`absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition-all ${data.enabled ? "left-[22px]" : "left-0.5"}`} />
                    </button>
                  </div>

                  {data.enabled && (
                    <form
                      onSubmit={(e) => { e.preventDefault(); act(() => api.addMemory(text), () => setText("")); }}
                      className="flex gap-2"
                    >
                      <label htmlFor="memory-add" className="sr-only">Add a note for the assistant to remember</label>
                      <input id="memory-add" value={text} onChange={(e) => setText(e.target.value)} maxLength={300} placeholder="e.g. I prefer short answers"
                        className="min-h-11 min-w-0 flex-1 rounded-xl border border-line/15 bg-ink/5 px-3 text-base sm:text-sm placeholder:text-muted/70 focus:border-accent focus:outline-none" />
                      <button type="submit" disabled={!text.trim()} className="btn-gradient inline-flex min-h-11 items-center gap-1.5 rounded-xl px-4 text-sm font-semibold disabled:opacity-50"><Plus size={16} /> Add</button>
                    </form>
                  )}

                  {data.items.length === 0 ? (
                    <p className="py-2 text-center text-sm text-muted">Nothing remembered yet.</p>
                  ) : (
                    <>
                      <div className="flex items-center justify-between text-xs text-muted">
                        <span>{data.items.length} of {data.limit} saved</span>
                        {confirmClear ? (
                          <span className="flex items-center gap-1">
                            <button type="button" onClick={() => act(() => api.clearMemory(), () => setConfirmClear(false))} className="min-h-9 rounded-lg bg-red-500 px-3 text-xs font-semibold text-white hover:bg-red-600">Delete all</button>
                            <button type="button" onClick={() => setConfirmClear(false)} className="min-h-9 rounded-lg px-2 hover:bg-ink/10">Cancel</button>
                          </span>
                        ) : <button type="button" onClick={() => setConfirmClear(true)} className="min-h-9 px-2 font-medium text-red-400 hover:underline">Delete all</button>}
                      </div>
                      <ul className="space-y-2">
                        {data.items.map((m) => (
                          <li key={m.id} className="flex items-center gap-3 rounded-xl border border-line/15 p-3">
                            <p className="min-w-0 flex-1 break-words text-sm">{m.text}</p>
                            <IconButton label={`Delete memory: ${m.text}`} onClick={() => act(() => api.deleteMemory(m.id))} className="shrink-0 hover:!text-red-400"><Trash2 size={17} /></IconButton>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
