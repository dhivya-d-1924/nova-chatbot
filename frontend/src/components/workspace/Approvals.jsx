import { useCallback, useEffect, useState } from "react";
import { Check, ExternalLink, Loader2, MessageSquare, Pencil, RotateCw, X } from "lucide-react";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";
import { Badge, Card, Err, Skeleton, field, primary, secondary, useAction, when } from "./ui";

const ORIGIN = { leave: "Leave assistant", job: "Scheduled job", assistant: "Assistant", user: "You", convert: "Drafted from a message" };
const TEXT_FIELD = { email_send: "body", email_reply: "body", email_draft: "body", teams_chat: "text", teams_channel: "text",
  calendar_create: "description", email_forward: "note", ooo_enable: "internal" };

function Draft({ d, onDone }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const key = TEXT_FIELD[d.kind];
  const [text, setText] = useState(d.payload[key] || "");
  const a = useAction();
  const save = () => a.run(async () => { await api.ws.editApproval(d.id, { [key]: text }); setEditing(false); onDone(); });
  const approve = () => a.run(async () => {
    const r = await api.ws.approve(d.id);
    if (r.status === "sent") toast.success(r.result || "Done.");
    else toast.error(r.error || "It didn't go through.");
    onDone();
  });
  const discard = () => a.run(async () => { await api.ws.discard(d.id); onDone(); });
  const retry = () => a.run(async () => {
    const r = await api.ws.retry(d.id);
    if (r.status === "sent") toast.success(r.result || "Done.");
    else toast.error(r.error || "It still didn't go through.");
    onDone();
  });
  return (
    <li className="rounded-2xl border border-line/15 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="accent">{d.action}</Badge>
        <Badge>{ORIGIN[d.origin] || d.origin}</Badge>
        <span className="text-xs text-muted">{when(d.created_at)}</span>
      </div>
      <p className="mt-2 break-words font-medium">{d.title}</p>
      {editing ? (
        <div className="mt-2 space-y-2">
          <label htmlFor={`edit-${d.id}`} className="sr-only">Edit text</label>
          <textarea id={`edit-${d.id}`} value={text} onChange={(e) => setText(e.target.value)} rows={8} className={`${field} py-2`} />
          <div className="flex gap-2"><button type="button" onClick={save} disabled={a.busy} className={secondary}>Save changes</button>
            <button type="button" onClick={() => setEditing(false)} className={secondary}>Cancel</button></div>
        </div>
      ) : (
        <pre className="thin-scroll mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-ink/5 p-3 font-sans text-sm" aria-label="What will happen">{d.preview}</pre>
      )}
      <Err error={a.error} />
      {d.status === "pending" ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" onClick={approve} disabled={a.busy} className={primary}>{a.busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} Approve and send</button>
          {key && !editing && <button type="button" onClick={() => setEditing(true)} className={secondary}><Pencil size={14} /> Edit</button>}
          <button type="button" onClick={discard} disabled={a.busy} className={secondary}><X size={14} /> Discard</button>
        </div>
      ) : d.status === "failed" ? (
        <div className="mt-2 space-y-2">
          <p className="text-sm text-red-400">Not sent: {d.error}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={retry} disabled={a.busy} className={primary}>{a.busy ? <Loader2 size={15} className="animate-spin" /> : <RotateCw size={15} />} Retry</button>
            {key && !editing && <button type="button" onClick={() => setEditing(true)} className={secondary}><Pencil size={14} /> Edit</button>}
            <button type="button" onClick={discard} disabled={a.busy} className={secondary}><X size={14} /> Cancel it</button>
          </div>
          <p className="text-xs text-muted">Retrying never sends twice: if it already went out, Nova refuses the repeat.</p>
        </div>
      ) : (
        <p className={`mt-2 text-sm ${d.status === "sent" ? "text-emerald-400" : "text-muted"}`}>{d.status === "sent" ? d.result : "Discarded"}</p>
      )}
    </li>
  );
}

export default function Approvals({ onChanged, onOpenChat }) {
  const [status, setStatus] = useState("pending");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => api.ws.approvals(status).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message)), [status]);
  useEffect(() => { load(); }, [load]);
  const refresh = () => { load(); onChanged?.(); };
  return (
    <div className="space-y-4">
      <Card title="Waiting for your approval" description="Every email, Teams message, meeting change or out-of-office is shown here exactly as it will be sent. Nothing goes out until you approve."
        actions={(
          <div role="tablist" aria-label="Show" className="flex gap-1 rounded-xl bg-ink/5 p-1 text-sm">
            {[["pending", "Pending"], ["failed", "Failed"], ["all", "History"]].map(([v, l]) => (
              <button key={v} type="button" role="tab" aria-selected={status === v} onClick={() => setStatus(v)}
                className={`min-h-9 rounded-lg px-3 ${status === v ? "bg-panel font-semibold shadow" : "text-muted"}`}>{l}</button>
            ))}
          </div>
        )}>
        <Err error={error} />
        {!data ? <Skeleton /> : (
          <div className="space-y-3">
            {data.in_chats.length > 0 && (
              <ul className="space-y-2" aria-label="Approvals in chats">
                {data.in_chats.map((c) => (
                  <li key={c.id} className="flex flex-wrap items-center gap-2 rounded-xl bg-amber-500/10 p-3 text-sm">
                    <MessageSquare size={15} className="shrink-0 text-amber-400" aria-hidden="true" />
                    <span className="min-w-0 flex-1 break-words">Waiting in the chat “{c.title}”</span>
                    <button type="button" onClick={() => onOpenChat?.(c.conversation_id)} className={secondary}>Open chat <ExternalLink size={12} /></button>
                  </li>
                ))}
              </ul>
            )}
            {data.drafts.length === 0 && data.in_chats.length === 0 ? <p className="text-sm text-muted">{status === "failed" ? "Nothing failed." : "Nothing waiting. ✨"}</p> : (
              <ul className="space-y-3" aria-label="Prepared actions">
                {data.drafts.map((d) => <Draft key={d.id} d={d} onDone={refresh} />)}
              </ul>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
