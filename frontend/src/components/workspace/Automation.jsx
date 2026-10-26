import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Pause, Play, Plus, X } from "lucide-react";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";
import { Badge, Card, Err, Skeleton, field, secondary, timeZones, useAction, when } from "./ui";

const MODES = [
  ["assisted", "1 · Assisted (recommended)", "Nova reads and summarises, prepares drafts and suggestions. Everything that changes something asks you first."],
  ["approval", "2 · Approval-based automation", "Low-risk mailbox actions you pick below run on their own. Sending, forwarding and meeting changes still ask you."],
  ["policy", "3 · Policy-based routines", "Also runs the narrow routines you define below (for example your out-of-office when your own leave is confirmed)."],
];
const DAYS = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"], ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];
const CHANNELS = [["app", "In the app"], ["email", "Email"], ["whatsapp", "WhatsApp"], ["teams", "Teams"]];

export default function Automation({ status, onSaved }) {
  const toast = useToast();
  const [p, setP] = useState(status.prefs);
  const [jobs, setJobs] = useState(null);
  const [holiday, setHoliday] = useState("");
  const a = useAction();
  const zones = useMemo(() => timeZones(p.timezone), [p.timezone]);
  const loadJobs = useCallback(() => api.ws.jobs().then(setJobs).catch(() => setJobs([])), []);
  useEffect(() => { loadJobs(); }, [loadJobs]);
  const save = (changes, msg = "Saved.") => a.run(async () => { const next = await api.ws.savePrefs(changes); setP(next); onSaved?.(); loadJobs(); toast.success(msg); });
  const job = (kind, changes) => a.run(async () => { const j = await api.ws.saveJob(kind, changes); setJobs((l) => l.map((x) => (x.kind === kind ? j : x))); });
  const runJob = (kind) => a.run(async () => { const r = await api.ws.runJob(kind); toast.info(`Ran: ${r.outcome}`); loadJobs(); });
  const pol = p.policies;
  return (
    <div className="space-y-4">
      <Card title="Automation mode" description="Nova never switches modes or widens permissions by itself. Every change is recorded in the audit log."
        actions={(
          <button type="button" onClick={() => save({ paused: !p.paused }, p.paused ? "Automation resumed." : "All automation paused.")} disabled={a.busy}
            className={`${secondary} ${p.paused ? "border-amber-400/60 text-amber-400" : ""}`}>{p.paused ? <><Play size={15} /> Resume automation</> : <><Pause size={15} /> Pause all automation</>}</button>
        )}>
        {p.paused && <p className="mb-3 rounded-xl bg-amber-500/10 p-2 text-sm text-amber-300">Paused: nothing runs on its own and scheduled messages wait. Everything asks for approval.</p>}
        <fieldset className="space-y-2"><legend className="sr-only">Mode</legend>
          {MODES.map(([v, l, d]) => (
            <label key={v} className={`flex cursor-pointer gap-3 rounded-xl border p-3 ${p.mode === v ? "border-accent bg-accent/10" : "border-line/15"}`}>
              <input type="radio" name="mode" value={v} checked={p.mode === v} onChange={() => save({ mode: v }, "Mode changed.")} className="mt-1" />
              <span><span className="block text-sm font-semibold">{l}</span><span className="block text-xs text-muted">{d}</span></span>
            </label>
          ))}
        </fieldset>
        {p.mode !== "assisted" && (
          <fieldset className="mt-3 space-y-1 rounded-xl border border-line/15 p-3"><legend className="px-1 text-sm font-semibold">Allowed without asking</legend>
            {Object.entries(status.auto_actions).map(([k, label]) => (
              <label key={k} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={p.auto_actions.includes(k)}
                onChange={(e) => save({ auto_actions: e.target.checked ? [...p.auto_actions, k] : p.auto_actions.filter((x) => x !== k) })} /> {label}</label>
            ))}
          </fieldset>
        )}
        {p.mode === "policy" && (
          <form className="mt-3 space-y-2 rounded-xl border border-line/15 p-3" aria-label="Routines"
            onSubmit={(e) => { e.preventDefault(); save({ policies: pol }, "Routine saved."); }}>
            <label className="flex items-center gap-2 text-sm font-semibold"><input type="checkbox" checked={pol.ooo_on_own_absence}
              onChange={(e) => setP((x) => ({ ...x, policies: { ...pol, ooo_on_own_absence: e.target.checked } }))} /> Turn on my out-of-office when my own leave is confirmed</label>
            <textarea value={pol.ooo_internal} onChange={(e) => setP((x) => ({ ...x, policies: { ...pol, ooo_internal: e.target.value } }))} rows={2} aria-label="Reply to colleagues"
              placeholder="Reply to colleagues (required)" className={`${field} py-2`} />
            <textarea value={pol.ooo_external} onChange={(e) => setP((x) => ({ ...x, policies: { ...pol, ooo_external: e.target.value } }))} rows={2} aria-label="Reply to outside people"
              placeholder="Reply to outside people (optional)" className={`${field} py-2`} />
            <input type="email" value={pol.ooo_contact} onChange={(e) => setP((x) => ({ ...x, policies: { ...pol, ooo_contact: e.target.value } }))} aria-label="Alternate contact"
              placeholder="Alternate contact (optional)" className={field} />
            <button type="submit" disabled={a.busy} className={secondary}>Save routine</button>
          </form>
        )}
        <div className="mt-2"><Err error={a.error} /></div>
      </Card>

      <Card title="Time and availability" description="Used for your schedule, free-slot search, reminders and quiet hours.">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-muted">Time zone
            <select value={p.timezone || ""} onChange={(e) => save({ timezone: e.target.value })} className={`${field} mt-1`}>
              <option value="">(not set: UTC)</option>{zones.map((z) => <option key={z} value={z}>{z}</option>)}
            </select></label>
          <label className="text-xs text-muted">Default calendar for new meetings
            <select value={p.default_calendar} onChange={(e) => save({ default_calendar: e.target.value })} className={`${field} mt-1`}>
              <option value="">Automatic</option><option value="google">Google Calendar</option><option value="microsoft">Outlook</option>
            </select></label>
          <div className="text-xs text-muted">Working hours
            <div className="mt-1 flex items-center gap-2">
              <input type="time" aria-label="Work starts" value={p.working_hours.start} onChange={(e) => save({ working_hours: { ...p.working_hours, start: e.target.value } })} className={field} />
              <span>to</span>
              <input type="time" aria-label="Work ends" value={p.working_hours.end} onChange={(e) => save({ working_hours: { ...p.working_hours, end: e.target.value } })} className={field} />
            </div></div>
          <div className="text-xs text-muted">Quiet hours (no email/WhatsApp/Teams alerts)
            <div className="mt-1 flex items-center gap-2">
              <input type="time" aria-label="Quiet from" value={p.quiet_hours.start} onChange={(e) => save({ quiet_hours: { ...p.quiet_hours, start: e.target.value } })} className={field} />
              <span>to</span>
              <input type="time" aria-label="Quiet until" value={p.quiet_hours.end} onChange={(e) => save({ quiet_hours: { ...p.quiet_hours, end: e.target.value } })} className={field} />
            </div></div>
        </div>
        <fieldset className="mt-3"><legend className="text-xs text-muted">Working days</legend>
          <div className="mt-1 flex flex-wrap gap-1">
            {DAYS.map(([d, l]) => {
              const on = p.working_days.includes(d);
              return <button key={d} type="button" aria-pressed={on} onClick={() => save({ working_days: on ? p.working_days.filter((x) => x !== d) : [...p.working_days, d] })}
                className={`min-h-10 min-w-12 rounded-xl border px-2 text-sm ${on ? "border-accent bg-accent/15 font-semibold" : "border-line/15 text-muted"}`}>{l}</button>;
            })}
          </div></fieldset>
        <div className="mt-3 text-xs text-muted">Holidays (no meetings suggested, no scheduled jobs)
          <div className="mt-1 flex flex-wrap gap-1">
            {p.holidays.map((h) => <Badge key={h}>{h} <button type="button" aria-label={`Remove ${h}`} onClick={() => save({ holidays: p.holidays.filter((x) => x !== h) })}><X size={11} /></button></Badge>)}
          </div>
          <div className="mt-1 flex gap-2"><input type="date" value={holiday} onChange={(e) => setHoliday(e.target.value)} aria-label="Holiday date" className={field} />
            <button type="button" disabled={!holiday} onClick={() => { save({ holidays: [...p.holidays, holiday] }); setHoliday(""); }} className={secondary}><Plus size={14} /> Add</button></div>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-muted">Remind me before meetings (minutes)
            <input type="number" min={1} max={240} value={p.reminder_minutes} onChange={(e) => setP((x) => ({ ...x, reminder_minutes: Number(e.target.value) }))}
              onBlur={() => save({ reminder_minutes: p.reminder_minutes })} className={`${field} mt-1`} /></label>
          <label className="text-xs text-muted">Unanswered email follow-up after (days)
            <input type="number" min={1} max={60} value={p.followup_days} onChange={(e) => setP((x) => ({ ...x, followup_days: Number(e.target.value) }))}
              onBlur={() => save({ followup_days: p.followup_days })} className={`${field} mt-1`} /></label>
        </div>
        <fieldset className="mt-3"><legend className="text-xs text-muted">Notify me by</legend>
          <div className="mt-1 flex flex-wrap gap-4 text-sm">
            {CHANNELS.map(([c, l]) => (
              <label key={c} className="flex items-center gap-2"><input type="checkbox" checked={p.notify_channels.includes(c)} disabled={c === "app"}
                onChange={(e) => save({ notify_channels: e.target.checked ? [...p.notify_channels, c] : p.notify_channels.filter((x) => x !== c) })} /> {l}</label>
            ))}
          </div>
          <p className="mt-1 text-xs text-muted">WhatsApp and Teams alerts need those apps linked in Account &gt; Apps.</p>
        </fieldset>
      </Card>

      <Card title="Scheduled jobs" description="Each runs once per scheduled time, even with several servers, and retries safely if a provider is down.">
        {!jobs ? <Skeleton /> : (
          <ul className="space-y-2" aria-label="Scheduled jobs">
            {jobs.map((j) => (
              <li key={j.kind} className="flex flex-wrap items-center gap-2 rounded-xl border border-line/10 p-3 text-sm">
                <label className="flex min-w-0 flex-1 items-center gap-2"><input type="checkbox" checked={j.enabled} onChange={(e) => job(j.kind, { enabled: e.target.checked })} />
                  <span className="min-w-0"><span className="block font-medium">{j.label}</span>
                    <span className="block text-xs text-muted">{j.config.every ? `every ${j.config.every} min` : `${j.config.days === "work" ? "working days" : j.config.days} at ${j.config.time}`}
                      {j.enabled && j.next_run_at ? ` · next ${when(j.next_run_at)}` : ""}{j.last_status ? ` · last: ${j.last_status === "ok" ? j.last_error || "ok" : j.last_error}` : ""}</span></span></label>
                {j.config.time && <input type="time" aria-label={`${j.label} time`} value={j.config.time} onChange={(e) => job(j.kind, { time: e.target.value })} className={`${field} !w-28`} />}
                <button type="button" onClick={() => runJob(j.kind)} disabled={a.busy} className={secondary}>{a.busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Run now</button>
              </li>
            ))}
          </ul>
        )}
        {!status.jobs_running && <p className="mt-2 text-xs text-amber-400">The background runner is off on this server (WORKSPACE_JOBS=false); an administrator runs jobs with cron.</p>}
      </Card>
    </div>
  );
}
