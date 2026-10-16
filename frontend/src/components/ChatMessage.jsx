import { Children, isValidElement, memo, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, Ban, Check, Copy, Download, FileText, Loader2, Pencil, RefreshCw, ShieldAlert, ThumbsDown, ThumbsUp, User, Volume2, VolumeX } from "lucide-react";
import Avatar from "./Avatar";
import TypingIndicator from "./TypingIndicator";
import { api } from "../lib/api";
import { useVoice } from "../lib/voice";
import IconButton from "./IconButton";
import { formatWhen, useCopy } from "../lib/hooks";
import { messageIn } from "../lib/motion";

function CodeBlock({ lang, code }) {
  const [copied, copy] = useCopy();
  return (
    <div className="overflow-hidden rounded-xl border border-line/15 bg-black/40 text-[#e6e9ff]">
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-1.5 text-xs text-[#9aa3d0]">
        <span>{lang || "code"}</span>
        <button
          type="button"
          onClick={() => copy(code)}
          aria-label={copied ? "Code copied" : "Copy code"}
          className="inline-flex min-h-8 items-center gap-1.5 rounded-md px-2 transition-colors hover:bg-white/10 hover:text-white"
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="thin-scroll overflow-x-auto p-4 text-[13px] leading-relaxed">
        <code className="font-mono">{code}</code>
      </pre>
    </div>
  );
}

const markdownComponents = {
  pre({ children }) {
    const child = Children.toArray(children)[0];
    if (!isValidElement(child)) return <pre>{children}</pre>;
    const lang = /language-(\w+)/.exec(child.props.className || "")?.[1];
    return <CodeBlock lang={lang} code={String(child.props.children ?? "").replace(/\n$/, "")} />;
  },
  table: ({ children }) => (
    <div className="thin-scroll overflow-x-auto"><table>{children}</table></div>
  ),
  a: ({ children, href, node: _node, ...p }) => (/^(https?:|mailto:)/i.test(href || "")
    ? <a {...p} href={href} target="_blank" rel="noopener noreferrer nofollow">{children}</a>
    : <span>{children}</span>),
  img: ({ alt }) => <span className="text-muted">[image: {alt || "not shown"}]</span>, // never load remote images from model output
};

const STEP_ICON = {
  running: <Loader2 size={15} className="animate-spin text-volt" />,
  done: <Check size={15} className="text-emerald-400" />,
  error: <AlertTriangle size={15} className="text-red-400" />,
  awaiting: <ShieldAlert size={15} className="text-amber-400" />,
  declined: <Ban size={15} className="text-muted" />,
};

function ApprovalCard({ step, disabled, onResolve }) {
  return (
    <div role="group" aria-label="Approval needed" className="mt-2 rounded-xl border border-amber-400/40 bg-amber-400/10 p-3">
      <p className="flex items-center gap-2 text-sm font-semibold"><ShieldAlert size={16} className="text-amber-400" /> Approve this action?</p>
      <p className="mt-1 text-sm">{step.label}</p>
      {step.expires_at && (
        <p className="mt-0.5 text-xs text-muted">
          {step.expires_at < Date.now() * 1000 ? "This request has expired. Ask again to retry." : `Expires ${formatWhen(step.expires_at, { long: true })}.`}
        </p>
      )}
      <dl className="mt-2 space-y-1.5 text-sm">
        {Object.entries(step.args || {}).map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs font-medium capitalize text-muted">{k}</dt>
            <dd className="whitespace-pre-wrap break-words rounded-lg bg-ink/5 px-2.5 py-1.5">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" disabled={disabled || (step.expires_at && step.expires_at < Date.now() * 1000)} onClick={() => onResolve(step.pending_id, true)} className="btn-gradient min-h-11 rounded-xl px-4 text-sm font-semibold shadow-glow disabled:opacity-50">Approve</button>
        <button type="button" disabled={disabled} onClick={() => onResolve(step.pending_id, false)} className="min-h-11 rounded-xl border border-line/20 px-4 text-sm font-medium transition-colors hover:bg-ink/10 disabled:opacity-50">Decline</button>
      </div>
    </div>
  );
}

function Steps({ steps, disabled, onResolve }) {
  const [dlError, setDlError] = useState(null);
  return (
    <ul className="mb-2 space-y-1.5" aria-label="What the assistant did">
      {steps.map((st) => (
        <li key={st.id} className="rounded-xl border border-line/10 bg-ink/5 px-3 py-2 text-sm">
          <div className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0">{STEP_ICON[st.status] || STEP_ICON.running}</span>
            <div className="min-w-0 flex-1">
              <p className="break-words">{st.label}</p>
              {st.summary && st.status !== "running" && <p className="break-words text-xs text-muted">{st.summary}</p>}
              {st.substeps?.length > 0 && (
                <ul className="mt-1.5 space-y-1 border-l-2 border-line/15 pl-3 text-xs text-muted" aria-label="Specialist steps">
                  {st.substeps.map((x, i) => <li key={i} className="break-words">{x.status === "done" ? "✓" : x.status === "error" ? "✗" : "•"} {x.label}</li>)}
                </ul>
              )}
              {st.args?.sql && st.status !== "awaiting" && (
                <details className="mt-1 text-xs text-muted">
                  <summary className="cursor-pointer select-none">View query</summary>
                  <pre className="thin-scroll mt-1 overflow-x-auto rounded-lg bg-black/30 p-2 text-[#e6e9ff]">{st.args.sql}</pre>
                </details>
              )}
              {st.report_id && (
                <button
                  type="button"
                  onClick={() => api.downloadReport(st.report_id, st.report_title).catch((e) => setDlError(e.message))}
                  className="mt-1.5 inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-line/15 px-2.5 text-xs font-medium transition-colors hover:bg-ink/10"
                >
                  <Download size={13} /> Download report
                </button>
              )}
            </div>
          </div>
          {st.status === "awaiting" && <ApprovalCard step={st} disabled={disabled} onResolve={onResolve} />}
        </li>
      ))}
      {dlError && <li role="alert" className="text-xs text-red-400">{dlError}</li>}
    </ul>
  );
}

function AttachmentImage({ image }) {
  const [src, setSrc] = useState(image.preview || null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (image.preview || !image.id) return undefined;
    let url;
    let dead = false;
    api.attachmentBlob(image.id)
      .then((b) => { if (!dead) { url = URL.createObjectURL(b); setSrc(url); } })
      .catch(() => { if (!dead) setFailed(true); });
    return () => { dead = true; if (url) URL.revokeObjectURL(url); };
  }, [image.id, image.preview]);
  if (failed) return <span className="flex h-20 w-28 items-center justify-center rounded-xl bg-white/15 text-xs">Image unavailable</span>;
  if (!src) return <span className="block h-20 w-28 animate-pulse rounded-xl bg-white/20" />;
  return (
    <a href={src} target="_blank" rel="noopener noreferrer">
      <img src={src} alt="Attached image" className="max-h-56 max-w-full rounded-xl object-cover" />
    </a>
  );
}

function Sources({ sources }) {
  const [open, setOpen] = useState(null);
  const active = sources.find((s) => s.n === open);
  return (
    <div className="mt-2">
      <p className="mb-1.5 text-xs font-medium text-muted">Sources</p>
      <div className="flex flex-wrap gap-2">
        {sources.map((s) => (
          <button
            key={s.n} type="button" aria-expanded={open === s.n}
            onClick={() => setOpen(open === s.n ? null : s.n)}
            className={`inline-flex min-h-9 max-w-full items-center gap-1.5 rounded-lg border px-2.5 text-xs transition-colors ${open === s.n ? "border-accent bg-accent/20" : "border-line/15 hover:bg-ink/10"}`}
          >
            <span className="font-semibold text-volt">[{s.n}]</span>
            <FileText size={13} className="shrink-0 text-muted" />
            <span className="truncate">{s.filename}</span>
            <span className="shrink-0 text-muted">· {s.kind === "pdf" ? "p." : "section"} {s.page}</span>
          </button>
        ))}
      </div>
      {active && (
        <blockquote className="mt-2 rounded-xl border-l-4 border-accent/70 bg-ink/5 p-3 text-sm text-muted">
          {active.snippet}{active.snippet.length >= 500 ? "…" : ""}
        </blockquote>
      )}
    </div>
  );
}

function Stamp({ message, align = "left" }) {
  if (!message.created_at) return null;
  return (
    <p className={`mt-1 px-1 text-[11px] text-muted/80 ${align === "right" ? "text-right" : ""}`}>
      <time dateTime={new Date(message.created_at / 1000).toISOString()} title={formatWhen(message.created_at, { long: true })}>
        {formatWhen(message.created_at)}
      </time>
      {message.edited_at ? " · edited" : ""}
    </p>
  );
}

function EditBox({ initial, onSave, onCancel }) {
  const [text, setText] = useState(initial);
  const ref = useRef(null);
  useEffect(() => { ref.current?.focus(); ref.current?.setSelectionRange(initial.length, initial.length); }, [initial]);
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (text.trim()) onSave(text); }}
      className="w-full max-w-[85%] rounded-2xl border border-accent/50 bg-panel p-2 sm:max-w-[75%]">
      <label htmlFor="edit-message" className="sr-only">Edit your message</label>
      <textarea id="edit-message" ref={ref} value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={8000}
        onKeyDown={(e) => { if (e.key === "Escape") onCancel(); if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); if (text.trim()) onSave(text); } }}
        className="thin-scroll w-full resize-y bg-transparent p-2 text-base focus:outline-none sm:text-[15px]" />
      <p className="px-2 text-xs text-muted">Saving replaces the replies after this message.</p>
      <div className="mt-2 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="min-h-10 rounded-lg px-3 text-sm hover:bg-ink/10">Cancel</button>
        <button type="submit" disabled={!text.trim()} className="btn-gradient min-h-10 rounded-lg px-4 text-sm font-semibold disabled:opacity-50">Save & send</button>
      </div>
    </form>
  );
}

function ChatMessage({ message, isLast, canEdit, busy, chatBusy, onRegenerate, onRate, onResolve, onEdit }) {
  const [copied, copy] = useCopy();
  const [editing, setEditing] = useState(false);
  const voice = useVoice();
  const isUser = message.role === "user";
  const awaiting = message.steps?.some((s) => s.status === "awaiting");

  if (isUser) {
    if (editing) {
      return (
        <div className="flex justify-end">
          <EditBox initial={message.content} onCancel={() => setEditing(false)}
            onSave={(t) => { setEditing(false); onEdit(message.id, t); }} />
        </div>
      );
    }
    return (
      <motion.div {...messageIn} className="group flex justify-end gap-3">
        <div className="flex max-w-[85%] flex-col items-end sm:max-w-[75%]">
          <div className="whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-gradient-to-br from-accent to-volt px-4 py-3 text-white shadow-lift">
            {message.images?.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2">{message.images.map((im, i) => <AttachmentImage key={im.id || im.preview || i} image={im} />)}</div>
            )}
            {message.content}
          </div>
          <div className="flex items-center gap-1">
            <Stamp message={message} align="right" />
            {canEdit && (
              <button type="button" onClick={() => setEditing(true)} aria-label="Edit message"
                className="mt-1 inline-flex h-8 items-center gap-1 rounded-lg px-2 text-[11px] text-muted opacity-100 transition hover:bg-ink/10 hover:text-ink md:opacity-0 md:focus-visible:opacity-100 md:group-hover:opacity-100">
                <Pencil size={12} /> Edit
              </button>
            )}
          </div>
        </div>
        <span className="mt-1 hidden h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ink/10 text-muted sm:flex" aria-hidden="true">
          <User size={16} />
        </span>
      </motion.div>
    );
  }

  return (
    <motion.div {...messageIn} className="group flex gap-3">
      <div className="mt-1 hidden sm:block"><Avatar size={32} busy={busy} /></div>
      <div className="min-w-0 max-w-full flex-1 sm:max-w-[85%]">
        {message.steps?.length > 0 && (
          <Steps steps={message.steps} disabled={chatBusy} onResolve={(pid, ok) => onResolve(message.id, pid, ok)} />
        )}
        {(message.content || busy) && !awaiting && (
          <div className="glass rounded-2xl rounded-tl-md px-4 py-3">
            {message.content ? (
              <div className="md">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents} skipHtml>
                  {message.content}
                </ReactMarkdown>
              </div>
            ) : <TypingIndicator />}
          </div>
        )}
        {!busy && message.sources?.length > 0 && <Sources sources={message.sources} />}
        {!busy && message.content && !awaiting && (
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <IconButton label={copied ? "Copied" : "Copy response"} onClick={() => copy(message.content)} side="top">
              {copied ? <Check size={17} className="text-emerald-400" /> : <Copy size={17} />}
            </IconButton>
            {voice.output && (
              <IconButton
                label={voice.speakingId === message.id ? "Stop reading" : "Read aloud"} side="top"
                onClick={() => (voice.speakingId === message.id ? voice.stop() : voice.speak(message.id, message.content))}
              >
                {voice.speakingId === message.id ? <VolumeX size={17} className="text-volt" /> : <Volume2 size={17} />}
              </IconButton>
            )}
            {!String(message.id).startsWith("partial-") && (
              <>
                <IconButton label="Good response" onClick={() => onRate(message.id, 1)} aria-pressed={message.rating === 1} side="top">
                  <ThumbsUp size={17} className={message.rating === 1 ? "text-emerald-400" : ""} fill={message.rating === 1 ? "currentColor" : "none"} />
                </IconButton>
                <IconButton label="Bad response" onClick={() => onRate(message.id, -1)} aria-pressed={message.rating === -1} side="top">
                  <ThumbsDown size={17} className={message.rating === -1 ? "text-red-400" : ""} fill={message.rating === -1 ? "currentColor" : "none"} />
                </IconButton>
              </>
            )}
            {isLast && !chatBusy && (
              <IconButton label="Regenerate response" onClick={onRegenerate} side="top">
                <RefreshCw size={17} />
              </IconButton>
            )}
            <Stamp message={message} />
          </div>
        )}
      </div>
    </motion.div>
  );
}

export default memo(ChatMessage);
