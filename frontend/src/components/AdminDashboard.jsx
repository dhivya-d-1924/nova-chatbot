import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  Activity, AlertTriangle, ArrowLeft, ChevronLeft, ChevronRight, ClipboardList, Coins, Gauge, Headset, LayoutGrid, Menu,
  MessageSquare, MessagesSquare, RefreshCw, Search, ThumbsDown, ThumbsUp, Users,
} from "lucide-react";
import EvalPanel from "./EvalPanel";
import IconButton from "./IconButton";
import ThemeToggle from "./ThemeToggle";
import { api } from "../lib/api";
import { formatWhen, useDebounced } from "../lib/hooks";
import { fadeUp, stagger } from "../lib/motion";
import { useToast } from "../lib/toast";

const fmt = (us) => (us ? formatWhen(us, { long: true }) : "Never");
const num = (n) => (n ?? 0).toLocaleString();
const money = (n) => `$${(n ?? 0).toFixed((n ?? 0) < 1 ? 4 : 2)}`;
const field = "min-h-11 rounded-xl border border-line/15 bg-ink/5 px-3 text-base focus:border-accent focus:outline-none sm:text-sm";
const TABS = [
  ["overview", "Overview", LayoutGrid], ["users", "Users", Users], ["support", "Support", Headset], ["usage", "Usage", Gauge],
  ["audit", "Audit log", ClipboardList], ["errors", "Errors", AlertTriangle], ["evals", "Evaluation", Activity],
];

function Stat({ icon: Icon, label, value, hint }) {
  return (
    <motion.div variants={fadeUp} className="glass min-w-0 rounded-2xl p-4">
      <div className="flex items-center gap-2 text-sm text-muted"><Icon size={16} aria-hidden="true" /> <span className="truncate">{label}</span></div>
      <p className="mt-2 truncate font-display text-2xl font-semibold sm:text-3xl">{value}</p>
      {hint && <p className="truncate text-xs text-muted">{hint}</p>}
    </motion.div>
  );
}

function Card({ title, children, actions }) {
  return (
    <section className="glass min-w-0 rounded-2xl p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display text-base font-semibold">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

function ErrorBox({ error }) {
  return error ? (
    <p role="alert" className="flex items-start gap-2 rounded-xl border border-red-400/40 bg-red-500/10 p-3 text-sm">
      <AlertTriangle size={16} className="mt-0.5 shrink-0 text-red-400" /> {error}
    </p>
  ) : null;
}

function Pager({ data, onPage }) {
  if (!data || data.pages <= 1) return null;
  return (
    <nav aria-label="Pages" className="mt-3 flex items-center justify-between gap-2 text-sm">
      <span className="text-muted">Page {data.page} of {data.pages} · {num(data.total)} total</span>
      <span className="flex gap-1">
        <IconButton label="Previous page" disabled={data.page <= 1} onClick={() => onPage(data.page - 1)}><ChevronLeft size={18} /></IconButton>
        <IconButton label="Next page" disabled={data.page >= data.pages} onClick={() => onPage(data.page + 1)}><ChevronRight size={18} /></IconButton>
      </span>
    </nav>
  );
}

/** Load a paginated admin list whenever its filters change. */
function usePaged(fetcher, params) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [page, setPage] = useState(1);
  const key = JSON.stringify(params);
  useEffect(() => { setPage(1); }, [key]);
  const load = useCallback(async () => {
    try { setData(await fetcher({ ...params, page })); setError(null); } catch (e) { setError(e.message); }
  }, [key, page]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  return { data, error, setPage, reload: load };
}

// ------------------------------------------------------------------ overview
function Overview() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => api.adminStats().then((s) => { setStats(s); setError(null); }).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);
  if (error) return <ErrorBox error={error} />;
  if (!stats) {
    return <div className="grid animate-pulse grid-cols-2 gap-3 lg:grid-cols-4" aria-busy="true">{[0, 1, 2, 3].map((i) => <div key={i} className="h-24 rounded-2xl bg-ink/10" />)}</div>;
  }
  const max = Math.max(1, ...stats.per_day.map((d) => d.count));
  const totalFb = stats.feedback.up + stats.feedback.down;
  const cfg = stats.config;
  return (
    <div className="space-y-4">
      <motion.div variants={stagger(0.06, 0)} initial="hidden" animate="show" className="grid grid-cols-1 gap-3 min-[360px]:grid-cols-2 lg:grid-cols-4">
        <Stat icon={Users} label="Users" value={num(stats.totals.users)} />
        <Stat icon={MessageSquare} label="Conversations" value={num(stats.totals.conversations)} />
        <Stat icon={MessagesSquare} label="Questions today" value={num(stats.totals.questions_today)} />
        <Stat icon={Headset} label="Open support requests" value={num(stats.totals.open_handoffs)} hint={`${num(stats.totals.errors)} errors logged`} />
      </motion.div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="min-w-0 lg:col-span-2">
          <Card title="Questions per day (last 14 days)">
            <div className="flex h-40 items-end gap-1" role="img" aria-label={`Bar chart of questions per day. Highest day: ${max}.`}>
              {stats.per_day.map((d) => (
                <div key={d.date} className="flex h-full min-w-0 flex-1 flex-col justify-end" title={`${d.date}: ${d.count}`}>
                  <motion.div initial={{ height: 0 }} animate={{ height: `${(d.count / max) * 100}%` }} transition={{ duration: 0.5 }}
                    className="min-h-[3px] rounded-t-md bg-gradient-to-t from-accent to-volt" />
                </div>
              ))}
            </div>
            <div className="mt-2 flex justify-between text-xs text-muted"><span>{stats.per_day[0].date.slice(5)}</span><span>{stats.per_day.at(-1).date.slice(5)}</span></div>
          </Card>
        </div>
        <Card title="Answer feedback">
          <div className="flex items-center gap-6">
            <p className="flex items-center gap-2 text-2xl font-semibold"><ThumbsUp size={20} className="text-emerald-400" aria-label="Positive" /> {stats.feedback.up}</p>
            <p className="flex items-center gap-2 text-2xl font-semibold"><ThumbsDown size={20} className="text-red-400" aria-label="Negative" /> {stats.feedback.down}</p>
          </div>
          <div className="mt-4 h-2 overflow-hidden rounded-full bg-ink/10" aria-hidden="true">
            <div className="h-full bg-emerald-400" style={{ width: totalFb ? `${(stats.feedback.up / totalFb) * 100}%` : 0 }} />
          </div>
          <p className="mt-2 text-sm text-muted">{totalFb ? `${Math.round((stats.feedback.up / totalFb) * 100)}% positive` : "No ratings yet"}</p>
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Configuration">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            {[["Environment", cfg.env], ["Model", cfg.model], ["AI provider", cfg.llm_configured ? "configured" : "NOT configured"],
              ["Semantic search", cfg.embeddings ? "on" : "off"], ["Email (SMTP)", cfg.smtp ? "configured" : "not configured"],
              ["Shared limiter (Redis)", cfg.redis ? "on" : "off"], ["Email verification", cfg.email_verification ? "required" : "optional"],
              ["Registration", cfg.registration_open ? "open" : "closed"], ["Database", cfg.database]].map(([k, v]) => (
              <div key={k} className="min-w-0"><dt className="text-xs text-muted">{k}</dt><dd className="truncate">{v}</dd></div>
            ))}
          </dl>
        </Card>
        <Card title="Recent 👎 answers">
          {stats.recent_downvotes.length === 0 ? <p className="text-sm text-muted">No downvoted answers.</p> : (
            <ul className="space-y-3">
              {stats.recent_downvotes.map((d, i) => (
                <li key={i} className="text-sm"><p className="break-words text-muted">“{d.snippet}…”</p><p className="text-xs text-muted">{d.user} · {fmt(d.created_at)}</p></li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <Card title="Recent tool activity">
        {stats.recent_tools.length === 0 ? <p className="text-sm text-muted">No tools used yet.</p> : (
          <ul className="space-y-3">
            {stats.recent_tools.map((t, i) => (
              <li key={i} className="text-sm">
                <p><span className="font-medium">{t.tool}</span>{" "}
                  <span className={t.status === "done" ? "text-emerald-400" : t.status === "error" ? "text-red-400" : "text-muted"}>· {t.status}</span>
                  {t.duration_ms != null && <span className="text-muted"> · {(t.duration_ms / 1000).toFixed(1)} s</span>}</p>
                <p className="break-all text-xs text-muted">{t.args}</p>
                <p className="text-xs text-muted">{t.user} · {fmt(t.created_at)}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ users
function UsersTab({ me }) {
  const toast = useToast();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const query = useDebounced(q, 300);
  const { data, error, setPage, reload } = usePaged(api.adminUsers, { q: query, role, status, limit: 20 });
  const [saving, setSaving] = useState(null);
  const act = async (id, fn, ok) => {
    setSaving(id);
    try { await fn(); toast.success(ok); await reload(); } catch (e) { toast.error(e.message); } finally { setSaving(null); }
  };
  return (
    <Card title="Users">
      <div className="mb-3 grid gap-2 sm:grid-cols-[1fr_auto_auto]">
        <div className="relative">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden="true" />
          <label htmlFor="user-q" className="sr-only">Search users</label>
          <input id="user-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name or email" className={`${field} w-full pl-9`} />
        </div>
        <label className="sr-only" htmlFor="user-role">Role</label>
        <select id="user-role" value={role} onChange={(e) => setRole(e.target.value)} className={`${field} bg-panel`}>
          <option value="">All roles</option><option value="user">Users</option><option value="admin">Admins</option>
        </select>
        <label className="sr-only" htmlFor="user-status">Status</label>
        <select id="user-status" value={status} onChange={(e) => setStatus(e.target.value)} className={`${field} bg-panel`}>
          <option value="">Any status</option><option value="active">Active</option><option value="disabled">Disabled</option><option value="locked">Locked out</option>
        </select>
      </div>
      <ErrorBox error={error} />
      {!data ? <div className="h-24 animate-pulse rounded-xl bg-ink/10" aria-busy="true" /> : data.items.length === 0 ? (
        <p className="text-sm text-muted">No users match.</p>
      ) : (
        <ul className="divide-y divide-line/10">
          {data.items.map((u) => {
            const self = u.id === me.id;
            const busy = saving === u.id;
            return (
              <li key={u.id} className="flex flex-col gap-3 py-3 md:flex-row md:items-center">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{u.name}{self && " (you)"}</p>
                  <p className="truncate text-xs text-muted">{u.email}</p>
                  <p className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted">
                    <span>{u.conversations} chats</span><span>{u.messages} questions</span><span>{u.sessions} sessions</span>
                    <span>Last sign-in {fmt(u.last_login)}</span>
                    {!u.email_verified && <span className="text-amber-400">unverified</span>}
                    {u.mfa_enabled && <span className="text-emerald-400">2-step on</span>}
                    {u.locked && <span className="text-red-400">locked out</span>}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <label className="sr-only" htmlFor={`role-${u.id}`}>Role for {u.name}</label>
                  <select id={`role-${u.id}`} value={u.role} disabled={self || busy}
                    onChange={(e) => act(u.id, () => api.adminUpdateUser(u.id, { role: e.target.value }), `Role changed to ${e.target.value}.`)}
                    className="min-h-10 rounded-lg border border-line/15 bg-panel px-2 text-sm disabled:opacity-50">
                    <option value="user">User</option><option value="admin">Admin</option>
                  </select>
                  <button type="button" disabled={self || busy}
                    onClick={() => act(u.id, () => api.adminUpdateUser(u.id, { disabled: !u.disabled }), u.disabled ? "Account enabled." : "Account disabled and signed out.")}
                    className={`min-h-10 rounded-lg px-3 text-xs font-semibold transition-colors disabled:opacity-40 ${u.disabled ? "bg-red-500/20 text-red-400 hover:bg-red-500/30" : "bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25"}`}>
                    {u.disabled ? "Disabled · Enable" : "Active · Disable"}
                  </button>
                  {u.locked && (
                    <button type="button" disabled={busy} onClick={() => act(u.id, () => api.adminUpdateUser(u.id, { unlock: true }), "Account unlocked.")}
                      className="min-h-10 rounded-lg border border-line/20 px-3 text-xs font-medium hover:bg-ink/10">Unlock</button>
                  )}
                  {u.sessions > 0 && !self && (
                    <button type="button" disabled={busy} onClick={() => act(u.id, () => api.adminRevokeSessions(u.id), "Signed out on all devices.")}
                      className="min-h-10 rounded-lg border border-line/20 px-3 text-xs font-medium hover:bg-ink/10">Sign out</button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <Pager data={data} onPage={setPage} />
    </Card>
  );
}

// ------------------------------------------------------------------ support
function Handoff({ h, onResolved }) {
  const toast = useToast();
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api.adminResolveHandoff(h.id, reply);
      setReply("");
      toast.success(r.delivered_to_chat ? "Reply sent to the user's chat." : "Resolved. The chat was deleted, so the user won't see the reply in it.");
      await onResolved();
    } catch (err) { toast.error(err.message); } finally { setBusy(false); }
  };
  return (
    <li className="rounded-xl border border-line/10 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${h.status === "open" ? "bg-amber-400/15 text-amber-400" : "bg-emerald-500/15 text-emerald-400"}`}>{h.status}</span>
        <span className="break-all font-medium">{h.user}</span>
        <span className="text-xs text-muted">{fmt(h.created_at)}</span>
      </div>
      <p className="mt-2 break-words">{h.summary}</p>
      {h.context.length > 0 && (
        <details className="mt-2 text-xs text-muted">
          <summary className="min-h-9 cursor-pointer select-none py-2">Recent conversation</summary>
          <ul className="mt-1 space-y-1">{h.context.map((m, i) => <li key={i} className="break-words"><b className="capitalize">{m.role}:</b> {m.content}</li>)}</ul>
        </details>
      )}
      {h.status === "open" ? (
        <form onSubmit={send} className="mt-3 flex flex-col gap-2 sm:flex-row">
          <label htmlFor={`reply-${h.id}`} className="sr-only">Reply to {h.user}</label>
          <textarea id={`reply-${h.id}`} value={reply} onChange={(e) => setReply(e.target.value)} rows={2} required maxLength={4000}
            placeholder={h.has_conversation ? "Write a reply. It appears in the user's chat." : "Write a resolution note."}
            className="flex-1 resize-none rounded-xl border border-line/15 bg-ink/5 p-2.5 text-base focus:border-accent focus:outline-none sm:text-sm" />
          <button type="submit" disabled={busy || !reply.trim()} className="btn-gradient min-h-11 shrink-0 rounded-xl px-4 text-sm font-semibold disabled:opacity-50">Reply & resolve</button>
        </form>
      ) : <p className="mt-2 rounded-lg bg-ink/5 p-2 text-xs text-muted"><b>Reply:</b> {h.reply}</p>}
    </li>
  );
}

function SupportTab() {
  const [status, setStatus] = useState("open");
  const { data, error, setPage, reload } = usePaged(api.adminHandoffs, { status, limit: 10 });
  return (
    <Card title="Support requests" actions={
      <div role="tablist" aria-label="Filter requests" className="flex gap-1 rounded-xl bg-ink/5 p-1 text-xs">
        {[["open", "Open"], ["resolved", "Resolved"], ["", "All"]].map(([v, l]) => (
          <button key={l} type="button" role="tab" aria-selected={status === v} onClick={() => setStatus(v)}
            className={`min-h-9 rounded-lg px-3 ${status === v ? "bg-panel font-semibold shadow" : "text-muted"}`}>{l}</button>
        ))}
      </div>}>
      <ErrorBox error={error} />
      {!data ? <div className="h-24 animate-pulse rounded-xl bg-ink/10" aria-busy="true" /> : data.items.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-muted"><Headset size={16} /> No requests here. Users can ask for a person from the assistant settings menu, and the assistant can escalate on its own.</p>
      ) : <ul className="space-y-3">{data.items.map((h) => <Handoff key={h.id} h={h} onResolved={reload} />)}</ul>}
      <Pager data={data} onPage={setPage} />
    </Card>
  );
}

// ------------------------------------------------------------------ usage
function UsageTab() {
  const [days, setDays] = useState(14);
  const [u, setU] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api.adminUsage(days).then((d) => { setU(d); setError(null); }).catch((e) => setError(e.message)); }, [days]);
  if (error) return <ErrorBox error={error} />;
  if (!u) return <div className="h-40 animate-pulse rounded-2xl bg-ink/10" aria-busy="true" />;
  const t = u.totals;
  const max = Math.max(1, ...u.per_day.map((d) => d.tokens));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted">{u.priced ? "Costs are estimates from LLM_PRICE_* settings." : "Set LLM_PRICE_INPUT_PER_1M and LLM_PRICE_OUTPUT_PER_1M to see cost estimates."}
          {t.estimated_share > 0 && ` ${Math.round(t.estimated_share * 100)}% of token counts are estimated (the provider didn't report them).`}</p>
        <label className="flex items-center gap-2 text-sm">Period
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className={`${field} bg-panel`}>
            {[7, 14, 30, 90].map((d) => <option key={d} value={d}>{d} days</option>)}
          </select>
        </label>
      </div>
      <motion.div variants={stagger(0.05, 0)} initial="hidden" animate="show" className="grid grid-cols-1 gap-3 min-[360px]:grid-cols-2 lg:grid-cols-4">
        <Stat icon={Activity} label="AI requests" value={num(t.requests)} hint={`${num(t.failed)} failed (${t.requests ? Math.round((t.failed / t.requests) * 100) : 0}%)`} />
        <Stat icon={MessagesSquare} label="Tokens" value={num(t.prompt_tokens + t.completion_tokens)} hint={`${num(t.prompt_tokens)} in · ${num(t.completion_tokens)} out`} />
        <Stat icon={Coins} label="Estimated cost" value={money(t.cost_usd)} hint={`This month ${money(t.month_cost_usd)}${t.monthly_budget_usd ? ` of ${money(t.monthly_budget_usd)}` : ""}`} />
        <Stat icon={Gauge} label="Latency (p50 / p95)" value={`${(t.latency_p50_ms / 1000).toFixed(1)} s`} hint={`p95 ${(t.latency_p95_ms / 1000).toFixed(1)} s`} />
      </motion.div>
      <Card title="Tokens per day">
        <div className="flex h-36 items-end gap-0.5" role="img" aria-label="Bar chart of tokens per day">
          {u.per_day.map((d) => (
            <div key={d.date} className="flex h-full min-w-0 flex-1 flex-col justify-end" title={`${d.date}: ${num(d.tokens)} tokens, ${d.requests} requests, ${d.failed} failed`}>
              <div className="min-h-[2px] rounded-t bg-gradient-to-t from-accent to-volt" style={{ height: `${(d.tokens / max) * 100}%` }} />
            </div>
          ))}
        </div>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="By model">
          <ul className="space-y-2 text-sm">
            {u.by_model.length === 0 ? <li className="text-muted">No usage yet.</li> : u.by_model.map((m) => (
              <li key={m.model} className="flex flex-wrap justify-between gap-2"><span className="break-all font-medium">{m.model}</span>
                <span className="text-muted">{num(m.requests)} req · {num(m.tokens)} tok · {money(m.cost_usd)}</span></li>
            ))}
          </ul>
          {Object.keys(u.by_kind).length > 0 && (
            <p className="mt-3 text-xs text-muted">By type: {Object.entries(u.by_kind).map(([k, v]) => `${k} ${v}`).join(" · ")}</p>
          )}
        </Card>
        <Card title="Top users">
          <ul className="space-y-2 text-sm">
            {u.top_users.length === 0 ? <li className="text-muted">No usage yet.</li> : u.top_users.map((x) => (
              <li key={x.user} className="flex flex-wrap justify-between gap-2"><span className="break-all">{x.user}</span><span className="text-muted">{num(x.tokens)} tok · {money(x.cost_usd)}</span></li>
            ))}
          </ul>
        </Card>
      </div>
      <Card title="Tools">
        {u.tools.length === 0 ? <p className="text-sm text-muted">No tool calls in this period.</p> : (
          <div className="thin-scroll overflow-x-auto">
            <table className="w-full min-w-[480px] text-left text-sm">
              <thead className="text-muted"><tr>{["Tool", "Calls", "Success", "Declined", "Avg time"].map((h) => <th key={h} scope="col" className="pb-2 pr-3 font-medium">{h}</th>)}</tr></thead>
              <tbody>
                {u.tools.map((x) => (
                  <tr key={x.tool} className="border-t border-line/10">
                    <td className="py-2 pr-3 font-medium">{x.tool}</td><td className="pr-3">{x.calls}</td>
                    <td className="pr-3">{x.success_rate != null ? `${Math.round(x.success_rate * 100)}%` : "—"}</td>
                    <td className="pr-3">{x.declined}</td><td>{x.avg_ms != null ? `${(x.avg_ms / 1000).toFixed(1)} s` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ audit & errors
function AuditTab() {
  const [action, setAction] = useState("");
  const [q, setQ] = useState("");
  const query = useDebounced(q, 300);
  const { data, error, setPage } = usePaged(api.adminAudit, { action, q: query, limit: 30 });
  return (
    <Card title="Audit log">
      <div className="mb-3 grid gap-2 sm:grid-cols-2">
        <label className="sr-only" htmlFor="audit-action">Event type</label>
        <select id="audit-action" value={action} onChange={(e) => setAction(e.target.value)} className={`${field} bg-panel`}>
          <option value="">All events</option>
          {(data?.actions || []).map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <label className="sr-only" htmlFor="audit-q">Filter by user email or IP</label>
        <input id="audit-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="User email or IP" className={field} />
      </div>
      <ErrorBox error={error} />
      {!data ? <div className="h-24 animate-pulse rounded-xl bg-ink/10" aria-busy="true" /> : data.items.length === 0 ? <p className="text-sm text-muted">No events.</p> : (
        <ul className="divide-y divide-line/10 text-sm">
          {data.items.map((e) => (
            <li key={e.id} className="py-2">
              <p className="flex flex-wrap gap-x-2"><span className="font-mono text-xs font-semibold text-volt">{e.action}</span><span className="text-xs text-muted">{fmt(e.created_at)}</span></p>
              <p className="break-all text-xs text-muted">
                by {e.actor}{e.target && e.target !== e.actor ? ` → ${e.target_type || ""} ${e.target}` : ""}{e.ip ? ` · ${e.ip}` : ""}
                {e.detail ? ` · ${Object.entries(e.detail).map(([k, v]) => `${k}: ${v}`).join(", ")}` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
      <Pager data={data} onPage={setPage} />
    </Card>
  );
}

function ErrorsTab() {
  const [q, setQ] = useState("");
  const query = useDebounced(q, 300);
  const { data, error, setPage } = usePaged(api.adminErrors, { q: query, limit: 25 });
  return (
    <Card title="Errors">
      <label className="sr-only" htmlFor="err-q">Search errors</label>
      <input id="err-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search error messages" className={`${field} mb-3 w-full`} />
      <ErrorBox error={error} />
      {!data ? <div className="h-24 animate-pulse rounded-xl bg-ink/10" aria-busy="true" /> : data.items.length === 0 ? <p className="text-sm text-muted">No errors logged.</p> : (
        <ul className="space-y-3">
          {data.items.map((e, i) => (
            <li key={i} className="text-sm"><p className="break-words">{e.message}</p><p className="text-xs text-muted">{e.user} · {fmt(e.created_at)}</p></li>
          ))}
        </ul>
      )}
      <Pager data={data} onPage={setPage} />
    </Card>
  );
}

export default function AdminDashboard({ me, onBack, onOpenMenu }) {
  const [tab, setTab] = useState("overview");
  const [nonce, setNonce] = useState(0);
  return (
    <main className="flex h-full min-w-0 flex-1 flex-col pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]" aria-label="Admin dashboard">
      <header className="flex shrink-0 items-center gap-1 px-1.5 pb-2 pt-[max(0.5rem,env(safe-area-inset-top))] sm:px-4">
        <IconButton label="Open menu" onClick={onOpenMenu} className="lg:hidden"><Menu size={21} /></IconButton>
        <h1 className="min-w-0 flex-1 truncate px-2 font-display text-lg font-semibold">Admin</h1>
        <IconButton label="Refresh" onClick={() => setNonce((n) => n + 1)}><RefreshCw size={18} /></IconButton>
        <ThemeToggle className="hidden min-[400px]:inline-flex" />
        <button type="button" onClick={onBack} className="ml-1 inline-flex min-h-11 shrink-0 items-center gap-2 rounded-xl border border-line/15 px-3 text-sm font-medium transition-colors hover:bg-ink/10">
          <ArrowLeft size={16} /> <span className="hidden min-[400px]:inline">Back to chat</span><span className="min-[400px]:hidden">Chat</span>
        </button>
      </header>
      <div role="tablist" aria-label="Admin sections" className="thin-scroll mx-3 flex shrink-0 gap-1 overflow-x-auto pb-2 sm:mx-6">
        {TABS.map(([id, label, Icon]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} aria-controls="admin-panel" onClick={() => setTab(id)}
            className={`inline-flex min-h-10 shrink-0 items-center gap-1.5 rounded-xl px-3 text-sm transition-colors ${tab === id ? "bg-accent/20 font-semibold text-ink" : "text-muted hover:bg-ink/10 hover:text-ink"}`}>
            <Icon size={15} aria-hidden="true" /> {label}
          </button>
        ))}
      </div>
      <div id="admin-panel" role="tabpanel" key={`${tab}-${nonce}`}
        className="thin-scroll min-h-0 flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-3 pb-[max(1.5rem,env(safe-area-inset-bottom))] sm:px-6">
        {tab === "overview" && <Overview />}
        {tab === "users" && <UsersTab me={me} />}
        {tab === "support" && <SupportTab />}
        {tab === "usage" && <UsageTab />}
        {tab === "audit" && <AuditTab />}
        {tab === "errors" && <ErrorsTab />}
        {tab === "evals" && <EvalPanel />}
      </div>
    </main>
  );
}
