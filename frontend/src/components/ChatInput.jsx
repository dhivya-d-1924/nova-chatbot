import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, ArrowUp, FileText, ImagePlus, Loader2, Mic, Paperclip, Square, X } from "lucide-react";
import IconButton from "./IconButton";
import ToolsMenu from "./ToolsMenu";
import { api } from "../lib/api";
import { MAX_IMAGES, prepareImage } from "../lib/images";
import { useDictation, useVoice } from "../lib/voice";

const MAX = 8000;

export default function ChatInput({ onSend, onStop, busy, focusKey, docCount = 0, onOpenDocs, tools, conversationId, offline = false }) {
  const [value, setValue] = useState("");
  const [files, setFiles] = useState([]); // {key, preview, id, status: uploading|ready|error, error}
  const [dragging, setDragging] = useState(false);
  const ref = useRef(null);
  const fileInput = useRef(null);
  const base = useRef("");
  const { lang, stop: stopSpeaking } = useVoice();
  const dictation = useDictation(lang, (t) => setValue((base.current ? `${base.current} ` : "") + t));

  const ready = files.filter((f) => f.status === "ready");
  const uploading = files.some((f) => f.status === "uploading");
  const canSend = (value.trim().length > 0 || ready.length > 0) && !uploading && !offline;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  // Focus the box on desktop; on touch devices that would pop up the keyboard unasked.
  useEffect(() => { if (window.matchMedia?.("(pointer: fine)").matches) ref.current?.focus(); }, [focusKey]);

  const addImages = async (list) => {
    const picked = Array.from(list).filter((f) => f.type.startsWith("image/")).slice(0, Math.max(0, MAX_IMAGES - files.length));
    for (const file of picked) {
      const key = `${file.name}-${Math.random()}`;
      setFiles((p) => [...p, { key, preview: URL.createObjectURL(file), status: "uploading", progress: 0 }]);
      try {
        const { id } = await api.uploadAttachment(await prepareImage(file),
          (pct) => setFiles((p) => p.map((f) => (f.key === key ? { ...f, progress: pct } : f))));
        setFiles((p) => p.map((f) => (f.key === key ? { ...f, id, status: "ready" } : f)));
      } catch (e) {
        setFiles((p) => p.map((f) => (f.key === key ? { ...f, status: "error", error: e.message } : f)));
      }
    }
  };

  const removeFile = (key) => setFiles((p) => {
    const f = p.find((x) => x.key === key);
    if (f?.preview) URL.revokeObjectURL(f.preview);
    if (f?.id) api.deleteAttachment(f.id).catch(() => {}); // unsent images are also cleaned up by the server after a day
    return p.filter((x) => x.key !== key);
  });

  const submit = () => {
    if (!canSend || busy) return;
    dictation.stop();
    stopSpeaking();
    onSend(value, ready.map((f) => ({ id: f.id, preview: f.preview })));
    setValue("");
    setFiles([]); // previews stay alive: the sent message still shows them
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const onPaste = (e) => {
    const imgs = Array.from(e.clipboardData?.files || []).filter((f) => f.type.startsWith("image/"));
    if (imgs.length) { e.preventDefault(); addImages(imgs); }
  };

  const toggleMic = () => {
    if (dictation.listening) { dictation.stop(); return; }
    base.current = value.trim();
    dictation.start();
  };

  return (
    <div className="shrink-0 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2 sm:px-6">
      <div className="mx-auto mb-2 flex max-w-3xl flex-wrap items-center gap-2">
        {docCount > 0 && (
          <button
            type="button" onClick={onOpenDocs}
            className="inline-flex min-h-9 items-center gap-2 rounded-full border border-accent/40 bg-accent/10 px-3 text-xs font-medium transition-colors hover:bg-accent/20"
          >
            <FileText size={14} className="text-accent" />
            Answering from {docCount} document{docCount > 1 ? "s" : ""}
          </button>
        )}
        {tools && <ToolsMenu tools={tools} conversationId={conversationId} />}
      </div>

      <div
        onDragOver={(e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); setDragging(true); } }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); addImages(e.dataTransfer.files); }}
        className={`glass mx-auto w-full max-w-3xl rounded-2xl p-2 transition-shadow focus-within:border-accent/60 focus-within:shadow-glow ${dragging ? "border-accent shadow-glow" : ""}`}
      >
        {files.length > 0 && (
          <ul className="flex flex-wrap gap-2 px-1 pb-2" aria-label="Attached images">
            {files.map((f) => (
              <li key={f.key} className="relative">
                <img src={f.preview} alt="Attached preview" className={`h-16 w-16 rounded-xl object-cover ${f.status === "error" ? "opacity-40" : ""}`} />
                {f.status === "uploading" && (
                  <span className="absolute inset-0 flex flex-col items-center justify-center rounded-xl bg-black/50 text-[10px] font-semibold text-white" aria-label={`Uploading ${f.progress || 0}%`}>
                    <Loader2 size={16} className="animate-spin" />{f.progress ? `${f.progress}%` : ""}
                  </span>
                )}
                {f.status === "error" && <span title={f.error} className="absolute inset-0 flex items-center justify-center rounded-xl bg-red-500/30"><AlertTriangle size={18} className="text-red-200" /></span>}
                <button type="button" onClick={() => removeFile(f.key)} aria-label="Remove image" className="absolute -right-2 -top-2 flex h-7 w-7 items-center justify-center rounded-full bg-ink text-bg shadow">
                  <X size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}
        {files.some((f) => f.status === "error") && (
          <p role="alert" className="px-2 pb-1 text-xs text-red-400">{files.find((f) => f.status === "error").error}</p>
        )}

        <div className="flex items-end gap-1">
          <IconButton label="Upload or choose documents" onClick={onOpenDocs} side="top" className="hidden shrink-0 sm:inline-flex"><Paperclip size={19} /></IconButton>
          <IconButton label="Attach an image" onClick={() => fileInput.current?.click()} side="top" className="shrink-0" disabled={files.length >= MAX_IMAGES}><ImagePlus size={19} /></IconButton>
          <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple className="sr-only" tabIndex={-1} aria-hidden="true"
            onChange={(e) => { addImages(e.target.files); e.target.value = ""; }} />
          {dictation.supported && (
            <span className="relative shrink-0">
              {dictation.listening && <motion.span className="pointer-events-none absolute inset-0 rounded-xl bg-red-500/40" animate={{ scale: [1, 1.35], opacity: [0.6, 0] }} transition={{ duration: 1.2, repeat: Infinity }} />}
              <IconButton
                label={dictation.listening ? "Stop voice input" : "Start voice input"} onClick={toggleMic} side="top"
                aria-pressed={dictation.listening} className={dictation.listening ? "!bg-red-500/20 !text-red-400" : ""}
              ><Mic size={19} /></IconButton>
            </span>
          )}
          <label htmlFor="chat-input" className="sr-only">Message</label>
          <textarea
            id="chat-input" ref={ref} rows={1} value={value} maxLength={MAX}
            onChange={(e) => setValue(e.target.value)} onKeyDown={onKeyDown} onPaste={onPaste}
            placeholder={offline ? "You're offline…" : dictation.listening ? "Listening… speak now" : docCount > 0 ? "Ask about your documents…" : "Message Nova…"}
            aria-describedby="chat-input-hint" enterKeyHint="send"
            className="thin-scroll max-h-[200px] min-h-[44px] min-w-0 flex-1 resize-none bg-transparent px-2 py-2.5 text-base placeholder:text-muted/80 focus:outline-none sm:px-3 sm:text-[15px]"
          />
          <AnimatePresence mode="wait" initial={false}>
            {busy ? (
              <motion.button
                key="stop" type="button" onClick={onStop} aria-label="Stop generating"
                initial={{ scale: 0.7, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.7, opacity: 0 }} whileTap={{ scale: 0.9 }}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-line/20 text-ink transition-colors hover:bg-ink/10"
              ><Square size={16} fill="currentColor" /></motion.button>
            ) : (
              <motion.button
                key="send" type="button" onClick={submit} disabled={!canSend} aria-label="Send message"
                initial={{ scale: 0.7, opacity: 0 }} animate={{ scale: canSend ? 1 : 0.92, opacity: 1 }} exit={{ scale: 0.7, opacity: 0 }}
                whileHover={canSend ? { scale: 1.08 } : undefined} whileTap={canSend ? { scale: 0.9 } : undefined}
                className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl transition-all duration-300 ${canSend ? "btn-gradient shadow-glow" : "bg-ink/10 text-muted"}`}
              >
                <motion.span animate={{ y: canSend ? [0, -2, 0] : 0 }} transition={{ duration: 0.4 }} className="flex"><ArrowUp size={20} /></motion.span>
              </motion.button>
            )}
          </AnimatePresence>
        </div>
      </div>
      <p id="chat-input-hint" aria-live="polite" className="mx-auto mt-1.5 max-w-3xl px-1 text-center text-xs text-muted">
        {dictation.error ? <span className="text-red-400">{dictation.error}</span> : (
          <>{value.length > MAX - 300 ? `${value.length}/${MAX} characters · ` : ""}<span className="hidden sm:inline">Enter to send, Shift+Enter for a new line · </span>Nova can make mistakes. Check important information.</>
        )}
      </p>
    </div>
  );
}
