import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, FileText, Loader2, RefreshCcw, ScanText, Sparkles, Trash2, UploadCloud, X } from "lucide-react";
import IconButton from "./IconButton";
import { useDialog } from "../lib/hooks";
import { overlay, spring } from "../lib/motion";

const size = (b) => (b < 1024 * 1024 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1024 / 1024).toFixed(1)} MB`);
const meta = (d) => `${["pdf", "image"].includes(d.kind) ? `${d.pages} page${d.pages === 1 ? "" : "s"}` : d.kind === "docx" ? "Word" : "text"} · ${size(d.size)}${d.version > 1 ? ` · v${d.version}` : ""}`;

export default function DocumentsPanel({ open, onClose, docs }) {
  const { docs: list, selected, loading, uploads, error, config, busyId, toggle, setAll, upload, dismissUpload, remove, reindex } = docs;
  const [drag, setDrag] = useState(false);
  const [confirmId, setConfirmId] = useState(null);
  const dialog = useRef(null);
  useDialog(open, onClose, dialog);
  useEffect(() => { if (!open) setConfirmId(null); }, [open]);
  const accept = config.extensions.map((e) => `.${e}`).join(",");

  const pick = (files) => files?.length && upload(Array.from(files));

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          {...overlay}
          className="fixed inset-0 z-[55] flex items-end justify-center bg-black/60 backdrop-blur-sm sm:items-center sm:p-4"
          onMouseDown={(e) => e.target === e.currentTarget && onClose()}
        >
          <motion.div
            ref={dialog} role="dialog" aria-modal="true" aria-labelledby="docs-title"
            initial={{ opacity: 0, y: 40 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 24 }} transition={spring}
            className="glass flex max-h-[90dvh] w-full max-w-lg flex-col rounded-t-3xl bg-panel pb-[env(safe-area-inset-bottom)] shadow-2xl sm:rounded-3xl"
          >
            <div className="flex items-start gap-3 p-5 pb-3">
              <div className="min-w-0 flex-1">
                <h2 id="docs-title" className="font-display text-lg font-semibold">Your documents</h2>
                <p className="mt-1 text-sm text-muted">Ticked files are searched when you ask a question, and answers cite them. Untick all to chat normally.</p>
              </div>
              <button type="button" onClick={onClose} aria-label="Close" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-muted hover:bg-ink/10 hover:text-ink">
                <X size={20} />
              </button>
            </div>

            <div className="thin-scroll min-h-0 flex-1 space-y-3 overflow-y-auto px-5 pb-5">
              <label
                onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files); }}
                className={`flex min-h-28 cursor-pointer flex-col items-center justify-center gap-1.5 rounded-2xl border-2 border-dashed p-4 text-center transition-colors focus-within:border-volt ${drag ? "border-accent bg-accent/10" : "border-line/20 hover:border-accent/60 hover:bg-ink/5"}`}
              >
                <UploadCloud size={26} className="text-accent" />
                <span className="text-sm font-medium">Drop files here or click to browse</span>
                <span className="text-xs text-muted">
                  {config.extensions.map((e) => e.toUpperCase()).join(", ")} · up to {config.max_mb} MB each
                  {config.ocr ? " · scans are read with OCR" : ""}
                </span>
                <input type="file" multiple accept={accept} className="sr-only"
                  onChange={(e) => { pick(e.target.files); e.target.value = ""; }} />
              </label>

              <div aria-live="polite" className="space-y-2">
                {uploads.map((u) => (
                  <div key={u.key} className={`flex items-center gap-3 rounded-xl border p-3 text-sm ${u.state === "error" ? "border-red-400/40 bg-red-500/10" : "border-line/15"}`}>
                    {u.state === "uploading" ? <Loader2 size={16} className="shrink-0 animate-spin text-accent" /> : <AlertTriangle size={16} className="shrink-0 text-red-400" />}
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{u.name}</p>
                      {u.state === "error" ? <p className="text-xs text-red-300">{u.error}</p> : (
                        <>
                          <p className="text-xs text-muted">{u.state === "indexing" ? "Reading and indexing…" : `Uploading ${u.progress || 0}%`}</p>
                          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink/10" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={u.progress || 0} aria-label={`Uploading ${u.name}`}>
                            <div className={`h-full bg-accent transition-all ${u.state === "indexing" ? "animate-pulse" : ""}`} style={{ width: `${u.state === "indexing" ? 100 : u.progress || 0}%` }} />
                          </div>
                        </>
                      )}
                    </div>
                    {u.state === "error" && <IconButton label="Dismiss" onClick={() => dismissUpload(u.key)}><X size={16} /></IconButton>}
                  </div>
                ))}
                {error && <p role="alert" className="rounded-xl border border-red-400/40 bg-red-500/10 p-3 text-sm">{error}</p>}
              </div>

              {loading ? (
                <div className="space-y-2" aria-busy="true">{[0, 1].map((i) => <div key={i} className="h-14 animate-pulse rounded-xl bg-ink/10" />)}</div>
              ) : list.length === 0 ? (
                <p className="py-4 text-center text-sm text-muted">No documents yet. Upload one to ask questions about it.</p>
              ) : (
                <>
                  <div className="flex items-center justify-between text-xs text-muted">
                    <span>{selected.length} of {list.length} selected</span>
                    <button type="button" onClick={() => setAll(selected.length !== list.length)} className="min-h-9 px-2 font-medium text-volt hover:underline">
                      {selected.length === list.length ? "Untick all" : "Tick all"}
                    </button>
                  </div>
                  <ul className="space-y-2">
                    {list.map((d) => (
                      <li key={d.id} className="flex items-center gap-3 rounded-xl border border-line/15 p-3">
                        <input
                          id={`doc-${d.id}`} type="checkbox" checked={selected.includes(d.id)} onChange={() => toggle(d.id)}
                          className="h-5 w-5 shrink-0 accent-[rgb(var(--accent))]"
                        />
                        <label htmlFor={`doc-${d.id}`} className="flex min-w-0 flex-1 cursor-pointer items-center gap-3">
                          <FileText size={18} className="shrink-0 text-muted" />
                          <span className="min-w-0">
                            <span className="block truncate text-sm font-medium">{d.filename}</span>
                            <span className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
                              {meta(d)}
                              {d.ocr && <span className="inline-flex items-center gap-0.5" title="Text read with OCR"><ScanText size={11} /> OCR</span>}
                              {d.embedded && <span className="inline-flex items-center gap-0.5" title="Semantic search ready"><Sparkles size={11} /> semantic</span>}
                            </span>
                          </span>
                        </label>
                        {confirmId === d.id ? (
                          <span className="flex shrink-0 items-center gap-1">
                            <button type="button" onClick={() => { remove(d.id); setConfirmId(null); }} className="min-h-9 rounded-lg bg-red-500 px-3 text-xs font-semibold text-white hover:bg-red-600">Delete</button>
                            <button type="button" onClick={() => setConfirmId(null)} className="min-h-9 rounded-lg px-2 text-xs hover:bg-ink/10">Cancel</button>
                          </span>
                        ) : busyId === d.id ? <Loader2 size={17} className="mx-3 shrink-0 animate-spin text-muted" aria-label="Working" /> : (
                          <span className="flex shrink-0">
                            {config.semantic && !d.embedded && (
                              <IconButton label={`Index ${d.filename} for semantic search`} onClick={() => reindex(d.id)}><RefreshCcw size={16} /></IconButton>
                            )}
                            <IconButton label={`Delete ${d.filename}`} onClick={() => setConfirmId(d.id)} className="hover:!text-red-400"><Trash2 size={17} /></IconButton>
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
