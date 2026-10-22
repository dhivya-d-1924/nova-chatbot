import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Bell, Briefcase, CalendarDays, CheckSquare, ClipboardCheck, FlaskConical, History, Inbox as InboxIcon, Menu, Plane, Plug, Settings2 } from "lucide-react";
import IconButton from "./IconButton";
import ThemeToggle from "./ThemeToggle";
import { api } from "../lib/api";
import { formatWhen } from "../lib/hooks";
import Accounts from "./workspace/Accounts";
import Approvals from "./workspace/Approvals";
import Automation from "./workspace/Automation";
import Inbox from "./workspace/Inbox";
import Leave from "./workspace/Leave";
import Tasks from "./workspace/Tasks";
import Today from "./workspace/Today";
import { Badge, Card, Err, Skeleton } from "./workspace/ui";

const TABS = [
  ["today", "Today", CalendarDays], ["inbox", "Inbox", InboxIcon], ["approvals", "Approvals", ClipboardCheck], ["leave", "Leave", Plane], ["tasks", "Tasks", CheckSquare],
  ["automation", "Automation", Settings2], ["activity", "Activity", History], ["accounts", "Accounts", Plug],
];

function Activity() {
  const [rows, setRows] = useState(null);
  useEffect(() => { api.ws.activity().then(setRows).catch(() => setRows([])); }, []);
  return (
    <Card title="Activity history" description="Everything the assistant did for you, with the provider's confirmation. Message contents aren't stored here.">
      {!rows ? <Skeleton /> : rows.length === 0 ? <p className="text-sm text-muted">Nothing yet.</p> : (
        <ul className="space-y-2" aria-label="Activity">
          {rows.map((r) => (
            <li key={r.id} className="rounded-xl border border-line/10 p-3 text-sm">
              <p className="flex flex-wrap items-center gap-2"><Badge tone={r.status === "done" ? "ok" : r.status === "failed" ? "bad" : "muted"}>{r.status}</Badge>
                <span className="font-mono text-xs text-muted">{r.kind}</span><span className="text-xs text-muted">{formatWhen(r.created_at, { long: true })}</span></p>
              <p className="mt-1 break-words">{r.summary}</p>
              {r.ref && r.ref.startsWith("https://") && <a href={r.ref} target="_blank" rel="noopener noreferrer" className="text-xs text-accent">Open</a>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Connections({ status, onManage }) {
  const rows = [["Gmail & Google Calendar", status.google], ["Teams & Outlook", status.microsoft]].filter(([, s]) => s.configured);
  if (!rows.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" aria-label="Connections">
      {rows.map(([name, s]) => {
        const warn = s.connected && s.missing_scopes?.length;
        return (
          <button key={name} type="button" onClick={onManage} className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-line/15 px-2.5 hover:bg-ink/10">
            <span className={`h-2 w-2 rounded-full ${!s.connected ? "bg-ink/30" : warn ? "bg-amber-400" : "bg-emerald-400"}`} aria-hidden="true" />
            {name}: {!s.connected ? "not connected" : warn ? "needs permission" : "connected"}
          </button>
        );
      })}
    </div>
  );
}

function NotificationBell({ count, onChanged }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    api.ws.notifications().then(setData).catch(() => setData({ items: [], unread: 0 }));
    const onDown = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);
  const readAll = async () => { await api.ws.readNotifications(); setData((d) => ({ ...d, unread: 0, items: d.items.map((n) => ({ ...n, read: true })) })); onChanged(); };
  return (
    <div className="relative" ref={ref}>
      <IconButton label={count ? `Notifications (${count} unread)` : "Notifications"} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <Bell size={19} />
        {count > 0 && <span className="pointer-events-none absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-white">{count}</span>}
      </IconButton>
      {open && (
        <div role="dialog" aria-label="Notifications" className="glass absolute right-0 top-12 z-40 w-[min(22rem,calc(100vw-1.5rem))] rounded-2xl bg-panel p-3 shadow-2xl">
          <div className="mb-2 flex items-center justify-between"><p className="font-display text-sm font-semibold">Notifications</p>
            {data?.unread > 0 && <button type="button" onClick={readAll} className="text-xs font-semibold text-accent">Mark all read</button>}</div>
          {!data ? <Skeleton h="h-16" /> : data.items.length === 0 ? <p className="text-sm text-muted">No notifications yet.</p> : (
            <ul className="thin-scroll max-h-[60dvh] space-y-2 overflow-y-auto">
              {data.items.map((n) => (
                <li key={n.id} className={`rounded-xl p-2 text-sm ${n.read ? "" : "bg-accent/10"}`}>
                  <p className="font-medium">{n.title}</p>
                  {n.body && <p className="mt-0.5 whitespace-pre-wrap break-words text-xs text-muted">{n.body.slice(0, 600)}</p>}
                  <p className="mt-0.5 text-[11px] text-muted">{formatWhen(n.created_at)}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export default function WorkspaceView({ onBack, onOpenMenu, onOpenChat, initialTab = "today" }) {
  const [tab, setTab] = useState(initialTab);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => api.ws.status().then((s) => { setStatus(s); setError(null); return s; }).catch((e) => setError(e.message)), []);
  useEffect(() => {
    load().then((s) => {
      // first visit: use this device's time zone so times and working hours are right from the start
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (s && !s.prefs.timezone && tz) api.ws.savePrefs({ timezone: tz }).then(load).catch(() => {});
    });
  }, [load]);
  useEffect(() => setTab(initialTab), [initialTab]);
  return (
    <main className="flex h-full min-w-0 flex-1 flex-col pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]" aria-label="Workspace">
      <header className="flex shrink-0 items-center gap-1 px-1.5 pb-2 pt-[max(0.5rem,env(safe-area-inset-top))] sm:px-4">
        <IconButton label="Open menu" onClick={onOpenMenu} className="lg:hidden"><Menu size={21} /></IconButton>
        <h1 className="flex min-w-0 flex-1 items-center gap-2 truncate px-2 font-display text-lg font-semibold"><Briefcase size={18} className="shrink-0 text-accent" aria-hidden="true" /> <span className="max-[359px]:sr-only">Workspace</span></h1>
        <NotificationBell count={status?.notifications || 0} onChanged={load} />
        <ThemeToggle className="hidden min-[400px]:inline-flex" />
        <button type="button" onClick={onBack} className="ml-1 inline-flex min-h-11 shrink-0 items-center gap-2 rounded-xl border border-line/15 px-3 text-sm font-medium transition-colors hover:bg-ink/10">
          <ArrowLeft size={16} /> <span className="hidden min-[400px]:inline">Back to chat</span><span className="min-[400px]:hidden">Chat</span>
        </button>
      </header>
      <div role="tablist" aria-label="Workspace sections" className="thin-scroll mx-3 flex shrink-0 gap-1 overflow-x-auto pb-2 sm:mx-6">
        {TABS.map(([id, label, Icon]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} aria-controls="ws-panel" onClick={() => setTab(id)}
            className={`inline-flex min-h-10 shrink-0 items-center gap-1.5 rounded-xl px-3 text-sm transition-colors ${tab === id ? "bg-accent/20 font-semibold text-ink" : "text-muted hover:bg-ink/10 hover:text-ink"}`}>
            <Icon size={15} aria-hidden="true" /> {label}
            {id === "approvals" && status?.pending_approvals > 0 && <span className="rounded-full bg-accent px-1.5 text-[10px] font-bold text-white">{status.pending_approvals}</span>}
            {id === "approvals" && status?.failed_approvals > 0 && <span className="rounded-full bg-red-500 px-1.5 text-[10px] font-bold text-white" title="Failed">{status.failed_approvals}!</span>}
          </button>
        ))}
      </div>
      <div id="ws-panel" role="tabpanel" className="thin-scroll min-h-0 flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-3 pb-[max(1.5rem,env(safe-area-inset-bottom))] sm:px-6">
        <Err error={error} />
        {status && <Connections status={status} onManage={() => setTab("accounts")} />}
        {status?.demo && (
          <p role="note" className="flex items-start gap-2 rounded-xl bg-amber-500/10 p-3 text-sm text-amber-300">
            <FlaskConical size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
            <span><strong>Demo mode:</strong> Gmail, Teams and calendars are simulated with sample data. Nothing reaches real people or accounts.</span></p>
        )}
        {!status ? <Skeleton h="h-40" /> : (
          <>
            {!status.google.connected && !status.microsoft.connected && tab !== "accounts" && (
              <p className="rounded-xl bg-accent/10 p-3 text-sm">Connect Google Workspace or Microsoft 365 to see your calendar, email and Teams here.{" "}
                <button type="button" onClick={() => setTab("accounts")} className="font-semibold text-accent underline-offset-4 hover:underline">Connect an account</button></p>
            )}
            {tab === "today" && <Today onChanged={load} />}
            {tab === "inbox" && <Inbox onChanged={load} onOpenTasks={() => setTab("tasks")} />}
            {tab === "approvals" && <Approvals onChanged={load} onOpenChat={onOpenChat} />}
            {tab === "leave" && <Leave status={status} onChanged={load} />}
            {tab === "tasks" && <Tasks />}
            {tab === "automation" && <Automation status={status} onSaved={load} />}
            {tab === "activity" && <Activity />}
            {tab === "accounts" && <Accounts status={status} onChanged={load} />}
          </>
        )}
      </div>
    </main>
  );
}
