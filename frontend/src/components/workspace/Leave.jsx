import { useCallback, useEffect, useState } from "react";
import { CalendarX, Check, Loader2, Mail, Plus, Radar, Send, Trash2, X } from "lucide-react";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";
import { Badge, Card, Err, Skeleton, danger, field, localInput, primary, secondary, useAction } from "./ui";

const KIND = { request: ["Requested", "warn"], announcement: ["Announced", "info"], approved: ["Approved", "ok"] };

function Absence({ a, onChanged }) {
  const toast = useToast();
  const [affected, setAffected] = useState(null);
  const x = useAction();
  const set = (changes) => x.run(async () => { await api.ws.updateAbsence(a.id, changes); onChanged(); });
  return (
    <li className="rounded-xl border border-line/10 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{a.person_name || a.person_email}</span>
        <Badge tone={KIND[a.kind][1]}>{KIND[a.kind][0]}</Badge>
        {a.status === "proposed" && <Badge tone="warn">Needs your check</Badge>}
        <span className="text-xs text-muted">from {a.source}</span>
      </div>
      <p className="text-xs text-muted">{a.person_email} · {a.start_date}{a.end_date !== a.start_date ? ` to ${a.end_date}` : ""}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {a.status === "proposed" ? (
          <>
            <button type="button" onClick={() => set({ status: "confirmed" })} disabled={x.busy} className={secondary}><Check size={14} /> Confirm</button>
            <button type="button" onClick={() => set({ status: "dismissed" })} disabled={x.busy} className={secondary}><X size={14} /> Not an absence</button>
          </>
        ) : (
          <>
            <button type="button" onClick={() => x.run(async () => setAffected(await api.ws.affected(a.id)))} disabled={x.busy} className={secondary}><CalendarX size={14} /> Affected meetings</button>
            <button type="button" disabled={x.busy} className={secondary}
              onClick={() => x.run(async () => { const r = await api.ws.notices(a.id); toast.success(r.created ? `${r.created} notice(s) prepared in Approvals.` : "No other attendees to notify."); onChanged(); })}>
              <Mail size={14} /> Prepare notices
            </button>
            {a.kind !== "approved" && <button type="button" onClick={() => set({ kind: "approved" })} disabled={x.busy} className={secondary}>Mark officially approved</button>}
          </>
        )}
        <button type="button" onClick={() => x.run(async () => { await api.ws.deleteAbsence(a.id); onChanged(); })} disabled={x.busy} aria-label={`Delete absence of ${a.person_email}`} className={danger}><Trash2 size={14} /></button>
      </div>
      <Err error={x.error} />
      {affected && (
        <ul className="mt-2 space-y-1 text-xs">
          {affected.length === 0 ? <li className="text-muted">No meetings affected.</li> : affected.map((e) => <li key={e.id}>• {e.local} — {e.title}</li>)}
        </ul>
      )}
    </li>
  );
}

function OutOfOffice({ status }) {
  const toast = useToast();
  const [s, setS] = useState(null);
  const [preview, setPreview] = useState(null);
  const a = useAction();
  const blank = () => {
    const start = new Date(); start.setDate(start.getDate() + 1); start.setHours(0, 0, 0, 0);
    const end = new Date(start); end.setDate(end.getDate() + 1);
    return { start: localInput(start), end: localInput(end), internal_message: "", external_message: "", alternate_contact: "",
      providers: [status.google.connected && "gmail", status.microsoft.connected && "outlook"].filter(Boolean) };
  };
  useEffect(() => {
    api.ws.ooo().then((o) => setS(o ? { ...o, start: localInput(new Date(o.start_at / 1000)), end: localInput(new Date(o.end_at / 1000)),
      external_message: o.external_message || "", alternate_contact: o.alternate_contact || "" } : blank())).catch(() => setS(blank()));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (!s) return <Skeleton />;
  const set = (k) => (e) => setS((x) => ({ ...x, [k]: e.target.value }));
  const toggle = (p) => setS((x) => ({ ...x, providers: x.providers.includes(p) ? x.providers.filter((y) => y !== p) : [...x.providers, p] }));
  const save = () => a.run(async () => { const o = await api.ws.saveOoo(s); setS((x) => ({ ...x, status: o.status })); setPreview(await api.ws.previewOoo()); });
  const none = !status.google.connected && !status.microsoft.connected;
  return (
    <Card title="Out of office" description="Your own words only: nothing is invented. Gmail and Outlook start and stop it at the times you set.">
      {none ? <p className="text-sm text-muted">Connect Google or Microsoft 365 (Accounts tab) to set automatic replies.</p> : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">Status: <Badge tone={s.status === "on" ? "ok" : "muted"}>{s.status === "on" ? "On" : s.status === "draft" ? "Saved, not on" : "Off"}</Badge>
            {s.applied && Object.entries(s.applied).map(([k, v]) => <Badge key={k} tone={String(v).startsWith("failed") ? "bad" : "info"}>{k}: {v}</Badge>)}</div>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="text-xs text-muted">From<input type="datetime-local" value={s.start} onChange={set("start")} className={`${field} mt-1`} /></label>
            <label className="text-xs text-muted">Until<input type="datetime-local" value={s.end} onChange={set("end")} className={`${field} mt-1`} /></label>
          </div>
          <label className="block text-xs text-muted">Reply to colleagues
            <textarea value={s.internal_message} onChange={set("internal_message")} rows={3} className={`${field} mt-1 py-2`}
              placeholder="Hello, thank you for your message. I'm away and will reply when I'm back." /></label>
          <label className="block text-xs text-muted">Reply to people outside your organisation (leave empty to not reply to them)
            <textarea value={s.external_message} onChange={set("external_message")} rows={3} className={`${field} mt-1 py-2`} /></label>
          <label className="block text-xs text-muted">Alternate contact (optional)
            <input type="email" value={s.alternate_contact} onChange={set("alternate_contact")} placeholder="colleague@company.com" className={`${field} mt-1`} /></label>
          <fieldset className="flex flex-wrap gap-4 text-sm"><legend className="sr-only">Where</legend>
            {status.google.connected && <label className="flex items-center gap-2"><input type="checkbox" checked={s.providers.includes("gmail")} onChange={() => toggle("gmail")} /> Gmail</label>}
            {status.microsoft.connected && <label className="flex items-center gap-2"><input type="checkbox" checked={s.providers.includes("outlook")} onChange={() => toggle("outlook")} /> Outlook</label>}
          </fieldset>
          <Err error={a.error} />
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={save} disabled={a.busy} className={secondary}>Save and preview</button>
            {preview && status.google.connected && <button type="button" onClick={() => a.run(async () => toast.success(`Test sent to ${(await api.ws.testOoo()).sent_to}.`))} disabled={a.busy} className={secondary}><Send size={14} /> Send me a test</button>}
            {preview && s.status !== "on" && <button type="button" onClick={() => a.run(async () => { const r = await api.ws.enableOoo(); toast.success(r.message); setS((x) => ({ ...x, status: r.ooo.status, applied: r.ooo.applied })); })} disabled={a.busy} className={primary}>
              {a.busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} Turn on</button>}
            {s.status === "on" && <button type="button" onClick={() => a.run(async () => { const r = await api.ws.disableOoo(); toast.success(r.message); setS((x) => ({ ...x, status: "off" })); })} disabled={a.busy} className={secondary}>Turn off</button>}
          </div>
          {preview && (
            <div className="space-y-2 rounded-xl bg-ink/5 p-3 text-sm" aria-label="Preview">
              <p className="text-xs text-muted">From {preview.from} until {preview.until}</p>
              <p className="text-xs font-semibold">Colleagues will get:</p><p className="whitespace-pre-wrap">{preview.internal}</p>
              <p className="text-xs font-semibold">Outside people will get:</p><p className="whitespace-pre-wrap">{preview.external}</p>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export default function Leave({ status, onChanged }) {
  const toast = useToast();
  const [list, setList] = useState(null);
  const [f, setF] = useState({ person_email: "", person_name: "", start_date: "", end_date: "", kind: "announcement" });
  const a = useAction();
  const load = useCallback(() => api.ws.absences().then(setList).catch(() => setList([])), []);
  useEffect(() => { load(); }, [load]);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const add = (e) => { e.preventDefault(); a.run(async () => { await api.ws.addAbsence({ ...f, end_date: f.end_date || f.start_date }); setF((x) => ({ ...x, person_email: "", person_name: "" })); load(); onChanged?.(); }); };
  const scan = () => a.run(async () => { const r = await api.ws.scanLeave(7); toast.success(r.found.length ? `Found ${r.found.length} possible absence(s). Please check them.` : "No new leave messages found."); load(); });
  const proposed = (list || []).filter((x) => x.status === "proposed");
  const confirmed = (list || []).filter((x) => x.status === "confirmed");
  return (
    <div className="space-y-4">
      <Card title="Leave and absences" description="Spots leave in recent email and Teams messages. Detections stay “needs your check” until you confirm them. Reasons are never stored or shared."
        actions={<button type="button" onClick={scan} disabled={a.busy} className={secondary}>{a.busy ? <Loader2 size={15} className="animate-spin" /> : <Radar size={15} />} Scan messages</button>}>
        <Err error={a.error} />
        {!list ? <Skeleton /> : (
          <div className="space-y-4">
            {proposed.length > 0 && <div><h3 className="mb-2 text-sm font-semibold">Found in your messages</h3><ul className="space-y-2">{proposed.map((x) => <Absence key={x.id} a={x} onChanged={load} />)}</ul></div>}
            <div><h3 className="mb-2 text-sm font-semibold">Confirmed</h3>
              {confirmed.length === 0 ? <p className="text-sm text-muted">No confirmed absences.</p> : <ul className="space-y-2">{confirmed.map((x) => <Absence key={x.id} a={x} onChanged={load} />)}</ul>}</div>
          </div>
        )}
        <form onSubmit={add} className="mt-4 grid gap-2 rounded-xl border border-line/15 p-3 sm:grid-cols-2" aria-label="Add an absence">
          <input type="email" required value={f.person_email} onChange={set("person_email")} placeholder="Email (yours or a colleague's)" aria-label="Email" className={field} />
          <input value={f.person_name} onChange={set("person_name")} placeholder="Name (optional)" aria-label="Name" className={field} />
          <label className="text-xs text-muted">From<input type="date" required value={f.start_date} onChange={set("start_date")} className={`${field} mt-1`} /></label>
          <label className="text-xs text-muted">To<input type="date" value={f.end_date} onChange={set("end_date")} className={`${field} mt-1`} /></label>
          <select value={f.kind} onChange={set("kind")} aria-label="Kind" className={field}>
            <option value="announcement">Announced</option><option value="request">Requested</option><option value="approved">Officially approved</option>
          </select>
          <button type="submit" disabled={a.busy} className={secondary}><Plus size={15} /> Add absence</button>
        </form>
      </Card>
      <OutOfOffice status={status} />
    </div>
  );
}
