import { useCallback, useEffect, useState } from "react";
import { ExternalLink, ListChecks, Loader2, MailCheck, MailOpen, RefreshCw, Sparkles, Wand2, X } from "lucide-react";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";
import { Badge, Card, Err, Skeleton, field, primary, secondary, useAction } from "./ui";

const TARGETS = {
  gmail: [["email_reply", "Reply by email"], ["teams_chat", "Teams message to a colleague"], ["teams_channel", "Teams channel update"],
    ["email", "Email to someone else"]],
  teams: [["teams_chat", "Reply in this chat"], ["email", "Email about this discussion"], ["teams_channel", "Teams channel update"]],
};
const CATEGORY_TONE = { urgent: "bad", needs_reply: "warn", fyi: "info", low: "muted" };

export function SourceBadge({ source }) {
  return <Badge tone={source === "gmail" ? "bad" : "info"}>{source === "gmail" ? "Gmail" : "Teams"}</Badge>;
}

function shortTime(at) {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? at : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/** Turn an email or Teams chat into a draft for the other platform (or a reply). The draft waits in Approvals. */
function ConvertForm({ source, refId, onClose, onDone }) {
  const toast = useToast();
  const [target, setTarget] = useState(TARGETS[source][0][0]);
  const [to, setTo] = useState("");
  const [channel, setChannel] = useState("");
  const [channels, setChannels] = useState(null);
  const [instructions, setInstructions] = useState("");
  const a = useAction();
  useEffect(() => {
    if (target === "teams_channel" && !channels) api.ws.channels().then(setChannels).catch(() => setChannels([]));
  }, [target, channels]);
  const needsTo = target === "email" || (target === "teams_chat" && source === "gmail");
  const submit = (e) => {
    e.preventDefault();
    const dest = {};
    if (target === "email") dest.to = to;
    if (target === "teams_chat" && source === "gmail") dest.to_email = to;
    if (target === "teams_channel") [dest.team_id, dest.channel_id] = channel.split("|");
    a.run(async () => {
      await api.ws.convert({ source, ref: refId, target, dest, instructions: instructions.trim() });
      toast.success("Draft ready in Approvals. Nothing has been sent.");
      onDone?.();
      onClose();
    });
  };
  const id = `cv-${refId.replace(/[^A-Za-z0-9]/g, "")}`;
  return (
    <form onSubmit={submit} className="mt-2 space-y-2 rounded-xl border border-accent/30 bg-accent/5 p-3" aria-label="Prepare a draft">
      <div className="grid gap-2 sm:grid-cols-2">
        <div><label htmlFor={`${id}-t`} className="text-xs text-muted">Draft</label>
          <select id={`${id}-t`} value={target} onChange={(e) => setTarget(e.target.value)} className={`${field} mt-1`}>
            {TARGETS[source].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></div>
        {needsTo && (
          <div><label htmlFor={`${id}-to`} className="text-xs text-muted">{target === "email" ? "To (emails)" : "Colleague's email"}</label>
            <input id={`${id}-to`} value={to} onChange={(e) => setTo(e.target.value)} required type={target === "email" ? "text" : "email"}
              placeholder="name@company.com" className={`${field} mt-1`} /></div>
        )}
        {target === "teams_channel" && (
          <div><label htmlFor={`${id}-ch`} className="text-xs text-muted">Channel</label>
            <select id={`${id}-ch`} value={channel} onChange={(e) => setChannel(e.target.value)} required className={`${field} mt-1`}>
              <option value="">{channels ? (channels.length ? "Pick a channel" : "No channels available") : "Loading…"}</option>
              {(channels || []).flatMap((t) => t.channels.map((c) => <option key={c.id} value={`${t.team_id}|${c.id}`}>{t.team} › {c.name}</option>))}
            </select></div>
        )}
      </div>
      <div><label htmlFor={`${id}-i`} className="block text-xs text-muted">What should it say? (optional)</label>
        <input id={`${id}-i`} value={instructions} onChange={(e) => setInstructions(e.target.value)} maxLength={500}
          placeholder="e.g. ask for an ETA, keep it short" className={`${field} mt-1`} /></div>
      <Err error={a.error} />
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={a.busy} className={primary}>{a.busy ? <Loader2 size={15} className="animate-spin" /> : <Wand2 size={15} />} Write draft</button>
        <button type="button" onClick={onClose} className={secondary}><X size={14} /> Close</button>
      </div>
    </form>
  );
}

function Item({ item, onChanged, extra }) {
  const [open, setOpen] = useState(false);
  const [read, setRead] = useState(item.unread === false);
  const a = useAction();
  const markRead = () => a.run(async () => { await api.ws.markRead(item.id, !read); setRead(!read); });
  return (
    <li className="rounded-xl border border-line/10 p-3 text-sm">
      <p className="flex flex-wrap items-center gap-2"><SourceBadge source={item.source} />{extra}
        {item.question && <Badge tone="warn">Question</Badge>}{item.important && <Badge tone="bad">Important</Badge>}
        <span className="min-w-0 break-words font-medium">{item.title || item.subject}</span></p>
      <p className="mt-1 break-words text-xs text-muted">{item.from} · {shortTime(item.at || item.date)}</p>
      <p className="mt-1 break-words text-xs">{item.snippet}</p>
      {item.reason && <p className="mt-1 text-xs italic text-muted">{item.reason}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className={secondary}><Wand2 size={14} /> Draft a response</button>
        {item.source === "gmail" && (
          <button type="button" onClick={markRead} disabled={a.busy} className={secondary}>
            {read ? <MailOpen size={14} /> : <MailCheck size={14} />} {read ? "Mark unread" : "Mark read"}</button>
        )}
        {item.link && <a href={item.link} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-10 items-center gap-1 px-1 text-xs font-semibold text-accent">
          Open in {item.source === "gmail" ? "Gmail" : "Teams"} <ExternalLink size={11} /></a>}
      </div>
      <Err error={a.error} />
      {open && <ConvertForm source={item.source} refId={item.id} onClose={() => setOpen(false)} onDone={onChanged} />}
    </li>
  );
}

function NeedsResponse({ onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => { setData(null); api.ws.needsResponse().then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message)); }, []);
  useEffect(() => { load(); }, [load]);
  return (
    <Card title="Needs a response" description="Emails and Teams chats where someone else wrote last and may be waiting for you."
      actions={<button type="button" onClick={load} className={secondary} aria-label="Refresh"><RefreshCw size={14} /></button>}>
      <Err error={error} />
      {!data && !error ? <Skeleton /> : data && (
        <>
          {data.errors.map((e) => <p key={e} className="mb-2 text-xs text-amber-400">{e}</p>)}
          {data.items.length === 0 ? <p className="text-sm text-muted">Nobody is waiting for you. 🎉</p> : (
            <ul className="space-y-2" aria-label="Waiting for a response">
              {data.items.map((i) => <Item key={`${i.source}-${i.id}`} item={i} onChanged={onChanged} />)}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}

function Triage({ onChanged }) {
  const [data, setData] = useState(null);
  const a = useAction();
  const run = () => a.run(async () => setData(await api.ws.triage()));
  const groups = data ? ["urgent", "needs_reply", "fyi", "low"].map((c) => [c, data.items.filter((i) => i.category === c)]).filter(([, l]) => l.length) : [];
  return (
    <Card title="Sort my email" description="Recent Gmail sorted into urgent, needs a reply, FYI and low priority. Senders, subjects and links come straight from Gmail."
      actions={<button type="button" onClick={run} disabled={a.busy} className={secondary}>{a.busy ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} {data ? "Sort again" : "Sort now"}</button>}>
      <Err error={a.error} />
      {a.busy && !data && <Skeleton />}
      {data && (data.items.length === 0 ? <p className="text-sm text-muted">No recent email.</p> : (
        <div className="space-y-3">
          <p className="text-xs text-muted">Sorted by {data.method === "ai" ? "the AI" : "simple rules (the AI was unavailable)"}.</p>
          {groups.map(([cat, list]) => (
            <section key={cat} aria-label={list[0].category_label}>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{list[0].category_label} ({list.length})</h3>
              <ul className="space-y-2">{list.map((i) => <Item key={i.id} item={{ ...i, title: i.subject }} onChanged={onChanged}
                extra={<Badge tone={CATEGORY_TONE[cat]}>{i.category_label}</Badge>} />)}</ul>
            </section>
          ))}
        </div>
      ))}
    </Card>
  );
}

function ActionItems({ onOpenTasks }) {
  const [days, setDays] = useState(3);
  const [res, setRes] = useState(null);
  const a = useAction();
  const run = () => a.run(async () => setRes(await api.ws.extractItems(Number(days))));
  return (
    <Card title="Find action items" description="Reads recent emails and Teams chats, finds tasks with owners and deadlines, and adds them to Tasks with a link to where they came from.">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="ai-days" className="sr-only">Look back</label>
        <select id="ai-days" value={days} onChange={(e) => setDays(e.target.value)} className={`${field} !w-auto`}>
          {[1, 3, 7, 14].map((d) => <option key={d} value={d}>Last {d} day{d > 1 ? "s" : ""}</option>)}
        </select>
        <button type="button" onClick={run} disabled={a.busy} className={secondary}>{a.busy ? <Loader2 size={14} className="animate-spin" /> : <ListChecks size={14} />} Find action items</button>
      </div>
      <div className="mt-2"><Err error={a.error} /></div>
      {res && (
        <div className="mt-3 space-y-2 text-sm">
          {res.errors.map((e) => <p key={e} className="text-xs text-amber-400">{e}</p>)}
          {res.added.length === 0 ? <p className="text-muted">No new action items{res.skipped ? ` (${res.skipped} already in Tasks)` : ""}.</p> : (
            <>
              <ul className="space-y-2" aria-label="New action items">
                {res.added.map((i) => (
                  <li key={i.id} className="rounded-xl border border-line/10 p-3">
                    <p className="break-words font-medium">{i.title}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted"><SourceBadge source={i.source} />
                      {i.owner && <span>Owner: {i.owner}</span>}{i.due && <span>Due {shortTime(i.due)}</span>}
                      {i.link?.startsWith("https://") && <a href={i.link} target="_blank" rel="noopener noreferrer" className="font-semibold text-accent">Source <ExternalLink size={10} className="inline" /></a>}</p>
                  </li>
                ))}
              </ul>
              <button type="button" onClick={onOpenTasks} className={secondary}>Open Tasks</button>
            </>
          )}
        </div>
      )}
    </Card>
  );
}

export default function Inbox({ onChanged, onOpenTasks }) {
  return (
    <div className="space-y-4">
      <NeedsResponse onChanged={onChanged} />
      <ActionItems onOpenTasks={onOpenTasks} />
      <Triage onChanged={onChanged} />
    </div>
  );
}
