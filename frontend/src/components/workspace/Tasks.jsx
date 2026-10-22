import { useCallback, useEffect, useState } from "react";
import { Bell, CheckCircle2, Circle, Plus, Trash2, X } from "lucide-react";
import { api } from "../../lib/api";
import { Badge, Card, Err, Skeleton, danger, field, localInput, secondary, useAction, when } from "./ui";

export default function Tasks() {
  const [status, setStatus] = useState("open");
  const [items, setItems] = useState(null);
  const [reminders, setReminders] = useState(null);
  const [f, setF] = useState({ title: "", owner: "", due: "" });
  const [r, setR] = useState({ text: "", at: localInput(new Date(Date.now() + 3600000)) });
  const a = useAction();
  const load = useCallback(() => {
    api.ws.items(status).then(setItems).catch(() => setItems([]));
    api.ws.reminders().then(setReminders).catch(() => setReminders([]));
  }, [status]);
  useEffect(() => { load(); }, [load]);
  const add = (e) => { e.preventDefault(); a.run(async () => { await api.ws.addItem({ title: f.title.trim(), owner: f.owner.trim(), due: f.due || undefined }); setF({ title: "", owner: "", due: "" }); load(); }); };
  const remind = (e) => { e.preventDefault(); a.run(async () => { await api.ws.addReminder(r.text.trim(), r.at); setR((x) => ({ ...x, text: "" })); load(); }); };
  return (
    <div className="space-y-4">
      <Card title="Action items" description="Tasks from your chats, emails and Teams, or added by you. Ask the assistant: “Find all pending tasks and their owners”."
        actions={(
          <div role="tablist" aria-label="Show" className="flex gap-1 rounded-xl bg-ink/5 p-1 text-sm">
            {[["open", "Open"], ["done", "Done"]].map(([v, l]) => (
              <button key={v} type="button" role="tab" aria-selected={status === v} onClick={() => setStatus(v)} className={`min-h-9 rounded-lg px-3 ${status === v ? "bg-panel font-semibold shadow" : "text-muted"}`}>{l}</button>
            ))}
          </div>
        )}>
        <form onSubmit={add} className="mb-3 grid gap-2 sm:grid-cols-[1fr_10rem_12rem_auto]" aria-label="Add an action item">
          <input value={f.title} onChange={(e) => setF((x) => ({ ...x, title: e.target.value }))} required maxLength={500} placeholder="What needs doing" aria-label="Action item" className={field} />
          <input value={f.owner} onChange={(e) => setF((x) => ({ ...x, owner: e.target.value }))} maxLength={255} placeholder="Owner" aria-label="Owner" className={field} />
          <input type="datetime-local" value={f.due} onChange={(e) => setF((x) => ({ ...x, due: e.target.value }))} aria-label="Due" className={field} />
          <button type="submit" disabled={a.busy} className={secondary}><Plus size={15} /> Add</button>
        </form>
        <Err error={a.error} />
        {!items ? <Skeleton /> : items.length === 0 ? <p className="text-sm text-muted">{status === "open" ? "Nothing open." : "Nothing done yet."}</p> : (
          <ul className="space-y-2" aria-label="Action items">
            {items.map((i) => {
              const overdue = i.status === "open" && i.due_at && i.due_at / 1000 < Date.now();
              return (
                <li key={i.id} className="flex items-start gap-2 rounded-xl border border-line/10 p-2 text-sm">
                  <button type="button" aria-label={i.status === "open" ? `Mark “${i.title}” done` : `Reopen “${i.title}”`} onClick={() => a.run(async () => { await api.ws.updateItem(i.id, { status: i.status === "open" ? "done" : "open" }); load(); })}
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg hover:bg-ink/10">{i.status === "done" ? <CheckCircle2 size={18} className="text-emerald-400" /> : <Circle size={18} className="text-muted" />}</button>
                  <div className="min-w-0 flex-1 py-1">
                    <p className={`break-words ${i.status === "done" ? "text-muted line-through" : ""}`}>{i.title}</p>
                    <p className="flex flex-wrap gap-2 text-xs text-muted">{i.owner && <span>{i.owner}</span>}{i.due_at && <span className={overdue ? "text-red-400" : ""}>due {when(i.due_at)}</span>}<Badge>{i.source}</Badge></p>
                  </div>
                  <button type="button" onClick={() => a.run(async () => { await api.ws.deleteItem(i.id); load(); })} aria-label={`Delete “${i.title}”`} className={danger}><Trash2 size={14} /></button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
      <Card title="Reminders and follow-ups" description="Ask: “Remind me to follow up if I receive no reply by Friday.” You're notified in the app and on the channels you chose.">
        <form onSubmit={remind} className="mb-3 grid gap-2 sm:grid-cols-[1fr_12rem_auto]" aria-label="Add a reminder">
          <input value={r.text} onChange={(e) => setR((x) => ({ ...x, text: e.target.value }))} required maxLength={1000} placeholder="Remind me to…" aria-label="Reminder" className={field} />
          <input type="datetime-local" required value={r.at} onChange={(e) => setR((x) => ({ ...x, at: e.target.value }))} aria-label="When" className={field} />
          <button type="submit" disabled={a.busy} className={secondary}><Bell size={15} /> Remind me</button>
        </form>
        {!reminders ? <Skeleton h="h-12" /> : reminders.length === 0 ? <p className="text-sm text-muted">No upcoming reminders.</p> : (
          <ul className="space-y-2" aria-label="Reminders">
            {reminders.map((x) => (
              <li key={x.id} className="flex items-center gap-2 rounded-xl border border-line/10 p-2 pl-3 text-sm">
                <Badge tone={x.kind === "followup" ? "info" : "accent"}>{x.kind === "followup" ? "Follow-up" : "Reminder"}</Badge>
                <span className="min-w-0 flex-1 break-words">{x.text}</span><span className="shrink-0 text-xs text-muted">{when(x.remind_at)}</span>
                <button type="button" onClick={() => a.run(async () => { await api.ws.cancelReminder(x.id); load(); })} aria-label={`Cancel reminder ${x.text}`} className={danger}><X size={14} /></button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
