import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, CalendarClock, ChevronLeft, ChevronRight, ExternalLink, Loader2, Search, Sparkles, Users, Video } from "lucide-react";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";
import { Badge, Card, Err, Skeleton, field, localInput, primary, secondary, useAction } from "./ui";

const iso = (d) => d.toISOString().slice(0, 10);

function Briefing() {
  const [data, setData] = useState(null);
  const [period, setPeriod] = useState("today");
  const [showFacts, setShowFacts] = useState(false);
  const a = useAction();
  const run = () => a.run(async () => setData(await api.ws.briefing(period)));
  return (
    <Card title="Brief me" description="A summary of your calendar, email, Teams, action items and approvals. Facts and suggestions are kept apart."
      actions={(
        <div className="flex gap-2">
          <label htmlFor="brief-period" className="sr-only">Period</label>
          <select id="brief-period" value={period} onChange={(e) => setPeriod(e.target.value)} className={`${field} !min-h-10 !w-auto`}>
            <option value="today">Today</option><option value="tomorrow">Tomorrow</option>
            <option value="since_yesterday">Since yesterday</option><option value="week">Next 7 days</option><option value="last_week">Past week</option>
          </select>
          <button type="button" onClick={run} disabled={a.busy} className={primary}>
            {a.busy ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />} Brief me
          </button>
        </div>
      )}>
      <Err error={a.error} />
      {data && (
        <div className="space-y-2 text-sm">
          <div className="break-words leading-relaxed [&_li]:ml-5 [&_ul]:list-disc [&_p]:my-1.5 [&_strong]:font-semibold">
            <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{data.summary}</ReactMarkdown>
          </div>
          {data.errors.length > 0 && <p className="flex items-start gap-1.5 text-xs text-amber-400"><AlertTriangle size={13} className="mt-0.5 shrink-0" /> Not checked: {data.errors.join("; ")}</p>}
          <button type="button" onClick={() => setShowFacts((v) => !v)} className="text-xs font-semibold text-accent">{showFacts ? "Hide" : "Show"} the source facts</button>
          {showFacts && <pre className="thin-scroll max-h-72 overflow-auto whitespace-pre-wrap rounded-xl bg-ink/5 p-3 text-xs">{data.facts}</pre>}
        </div>
      )}
    </Card>
  );
}

function UnifiedSearch() {
  const [f, setF] = useState({ q: "", source: "all", from: "", since: "", until: "" });
  const [filters, setFilters] = useState(false);
  const [res, setRes] = useState(null);
  const a = useAction();
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const params = { q: f.q.trim(), source: f.source, from: f.from.trim(), since: f.since, until: f.until };
  const go = (e) => { e.preventDefault(); a.run(async () => setRes(await api.ws.search(params))); };
  const more = () => a.run(async () => {
    const next = await api.ws.search({ ...params, source: "gmail", page: res.next_page });
    setRes((r) => ({ ...r, results: [...r.results, ...next.results], next_page: next.next_page, errors: next.errors }));
  });
  return (
    <Card title="Search email and Teams" description="One search across Gmail and Teams. Every result shows where it came from.">
      <form onSubmit={go} className="space-y-2" role="search">
        <div className="flex gap-2">
          <label htmlFor="ws-search" className="sr-only">Search Gmail and Teams</label>
          <input id="ws-search" type="search" value={f.q} onChange={set("q")} placeholder="e.g. deployment issue, invoice, Priya" className={field} />
          <button type="submit" disabled={a.busy || f.q.trim().length < 2} className={secondary} aria-label="Search">{a.busy ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}</button>
        </div>
        <button type="button" onClick={() => setFilters((v) => !v)} aria-expanded={filters} className="text-xs font-semibold text-accent">{filters ? "Hide filters" : "Filters: source, person, dates"}</button>
        {filters && (
          <div className="grid gap-2 sm:grid-cols-4">
            <div><label htmlFor="ws-src" className="text-xs text-muted">Source</label><select id="ws-src" value={f.source} onChange={set("source")} className={`${field} mt-1`}>
              <option value="all">Gmail and Teams</option><option value="gmail">Gmail only</option><option value="teams">Teams only</option></select></div>
            <div><label htmlFor="ws-from" className="text-xs text-muted">Sender</label><input id="ws-from" value={f.from} onChange={set("from")} placeholder="Name or email" className={`${field} mt-1`} /></div>
            <div><label htmlFor="ws-since" className="text-xs text-muted">From date</label><input id="ws-since" type="date" value={f.since} onChange={set("since")} className={`${field} mt-1`} /></div>
            <div><label htmlFor="ws-until" className="text-xs text-muted">To date</label><input id="ws-until" type="date" value={f.until} onChange={set("until")} className={`${field} mt-1`} /></div>
          </div>
        )}
      </form>
      <div className="mt-2"><Err error={a.error} /></div>
      {res && (
        <ul className="mt-3 space-y-2" aria-label="Search results">
          {res.errors.map((e) => <li key={e} className="text-xs text-amber-400">{e}</li>)}
          {res.results.length === 0 && <li className="text-sm text-muted">Nothing found.</li>}
          {res.results.map((r) => (
            <li key={`${r.source}-${r.id}`} className="rounded-xl border border-line/10 p-3 text-sm">
              <p className="flex flex-wrap items-center gap-2"><Badge tone={r.source === "gmail" ? "bad" : "info"}>{r.source === "gmail" ? "Gmail" : r.channel_id ? "Teams channel" : "Teams chat"}</Badge>
                <span className="min-w-0 break-words font-medium">{r.title}</span></p>
              <p className="mt-1 break-words text-xs text-muted">{r.from} · {r.at}</p>
              <p className="mt-1 break-words text-xs">{r.snippet}</p>
              {r.link && <a href={r.link} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs font-semibold text-accent">Open <ExternalLink size={11} /></a>}
            </li>
          ))}
          {res.next_page && <li><button type="button" onClick={more} disabled={a.busy} className={secondary}>More emails</button></li>}
        </ul>
      )}
    </Card>
  );
}

function FindTime({ onScheduled }) {
  const toast = useToast();
  const [f, setF] = useState({ attendees: "", minutes: 30, title: "", description: "", online: true });
  const [slots, setSlots] = useState(null);
  const [chosen, setChosen] = useState(null);
  const a = useAction();
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const find = (e) => {
    e.preventDefault();
    const start = new Date();
    a.run(async () => { setChosen(null); setSlots(await api.ws.slots({ attendees: f.attendees, minutes: Number(f.minutes), start: localInput(start),
      end: localInput(new Date(start.getTime() + 7 * 86400000)) })); });
  };
  const schedule = () => a.run(async () => {
    await api.ws.proposeMeeting({ title: f.title.trim(), start: chosen.start, end: chosen.end, attendees: f.attendees, description: f.description, online: f.online });
    toast.success("Meeting prepared. Approve it in Approvals to send the invitations.");
    setSlots(null); setChosen(null); setF((x) => ({ ...x, title: "", description: "" }));
    onScheduled?.();
  });
  return (
    <Card title="Find a time and schedule" description="Checks your calendar and colleagues' free/busy inside your working hours. Invitations go out only after you approve.">
      <form onSubmit={find} className="grid gap-2 sm:grid-cols-[1fr_8rem_auto]">
        <label htmlFor="ft-att" className="sr-only">Attendees</label>
        <input id="ft-att" value={f.attendees} onChange={set("attendees")} placeholder="Attendees' emails, comma-separated" className={field} />
        <label htmlFor="ft-min" className="sr-only">Length in minutes</label>
        <select id="ft-min" value={f.minutes} onChange={set("minutes")} className={field}>
          {[15, 30, 45, 60, 90].map((m) => <option key={m} value={m}>{m} min</option>)}
        </select>
        <button type="submit" disabled={a.busy} className={secondary}>{a.busy && !chosen ? <Loader2 size={15} className="animate-spin" /> : <CalendarClock size={15} />} Find times</button>
      </form>
      <div className="mt-2"><Err error={a.error} /></div>
      {slots && (
        <div className="mt-3 space-y-2">
          {slots.unknown.length > 0 && <p className="text-xs text-amber-400">Availability unknown for {slots.unknown.join(", ")} (no access to their calendar).</p>}
          {slots.slots.length === 0 ? <p className="text-sm text-muted">No common free time in the next 7 days.</p> : (
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Free times">
              {slots.slots.map((s) => (
                <button key={s.start} type="button" role="radio" aria-checked={chosen?.start === s.start} onClick={() => setChosen(s)}
                  className={`min-h-10 rounded-xl border px-3 text-sm ${chosen?.start === s.start ? "border-accent bg-accent/15 font-semibold" : "border-line/15 hover:bg-ink/10"}`}>{s.label}</button>
              ))}
            </div>
          )}
          {chosen && (
            <div className="space-y-2 rounded-xl border border-accent/30 bg-accent/5 p-3">
              <label htmlFor="ft-title" className="sr-only">Meeting title</label>
              <input id="ft-title" value={f.title} onChange={set("title")} placeholder="Meeting title" maxLength={300} className={field} />
              <label htmlFor="ft-desc" className="sr-only">Agenda</label>
              <textarea id="ft-desc" value={f.description} onChange={set("description")} rows={3} placeholder="Agenda (optional)" className={`${field} py-2`} />
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.online} onChange={set("online")} /> Add a Teams/Meet link</label>
              <button type="button" onClick={schedule} disabled={a.busy || !f.title.trim()} className={primary}>Prepare invitation</button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export default function Today({ onChanged }) {
  const [date, setDate] = useState(() => iso(new Date()));
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => {
    setData(null);
    api.ws.calendar(date).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, [date]);
  useEffect(() => { load(); }, [load]);
  const shift = (n) => { const d = new Date(`${date}T12:00:00`); d.setDate(d.getDate() + n); setDate(iso(d)); };
  const clash = new Set((data?.conflicts || []).flatMap((c) => [c.a, c.b]));
  const label = new Date(`${date}T12:00:00`).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
  return (
    <div className="space-y-4">
      <Card title={label} description={data ? `Times in ${data.timezone}` : ""}
        actions={(
          <div className="flex items-center gap-1">
            <button type="button" onClick={() => shift(-1)} aria-label="Previous day" className="flex h-10 w-10 items-center justify-center rounded-xl hover:bg-ink/10"><ChevronLeft size={18} /></button>
            <button type="button" onClick={() => setDate(iso(new Date()))} className={secondary}>Today</button>
            <button type="button" onClick={() => shift(1)} aria-label="Next day" className="flex h-10 w-10 items-center justify-center rounded-xl hover:bg-ink/10"><ChevronRight size={18} /></button>
          </div>
        )}>
        <Err error={error} />
        {!data && !error ? <Skeleton /> : data && (
          <div className="space-y-3">
            {data.errors.map((e) => <p key={e} className="text-xs text-amber-400">{e}</p>)}
            {data.conflicts.map((c) => <p key={c.a + c.b} className="flex items-start gap-1.5 rounded-xl bg-red-500/10 p-2 text-sm text-red-300"><AlertTriangle size={15} className="mt-0.5 shrink-0" /> Conflict: {c.text}</p>)}
            {data.away.length > 0 && (
              <p className="flex items-start gap-1.5 rounded-xl bg-amber-500/10 p-2 text-sm"><Users size={15} className="mt-0.5 shrink-0 text-amber-400" />
                Away: {data.away.map((x) => x.person_name || x.person_email).join(", ")}</p>
            )}
            {data.events.length === 0 ? <p className="text-sm text-muted">No meetings.</p> : (
              <ul className="space-y-2" aria-label="Meetings">
                {data.events.map((e) => (
                  <li key={`${e.provider}-${e.id}`} className={`rounded-xl border p-3 text-sm ${clash.has(e.id) ? "border-red-400/40" : "border-line/10"}`}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-muted">{e.all_day ? "All day" : e.local.replace(/^\w{3} \d{2} \w{3} /, "")}</span>
                      <Badge tone={e.provider === "google" ? "ok" : "info"}>{e.provider === "google" ? "Google" : "Outlook"}</Badge>
                      {e.status !== "confirmed" && <Badge tone="warn">{e.status}</Badge>}
                      {e.recurring && <Badge>recurring</Badge>}
                    </div>
                    <p className="mt-1 break-words font-medium">{e.title}</p>
                    <p className="text-xs text-muted">
                      {e.attendees.length ? `${e.attendees.length} attendee${e.attendees.length === 1 ? "" : "s"}` : "Just you"}
                      {e.location ? ` · ${e.location}` : ""}
                    </p>
                    <div className="mt-1 flex flex-wrap gap-3 text-xs">
                      {e.join_url && <a href={e.join_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-semibold text-accent"><Video size={12} /> Join</a>}
                      {e.web_link && <a href={e.web_link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-muted hover:text-ink">Open in calendar <ExternalLink size={11} /></a>}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>
      <Briefing />
      <FindTime onScheduled={onChanged} />
      <UnifiedSearch />
    </div>
  );
}
