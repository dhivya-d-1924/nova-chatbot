import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, BookOpen, FileText, KeyRound, Menu, Minimize2, MoreVertical, RefreshCw, Trash2 } from "lucide-react";
import Avatar from "./Avatar";
import ChatInput from "./ChatInput";
import ChatMessage from "./ChatMessage";
import IconButton from "./IconButton";
import SuggestionCards from "./SuggestionCards";
import ThemeToggle from "./ThemeToggle";
import TypingIndicator from "./TypingIndicator";
import { useMediaQuery } from "../lib/hooks";
import { fadeUp, stagger } from "../lib/motion";
import { useSettings } from "../lib/settings";
import { resolveTheme } from "../lib/theme";
import { useVoice } from "../lib/voice";

const GREETING = "Hi, I'm Nova. What are we working on?".split(" ");

function Welcome({ onPick, disabled }) {
  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-6 px-4 py-8 text-center">
      <Avatar size={84} float />
      <motion.h1 variants={stagger(0.07, 0.2)} initial="hidden" animate="show"
        className="max-w-md font-display text-2xl font-semibold leading-snug sm:text-3xl" aria-label={GREETING.join(" ")}>
        {GREETING.map((w, i) => (
          <motion.span key={i} variants={fadeUp} className="mr-[0.28em] inline-block" aria-hidden="true">{w}</motion.span>
        ))}
      </motion.h1>
      <SuggestionCards onPick={onPick} disabled={disabled} />
    </div>
  );
}

const STUDY_PROMPTS = ["Explain the first topic of my syllabus simply", "Give me 5 practice questions with answers",
  "Summarise the key points I must remember", "What are the important definitions?"];

function StudyWelcome({ subject, onPick, disabled }) {
  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-5 px-4 py-8 text-center">
      <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-accent/15 text-accent"><BookOpen size={30} /></span>
      <div>
        <h1 className="font-display text-2xl font-semibold">{subject.name} tutor</h1>
        <p className="mt-1 max-w-sm text-sm text-muted">I answer only from your {subject.name} material. Anything it doesn't cover goes to your open doubts.</p>
      </div>
      <div className="grid w-full max-w-lg gap-2 sm:grid-cols-2">
        {STUDY_PROMPTS.map((p) => (
          <button key={p} type="button" onClick={() => onPick(p)} disabled={disabled}
            className="glass min-h-11 rounded-2xl px-4 py-3 text-left text-sm hover:bg-ink/10 disabled:opacity-50">{p}</button>
        ))}
      </div>
    </div>
  );
}

/** On narrow phones, less-used header actions move into a menu so nothing overflows at 320px. */
function MoreMenu({ onClear, canClear, onMinimize }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const { settings, update } = useSettings();
  useEffect(() => {
    if (!open) return undefined;
    ref.current?.querySelector("[role=menuitem]")?.focus();
    const onDown = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);
  const dark = resolveTheme(settings.theme) === "dark";
  const item = "flex min-h-11 w-full items-center gap-2.5 rounded-lg px-3 text-left text-sm hover:bg-ink/10 disabled:opacity-40";
  return (
    <div ref={ref} className="relative">
      <IconButton label="More options" align="end" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}><MoreVertical size={19} /></IconButton>
      {open && (
        <div role="menu" className="glass absolute right-0 top-full z-30 mt-1 w-52 rounded-xl bg-panel p-1 shadow-2xl">
          <button type="button" role="menuitem" className={item} onClick={() => { setOpen(false); update({ theme: dark ? "light" : "dark" }); }}>
            {dark ? "Light theme" : "Dark theme"}
          </button>
          <button type="button" role="menuitem" className={item} disabled={!canClear} onClick={() => { setOpen(false); onClear(); }}>
            <Trash2 size={15} /> Clear chat
          </button>
          <button type="button" role="menuitem" className={item} onClick={() => { setOpen(false); onMinimize(); }}>
            <Minimize2 size={15} /> Minimize
          </button>
        </div>
      )}
    </div>
  );
}

export default function ChatWindow({ chat, health, online, tools, docCount, onOpenDocs, onOpenMenu, onMinimize, onRequestClear, onOpenStudy }) {
  const { messages, status, error, threadLoading, send, stop, regenerate, retry, rate, resolveAction, editMessage } = chat;
  const subject = chat.activeSubject;
  const scroller = useRef(null);
  const endRef = useRef(null);
  const stick = useRef(true);
  const voice = useVoice();
  const wasBusy = useRef(false);
  const narrow = useMediaQuery("(max-width: 419px)");
  const busy = status === "loading" || status === "streaming";
  const last = messages[messages.length - 1];

  useEffect(() => {
    if (last?.role === "user") stick.current = true;
    if (stick.current) endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, status, error, last?.role]);

  // Read a finished reply aloud when the user has turned that on.
  useEffect(() => {
    if (busy) { wasBusy.current = true; return; }
    if (!wasBusy.current) return;
    wasBusy.current = false;
    const m = messages[messages.length - 1];
    if (voice.autoRead && voice.output && m?.role === "assistant" && m.content && !m.steps?.some((s) => s.status === "awaiting")) {
      voice.speak(m.id, m.content);
    }
  }, [busy, messages, voice]);

  const onScroll = () => {
    const el = scroller.current;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  const empty = messages.length === 0 && !threadLoading && status !== "loading";
  const statusText = busy ? "Thinking…" : !online ? "Offline" : health?.unreachable ? "Server unreachable" : health?.model || "Connecting…";
  const dot = !online || health?.unreachable ? "bg-red-400" : health?.configured ? "bg-emerald-400" : "bg-amber-400";

  return (
    <main className="flex h-full min-w-0 flex-1 flex-col pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]" aria-label="Chat">
      <header className="flex shrink-0 items-center gap-0.5 px-1.5 pb-2 pt-[max(0.5rem,env(safe-area-inset-top))] sm:gap-1 sm:px-4">
        <IconButton label="Open menu" onClick={onOpenMenu} className="lg:hidden"><Menu size={21} /></IconButton>
        <div className="flex min-w-0 flex-1 items-center gap-2.5 px-1">
          <div className="hidden min-[360px]:block lg:hidden"><Avatar size={30} busy={busy} /></div>
          <div className="min-w-0">
            <p className="truncate font-display text-sm font-semibold">Nova</p>
            <p className="flex items-center gap-1.5 truncate text-xs text-muted" aria-live="polite">
              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} aria-hidden="true" />
              <span className="truncate">{statusText}</span>
            </p>
          </div>
        </div>
        {subject && <IconButton label={`Open ${subject.name} in Study`} onClick={() => onOpenStudy?.(subject.id)}><BookOpen size={19} /></IconButton>}
        <span className={`relative ${subject ? "hidden" : ""}`}>
          <IconButton label="Documents" onClick={onOpenDocs}><FileText size={19} /></IconButton>
          {docCount > 0 && (
            <span className="pointer-events-none absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-white" aria-hidden="true">{docCount}</span>
          )}
        </span>
        {narrow ? (
          <MoreMenu onClear={onRequestClear} canClear={messages.length > 0} onMinimize={onMinimize} />
        ) : (
          <>
            <IconButton label="Clear chat" onClick={onRequestClear} disabled={messages.length === 0}><Trash2 size={19} /></IconButton>
            <ThemeToggle />
            <IconButton label="Minimize chat" onClick={onMinimize} align="end"><Minimize2 size={19} /></IconButton>
          </>
        )}
      </header>

      {health && !health.unreachable && !health.configured && (
        <div role="alert" className="mx-3 mb-2 flex items-start gap-2 rounded-xl border border-amber-400/40 bg-amber-400/10 p-3 text-sm sm:mx-6">
          <KeyRound size={18} className="mt-0.5 shrink-0 text-amber-400" />
          <span>The AI provider isn't configured yet. An administrator needs to set <code className="font-mono">LLM_API_KEY</code> in <code className="font-mono">backend/.env</code> and restart the server.</span>
        </div>
      )}

      {subject && (
        <div className="mx-3 mb-2 flex items-center gap-2 rounded-xl border border-accent/30 bg-accent/10 px-3 py-2 text-xs sm:mx-6" role="note">
          <BookOpen size={15} className="shrink-0 text-accent" aria-hidden="true" />
          <span className="min-w-0 flex-1"><strong>Study mode · {subject.name}</strong>: answers come only from your study material.</span>
        </div>
      )}

      <div ref={scroller} onScroll={onScroll} className="thin-scroll min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain">
        {threadLoading ? (
          <div className="mx-auto max-w-3xl space-y-4 p-6" aria-busy="true" aria-label="Loading conversation">
            {[0, 1, 2].map((i) => <div key={i} className={`h-16 animate-pulse rounded-2xl bg-ink/10 ${i % 2 ? "ml-auto w-2/3" : "w-3/4"}`} />)}
          </div>
        ) : empty ? (
          subject ? <StudyWelcome subject={subject} onPick={send} disabled={busy} /> : <Welcome onPick={send} disabled={busy} />
        ) : (
          <div role="log" aria-live="polite" aria-relevant="additions" className="mx-auto max-w-3xl space-y-5 px-3 py-4 sm:px-6">
            {messages.map((m, i) =>
              m.id === "pending" && !m.content && !m.steps?.length ? (
                <div key="typing" className="flex gap-3">
                  <div className="mt-1 hidden sm:block"><Avatar size={32} busy /></div>
                  <div className="glass rounded-2xl rounded-tl-md px-4"><TypingIndicator /></div>
                </div>
              ) : (
                <ChatMessage
                  key={m.id}
                  message={m}
                  isLast={i === messages.length - 1}
                  canEdit={m.role === "user" && !busy && !String(m.id).startsWith("tmp-")}
                  busy={m.id === "pending"}
                  chatBusy={busy}
                  onResolve={resolveAction}
                  onRegenerate={regenerate}
                  onRate={rate}
                  onEdit={editMessage}
                />
              ),
            )}
            <AnimatePresence>
              {status === "error" && error && (
                <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} role="alert"
                  className="flex flex-col gap-3 rounded-2xl border border-red-400/40 bg-red-500/10 p-4 sm:flex-row sm:items-center">
                  <AlertTriangle size={20} className="shrink-0 text-red-400" />
                  <p className="flex-1 break-words text-sm">{error}</p>
                  <button type="button" onClick={retry}
                    className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-red-500 px-4 text-sm font-semibold text-white transition-colors hover:bg-red-600">
                    <RefreshCw size={16} /> Retry
                  </button>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <ChatInput onSend={send} onStop={stop} busy={busy} focusKey={chat.activeId} docCount={subject ? 0 : docCount} onOpenDocs={onOpenDocs}
        tools={subject ? null : tools} conversationId={chat.activeId} offline={!online} />
    </main>
  );
}
