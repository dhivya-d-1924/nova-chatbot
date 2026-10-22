import { CheckCircle2, ExternalLink, Link2, ShieldAlert } from "lucide-react";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";
import { Badge, Card, Err, Skeleton, danger, primary, useAction, when } from "./ui";

function Provider({ name, info, onConnect, onDisconnect, busy, children }) {
  return (
    <Card title={name}>
      {!info.configured ? <p className="text-sm text-muted">Not set up on this server yet. Ask your administrator (see WORKSPACE.md).</p> : info.connected ? (
        <div className="space-y-2 text-sm">
          <p className="flex flex-wrap items-center gap-2"><CheckCircle2 size={16} className="text-emerald-400" aria-hidden="true" /> Connected as <strong className="break-all">{info.account}</strong>
            <span className="text-xs text-muted">since {when(info.since)}</span></p>
          {info.missing_scopes?.length > 0 && (
            <p className="flex items-start gap-1.5 rounded-xl bg-amber-500/10 p-2 text-xs text-amber-300"><ShieldAlert size={14} className="mt-0.5 shrink-0" />
              Some features need extra permission ({info.missing_scopes.join(", ")}). Reconnect to grant it.</p>
          )}
          {children}
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={onConnect} disabled={busy} className="inline-flex min-h-10 items-center gap-1.5 rounded-xl border border-line/20 px-3 text-sm hover:bg-ink/10"><Link2 size={14} /> Reconnect</button>
            <button type="button" onClick={onDisconnect} disabled={busy} className={danger}>Disconnect</button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {children}
          <button type="button" onClick={onConnect} disabled={busy} className={primary}><Link2 size={15} /> Connect {name}</button>
        </div>
      )}
    </Card>
  );
}

export default function Accounts({ status, onChanged }) {
  const toast = useToast();
  const a = useAction();
  if (!status) return <Skeleton />;
  const ms = status.microsoft;
  return (
    <div className="space-y-4">
      <Provider name="Google Workspace" info={status.google} busy={a.busy}
        onConnect={() => a.run(async () => { window.location.assign((await api.googleConnect()).url); })}
        onDisconnect={() => a.run(async () => { await api.googleDisconnect(); toast.success("Google disconnected and access revoked."); onChanged(); })}>
        <p className="text-xs text-muted">Calendar (read, create, change), colleagues' free/busy, Gmail (read, label, draft, send after approval, out-of-office) and Drive (read-only).</p>
      </Provider>
      <Provider name="Microsoft 365" info={ms} busy={a.busy}
        onConnect={() => a.run(async () => { window.location.assign((await api.ws.msConnect()).url); })}
        onDisconnect={() => a.run(async () => { await api.ws.msDisconnect(); toast.success("Microsoft 365 disconnected."); onChanged(); })}>
        <p className="text-xs text-muted">Outlook Calendar (with Teams meeting links), colleagues' availability, Teams chats (read, search, send after approval), channel posts and Outlook automatic replies.</p>
        {ms.configured && (
          <div className="rounded-xl bg-ink/5 p-2 text-xs text-muted">
            <p className="font-semibold text-ink/80">Needs your Microsoft 365 administrator's approval:</p>
            <ul className="mt-1 space-y-0.5">
              {Object.entries(ms.admin_scopes).map(([scope, what]) => (
                <li key={scope}>{ms.extra_scopes.includes(scope) ? <Badge tone="ok">on</Badge> : <Badge>off</Badge>} {what} <span className="font-mono">({scope})</span></li>
              ))}
            </ul>
            <a href="https://myapps.microsoft.com" target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-accent">Manage app access <ExternalLink size={11} /></a>
          </div>
        )}
      </Provider>
      <Err error={a.error} />
    </div>
  );
}
