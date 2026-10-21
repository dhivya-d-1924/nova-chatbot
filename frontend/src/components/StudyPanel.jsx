import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle, ArrowLeft, BookOpen, CalendarDays, CheckCircle2, Circle, Download, FileText, HelpCircle, Loader2,
  MessageSquare, Plus, RotateCcw, Sparkles, Trash2, UploadCloud, X,
} from "lucide-react";
import { api } from "../lib/api";
import { formatWhen, useDialog } from "../lib/hooks";
import { overlay, spring } from "../lib/motion";
import { useToast } from "../lib/toast";

const input = "min-h-11 w-full rounded-xl border border-line/15 bg-ink/5 px-3 text-base placeholder:text-muted/70 focus:border-accent focus:outline-none sm:text-sm";
const primary = "btn-gradient inline-flex min-h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold disabled:opacity-50";
const secondary = "inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-line/20 px-4 text-sm font-medium hover:bg-ink/10 disabled:opacity-50";
const TABS = [["material", "Material", FileText], ["plan", "Plan", CalendarDays], ["notes", "Notes", BookOpen], ["doubts", "Doubts", HelpCircle]];

function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const run = useCallback(async (fn) => {
    setBusy(true);
    setError(null);
    try { return await fn(); } catch (e) { setError(e.message); return undefined; } finally { setBusy(false); }
  }, []);
  return { busy, error, setError, run };
}

const ErrorText = ({ error }) => (error ? (
  <p role="alert" className="flex items-start gap-1.5 text-sm text-red-400"><AlertTriangle size={15} className="mt-0.5 shrink-0" /> {error}</p>
) : null);

// ------------------------------------------------------------------ subject list
function SubjectList({ onOpen }) {
  const [list, setList] = useState(null);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [doubts, setDoubts] = useState(null);
  const a = useAction();
  const load = useCallback(() => a.run(async () => {
    const [s, d] = await Promise.all([api.subjects(), api.doubts({ status: "open" })]);
    setList(s);
    setDoubts(d);
  }), [a.run]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  const create = (e) => {
    e.preventDefault();
    a.run(async () => { const s = await api.createSubject(name.trim(), desc.trim()); setName(""); setDesc(""); onOpen(s.id, "material"); });
  };
  return (
    <div className="space-y-4">
      <form onSubmit={create} className="rounded-2xl border border-line/15 p-4">
        <h3 className="font-display text-sm font-semibold">New subject</h3>
        <p className="mt-1 text-xs text-muted">Add your syllabus and notes, get a day-by-day plan, and chat with a tutor that answers only from your material.</p>
        <div className="mt-3 space-y-2">
          <label htmlFor="subj-name" className="sr-only">Subject name</label>
          <input id="subj-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="Subject name, e.g. Biology" className={input} />
          <label htmlFor="subj-desc" className="sr-only">Description</label>
          <input id="subj-desc" value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={1000} placeholder="Class or exam (optional)" className={input} />
          <button type="submit" disabled={a.busy || !name.trim()} className={primary}><Plus size={16} /> Create subject</button>
        </div>
      </form>
      <ErrorText error={a.error} />
      {!list ? <div className="h-24 animate-pulse rounded-2xl bg-ink/10" aria-busy="true" /> : list.length === 0 ? (
        <p className="text-center text-sm text-muted">No subjects yet. Create your first one above.</p>
      ) : (
        <ul className="space-y-2" aria-label="Your subjects">
          {list.map((s) => (
            <li key={s.id}>
              <button type="button" onClick={() => onOpen(s.id)} className="flex w-full items-center gap-3 rounded-2xl border border-line/15 p-4 text-left hover:bg-ink/5">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/15 text-accent"><BookOpen size={19} /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{s.name}</span>
                  <span className="block text-xs text-muted">
                    {s.materials} file{s.materials === 1 ? "" : "s"}
                    {s.plan_progress ? ` · plan ${s.plan_progress.done}/${s.plan_progress.days} days` : " · no plan yet"}
                    {s.open_doubts ? ` · ${s.open_doubts} open doubt${s.open_doubts === 1 ? "" : "s"}` : ""}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {doubts?.doubts?.length > 0 && (
        <section className="rounded-2xl border border-amber-400/30 bg-amber-400/5 p-4">
          <h3 className="flex items-center gap-2 font-display text-sm font-semibold"><HelpCircle size={16} /> Open doubts index ({doubts.counts.open})</h3>
          <ul className="mt-2 space-y-1.5 text-sm">
            {doubts.doubts.slice(0, 8).map((d) => (
              <li key={d.id}>
                <button type="button" onClick={() => onOpen(d.subject_id, "doubts")} className="w-full rounded-lg px-2 py-1.5 text-left hover:bg-ink/10">
                  <span className="break-words">{d.question}</span> <span className="text-xs text-muted">· {d.subject_name}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ material
function MaterialTab({ subject, setSubject }) {
  const [docs, setDocs] = useState([]);
  const [pick, setPick] = useState("");
  const [role, setRole] = useState("material");
  const [progress, setProgress] = useState(null);
  const fileRef = useRef(null);
  const a = useAction();
  useEffect(() => { api.listDocuments().then(setDocs).catch(() => setDocs([])); }, [subject.documents.length]);
  const attached = new Set(subject.documents.map((d) => d.id));
  const available = docs.filter((d) => !attached.has(d.id));
  const attach = (docId, r = role) => a.run(async () => { setSubject(await api.attachSubjectDoc(subject.id, docId, r)); setPick(""); });
  const upload = (files) => a.run(async () => {
    for (const file of Array.from(files || [])) {
      setProgress({ name: file.name, p: 0 });
      const d = await api.uploadDocument(file, (p) => setProgress({ name: file.name, p }));
      setSubject(await api.attachSubjectDoc(subject.id, d.id, role));
    }
    setProgress(null);
  }).finally(() => { setProgress(null); if (fileRef.current) fileRef.current.value = ""; });
  return (
    <div className="space-y-4">
      {subject.documents.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line/25 p-4 text-sm text-muted">Add your <strong>syllabus</strong> (for the study plan) and your <strong>study material</strong> (notes, textbook chapters). The tutor answers only from these files.</p>
      ) : (
        <ul className="space-y-2" aria-label="Files in this subject">
          {subject.documents.map((d) => (
            <li key={d.id} className="flex items-center gap-3 rounded-xl border border-line/10 p-3 text-sm">
              <FileText size={17} className="shrink-0 text-muted" aria-hidden="true" />
              <span className="min-w-0 flex-1 truncate">{d.filename}</span>
              <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${d.role === "syllabus" ? "bg-volt/20 text-volt" : "bg-ink/10 text-muted"}`}>{d.role === "syllabus" ? "Syllabus" : "Material"}</span>
              <button type="button" onClick={() => a.run(async () => setSubject(await api.detachSubjectDoc(subject.id, d.id)))} disabled={a.busy}
                aria-label={`Remove ${d.filename} from ${subject.name}`} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-red-500/10 hover:text-red-400"><X size={15} /></button>
            </li>
          ))}
        </ul>
      )}
      <fieldset className="space-y-2 rounded-2xl border border-line/15 p-4">
        <legend className="px-1 text-sm font-semibold">Add a file</legend>
        <div className="flex gap-2 text-sm" role="radiogroup" aria-label="File type">
          {[["syllabus", "Syllabus"], ["material", "Study material"]].map(([v, l]) => (
            <label key={v} className={`flex min-h-10 cursor-pointer items-center gap-2 rounded-xl border px-3 ${role === v ? "border-accent bg-accent/10" : "border-line/15"}`}>
              <input type="radio" name="doc-role" value={v} checked={role === v} onChange={() => setRole(v)} /> {l}
            </label>
          ))}
        </div>
        <input ref={fileRef} type="file" className="sr-only" id="study-upload" accept=".pdf,.docx,.txt,.md,.csv,.png,.jpg,.jpeg,.webp"
          onChange={(e) => upload(e.target.files)} multiple />
        <label htmlFor="study-upload" className={`${secondary} w-full cursor-pointer`}><UploadCloud size={16} /> Upload from this device</label>
        {progress && <p className="flex items-center gap-2 text-xs text-muted" aria-live="polite"><Loader2 size={13} className="animate-spin" /> {progress.name} · {progress.p >= 100 ? "reading the file…" : `${progress.p}%`}</p>}
        {available.length > 0 && (
          <div className="flex flex-col gap-2 sm:flex-row">
            <label htmlFor="study-pick" className="sr-only">Choose from your documents</label>
            <select id="study-pick" value={pick} onChange={(e) => setPick(e.target.value)} className={input}>
              <option value="">Choose from your documents…</option>
              {available.map((d) => <option key={d.id} value={d.id}>{d.filename}</option>)}
            </select>
            <button type="button" onClick={() => attach(pick)} disabled={!pick || a.busy} className={secondary}>Add</button>
          </div>
        )}
      </fieldset>
      <ErrorText error={a.error} />
    </div>
  );
}

// ------------------------------------------------------------------ plan
function PlanTab({ subject, setSubject, onNotesForDay }) {
  const [days, setDays] = useState(subject.plan?.day_count || 5);
  const [minutes, setMinutes] = useState(subject.plan?.minutes_per_day || 120);
  const [goal, setGoal] = useState("");
  const [showForm, setShowForm] = useState(!subject.plan);
  const a = useAction();
  const plan = subject.plan;
  const generate = (e) => {
    e.preventDefault();
    a.run(async () => { const p = await api.makePlan(subject.id, Number(days), Number(minutes), goal.trim() || undefined); setSubject({ ...subject, plan: p }); setShowForm(false); });
  };
  const toggle = (d) => a.run(async () => setSubject({ ...subject, plan: await api.setPlanDay(subject.id, d.day, !d.done) }));
  return (
    <div className="space-y-4">
      {showForm ? (
        <form onSubmit={generate} className="space-y-3 rounded-2xl border border-line/15 p-4">
          <p className="text-sm text-muted">{subject.has_syllabus ? "The plan is built from your syllabus." : "No syllabus added: the plan is built from your study material."}</p>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-muted">Days
              <input type="number" min={1} max={14} value={days} onChange={(e) => setDays(e.target.value)} className={`${input} mt-1`} />
            </label>
            <label className="text-xs text-muted">Minutes per day
              <input type="number" min={15} max={720} step={15} value={minutes} onChange={(e) => setMinutes(e.target.value)} className={`${input} mt-1`} />
            </label>
          </div>
          <label className="block text-xs text-muted">Goal (optional)
            <input value={goal} onChange={(e) => setGoal(e.target.value)} maxLength={300} placeholder="e.g. Unit test on Friday" className={`${input} mt-1`} />
          </label>
          <div className="flex flex-wrap gap-2">
            <button type="submit" disabled={a.busy || subject.materials === 0} className={primary}>
              {a.busy ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />} {a.busy ? "Making your plan…" : `Make ${days}-day plan`}
            </button>
            {plan && <button type="button" onClick={() => setShowForm(false)} className={secondary}>Cancel</button>}
          </div>
          {subject.materials === 0 && <p className="text-xs text-amber-400">Add your syllabus or material first (Material tab).</p>}
        </form>
      ) : plan && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm"><strong>{plan.done}</strong> of {plan.day_count} days done</p>
          <button type="button" onClick={() => setShowForm(true)} className={secondary}><RotateCcw size={15} /> New plan</button>
        </div>
      )}
      {plan && (
        <ol className="space-y-3" aria-label="Study plan">
          {plan.days.map((d) => (
            <li key={d.day} className={`rounded-2xl border p-4 ${d.done ? "border-emerald-400/30 bg-emerald-500/5" : "border-line/15"}`}>
              <div className="flex items-start gap-3">
                <button type="button" onClick={() => toggle(d)} disabled={a.busy} aria-pressed={d.done} aria-label={`Mark day ${d.day} ${d.done ? "not done" : "done"}`}
                  className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg hover:bg-ink/10">
                  {d.done ? <CheckCircle2 size={20} className="text-emerald-400" /> : <Circle size={20} className="text-muted" />}
                </button>
                <div className="min-w-0 flex-1">
                  <p className="font-display font-semibold">Day {d.day}: {d.title}</p>
                  <p className="text-xs text-muted">{d.minutes} minutes</p>
                  {d.topics.length > 0 && <p className="mt-2 text-sm"><span className="text-muted">Topics: </span>{d.topics.join(", ")}</p>}
                  {d.goals.length > 0 && <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm">{d.goals.map((g, i) => <li key={i}>{g}</li>)}</ul>}
                  {d.tasks.length > 0 && (
                    <ul className="mt-2 space-y-1 text-sm">{d.tasks.map((t, i) => <li key={i} className="flex gap-2"><span className="text-accent" aria-hidden="true">→</span><span>{t}</span></li>)}</ul>
                  )}
                  <button type="button" onClick={() => onNotesForDay(d.day)} className="mt-3 inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-xs font-semibold text-accent hover:bg-accent/10">
                    <Sparkles size={13} /> Study material for this day
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
      <ErrorText error={a.error} />
    </div>
  );
}

// ------------------------------------------------------------------ notes
function NotesTab({ subject, autoDay, onConsumedDay, onChanged }) {
  const [list, setList] = useState(null);
  const [topic, setTopic] = useState("");
  const [open, setOpen] = useState(null);
  const a = useAction();
  const load = useCallback(() => api.studyNotes(subject.id).then(setList).catch(() => setList([])), [subject.id]);
  useEffect(() => { load(); }, [load]);
  const generate = useCallback((args) => a.run(async () => {
    const n = await api.makeNotes(subject.id, args);
    setOpen(n);
    setTopic("");
    await load();
  }).finally(() => onChanged?.()), [a.run, subject.id, load, onChanged]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (autoDay) { onConsumedDay(); generate({ day: autoDay }); }
  }, [autoDay, onConsumedDay, generate]);
  const show = (n) => a.run(async () => setOpen(await api.getNote(subject.id, n.id)));
  const remove = (n) => a.run(async () => { await api.deleteNote(subject.id, n.id); if (open?.id === n.id) setOpen(null); await load(); });
  const download = (n) => {
    const url = URL.createObjectURL(new Blob([`# ${n.title}\n\n${n.content}\n`], { type: "text/markdown" }));
    const link = Object.assign(document.createElement("a"), { href: url, download: `${n.title.replace(/[^\w\- ]+/g, "").trim() || "notes"}.md` });
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  if (open) {
    return (
      <article className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => setOpen(null)} className={secondary}><ArrowLeft size={15} /> All notes</button>
          <button type="button" onClick={() => download(open)} className={secondary}><Download size={15} /> Download</button>
        </div>
        <h3 className="font-display text-lg font-semibold">{open.title}</h3>
        <div className="prose-chat break-words text-sm leading-relaxed [&_h2]:mt-4 [&_h2]:font-display [&_h2]:font-semibold [&_li]:ml-5 [&_ol]:list-decimal [&_ul]:list-disc">
          <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{open.content}</ReactMarkdown>
        </div>
        {open.sources?.length > 0 && (
          <div className="rounded-xl bg-ink/5 p-3 text-xs text-muted">
            <p className="mb-1 font-semibold">Sources</p>
            <ul className="space-y-0.5">{open.sources.map((s) => <li key={s.n}>[{s.n}] {s.filename}, {["pdf", "image"].includes(s.kind) ? "page" : "section"} {s.page}</li>)}</ul>
          </div>
        )}
      </article>
    );
  }
  return (
    <div className="space-y-4">
      <form onSubmit={(e) => { e.preventDefault(); generate({ topic: topic.trim() }); }} className="space-y-2 rounded-2xl border border-line/15 p-4">
        <label htmlFor="notes-topic" className="text-sm font-semibold">Make study material</label>
        <p className="text-xs text-muted">Notes, key terms, a quick revision list and practice questions, written only from your files.</p>
        <input id="notes-topic" value={topic} onChange={(e) => setTopic(e.target.value)} maxLength={300} placeholder="Topic, e.g. Photosynthesis" className={input} />
        <button type="submit" disabled={a.busy || !topic.trim()} className={primary}>
          {a.busy ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />} {a.busy ? "Writing…" : "Make notes"}
        </button>
      </form>
      <ErrorText error={a.error} />
      {!list ? <div className="h-16 animate-pulse rounded-xl bg-ink/10" aria-busy="true" /> : list.length === 0 ? (
        <p className="text-center text-sm text-muted">No study material yet. Make some above, or from a day in your plan.</p>
      ) : (
        <ul className="space-y-2" aria-label="Study notes">
          {list.map((n) => (
            <li key={n.id} className="flex items-center gap-2 rounded-xl border border-line/10 p-2 pl-3 text-sm">
              <button type="button" onClick={() => show(n)} className="min-w-0 flex-1 py-1 text-left">
                <span className="block truncate font-medium">{n.title}</span>
                <span className="block text-xs text-muted">{n.day ? `Day ${n.day} · ` : ""}{formatWhen(n.created_at)}</span>
              </button>
              <button type="button" onClick={() => remove(n)} disabled={a.busy} aria-label={`Delete notes ${n.title}`} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-red-500/10 hover:text-red-400"><Trash2 size={15} /></button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ doubts index
function DoubtsTab({ subject, onChanged }) {
  const [status, setStatus] = useState("open");
  const [data, setData] = useState(null);
  const [question, setQuestion] = useState("");
  const [notes, setNotes] = useState({});
  const a = useAction();
  const load = useCallback(() => api.doubts({ subject_id: subject.id, status }).then(setData).catch(() => setData({ doubts: [], counts: {} })), [subject.id, status]);
  useEffect(() => { load(); }, [load]);
  const after = async () => { await load(); onChanged?.(); };
  const add = (e) => { e.preventDefault(); a.run(async () => { await api.addDoubt(subject.id, question.trim()); setQuestion(""); await after(); }); };
  const set = (d, changes) => a.run(async () => { await api.updateDoubt(d.id, changes); await after(); });
  const remove = (d) => a.run(async () => { await api.deleteDoubt(d.id); await after(); });
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">Questions your material couldn't answer are added here automatically. Ask your teacher, add material that covers them, then mark them resolved.</p>
      <form onSubmit={add} className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor="doubt-q" className="sr-only">Add a doubt</label>
        <input id="doubt-q" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={2000} placeholder="Write down a doubt…" className={input} />
        <button type="submit" disabled={a.busy || !question.trim()} className={secondary}><Plus size={15} /> Add</button>
      </form>
      <div role="tablist" aria-label="Doubt status" className="grid grid-cols-3 gap-1 rounded-xl bg-ink/5 p-1 text-sm">
        {[["open", "Open"], ["resolved", "Resolved"], ["all", "All"]].map(([v, l]) => (
          <button key={v} type="button" role="tab" aria-selected={status === v} onClick={() => setStatus(v)}
            className={`min-h-10 rounded-lg ${status === v ? "bg-panel font-semibold shadow" : "text-muted hover:text-ink"}`}>
            {l}{data?.counts && v !== "all" ? ` (${data.counts[v] ?? 0})` : ""}
          </button>
        ))}
      </div>
      <ErrorText error={a.error} />
      {!data ? <div className="h-16 animate-pulse rounded-xl bg-ink/10" aria-busy="true" /> : data.doubts.length === 0 ? (
        <p className="text-center text-sm text-muted">{status === "open" ? "No open doubts. 🎉" : "Nothing here yet."}</p>
      ) : (
        <ul className="space-y-2" aria-label="Doubts">
          {data.doubts.map((d) => (
            <li key={d.id} className="rounded-xl border border-line/10 p-3 text-sm">
              <p className="break-words font-medium">{d.question}</p>
              <p className="mt-0.5 text-xs text-muted">{d.origin === "chat" ? "From the study chat" : "Added by you"} · {formatWhen(d.created_at)}
                {d.status === "resolved" && d.resolved_at ? ` · resolved ${formatWhen(d.resolved_at)}` : ""}</p>
              {d.note && <p className="mt-2 whitespace-pre-wrap rounded-lg bg-emerald-500/10 p-2 text-xs">{d.note}</p>}
              {d.status === "open" ? (
                <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                  <label htmlFor={`note-${d.id}`} className="sr-only">Answer or note</label>
                  <input id={`note-${d.id}`} value={notes[d.id] || ""} onChange={(e) => setNotes((n) => ({ ...n, [d.id]: e.target.value }))}
                    placeholder="Answer or note (optional)" maxLength={4000} className={input} />
                  <button type="button" onClick={() => set(d, { status: "resolved", note: notes[d.id] || undefined })} disabled={a.busy} className={secondary}><CheckCircle2 size={15} /> Resolve</button>
                </div>
              ) : (
                <button type="button" onClick={() => set(d, { status: "open" })} disabled={a.busy} className="mt-2 inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-xs font-semibold text-muted hover:bg-ink/10"><RotateCcw size={13} /> Reopen</button>
              )}
              <button type="button" onClick={() => remove(d)} disabled={a.busy} className="mt-1 inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-xs text-red-400 hover:bg-red-500/10"><Trash2 size={13} /> Delete</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ subject view
function SubjectView({ id, initialTab, onBack, onOpenChat }) {
  const toast = useToast();
  const [subject, setSubject] = useState(null);
  const [tab, setTab] = useState(initialTab || "material");
  const [autoDay, setAutoDay] = useState(null);
  const a = useAction();
  const reload = useCallback(() => api.getSubject(id).then(setSubject).catch((e) => a.setError(e.message)), [id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, [reload]);
  const consumeDay = useCallback(() => setAutoDay(null), []);
  if (!subject) return a.error ? <ErrorText error={a.error} /> : <div className="h-32 animate-pulse rounded-2xl bg-ink/10" aria-busy="true" />;
  const startChat = () => a.run(async () => { const conv = await api.startStudyChat(subject.id); onOpenChat(conv); });
  const remove = () => a.run(async () => {
    if (!window.confirm(`Delete "${subject.name}"? Its plan, notes and doubts are removed. Your files stay in Your documents.`)) return;
    await api.deleteSubject(subject.id);
    toast.success("Subject deleted.");
    onBack();
  });
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2">
        <button type="button" onClick={onBack} aria-label="All subjects" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-muted hover:bg-ink/10 hover:text-ink"><ArrowLeft size={19} /></button>
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-display text-lg font-semibold">{subject.name}</h3>
          {subject.description && <p className="truncate text-sm text-muted">{subject.description}</p>}
        </div>
        <button type="button" onClick={remove} aria-label={`Delete ${subject.name}`} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-muted hover:bg-red-500/10 hover:text-red-400"><Trash2 size={17} /></button>
      </div>
      <button type="button" onClick={startChat} disabled={a.busy || subject.materials === 0} className={`${primary} w-full`}>
        <MessageSquare size={16} /> Ask the {subject.name} tutor
      </button>
      {subject.materials === 0 && <p className="-mt-2 text-center text-xs text-muted">Add material first: the tutor answers only from your files.</p>}
      <div role="tablist" aria-label="Subject sections" className="grid grid-cols-4 gap-1 rounded-xl bg-ink/5 p-1 text-sm">
        {TABS.map(([v, l, Icon]) => (
          <button key={v} type="button" role="tab" aria-selected={tab === v} aria-controls="study-tabpanel" onClick={() => setTab(v)}
            className={`flex min-h-10 flex-col items-center justify-center gap-0.5 rounded-lg px-1 text-[12px] min-[400px]:flex-row min-[400px]:gap-1.5 min-[400px]:text-sm ${tab === v ? "bg-panel font-semibold shadow" : "text-muted hover:text-ink"}`}>
            <Icon size={15} aria-hidden="true" /> {l}{v === "doubts" && subject.open_doubts ? ` (${subject.open_doubts})` : ""}
          </button>
        ))}
      </div>
      <div id="study-tabpanel" role="tabpanel">
        {tab === "material" && <MaterialTab subject={subject} setSubject={setSubject} />}
        {tab === "plan" && <PlanTab subject={subject} setSubject={setSubject} onNotesForDay={(d) => { setAutoDay(d); setTab("notes"); }} />}
        {tab === "notes" && <NotesTab subject={subject} autoDay={autoDay} onConsumedDay={consumeDay} onChanged={reload} />}
        {tab === "doubts" && <DoubtsTab subject={subject} onChanged={reload} />}
      </div>
      <ErrorText error={a.error} />
    </div>
  );
}

export default function StudyPanel({ open, onClose, target, onOpenChat }) {
  const [view, setView] = useState(null); // { id, tab }
  const ref = useRef(null);
  useDialog(open, onClose, ref);
  useEffect(() => { if (open) setView(target?.id ? { id: target.id, tab: target.tab } : null); }, [open, target]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div {...overlay} className="fixed inset-0 z-[55] flex items-end justify-center bg-black/60 backdrop-blur-sm sm:items-center sm:p-4"
          onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
          <motion.div ref={ref} role="dialog" aria-modal="true" aria-labelledby="study-title"
            initial={{ opacity: 0, y: 40 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 24 }} transition={spring}
            className="glass flex max-h-[94dvh] w-full max-w-2xl flex-col rounded-t-3xl bg-panel pb-[env(safe-area-inset-bottom)] shadow-2xl sm:rounded-3xl">
            <div className="flex items-start gap-3 p-5 pb-3">
              <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/15 text-accent"><BookOpen size={20} /></span>
              <div className="min-w-0 flex-1">
                <h2 id="study-title" className="font-display text-lg font-semibold">Study</h2>
                <p className="text-sm text-muted">Syllabus, day-by-day plan, study material and your doubts.</p>
              </div>
              <button type="button" onClick={onClose} aria-label="Close" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-muted hover:bg-ink/10 hover:text-ink"><X size={20} /></button>
            </div>
            <div className="thin-scroll min-h-0 flex-1 overflow-y-auto px-5 pb-5">
              {view ? (
                <SubjectView key={view.id} id={view.id} initialTab={view.tab} onBack={() => setView(null)} onOpenChat={onOpenChat} />
              ) : (
                <SubjectList onOpen={(id, tab) => setView({ id, tab })} />
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
