import { cloneElement, useCallback, useEffect, useId, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Pencil, Play, Plug, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";

const field = "min-h-11 w-full rounded-xl border border-line/15 bg-ink/5 px-3 text-base focus:border-accent focus:outline-none sm:text-sm";
const primary = "btn-gradient inline-flex min-h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold disabled:opacity-50";
const secondary = "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-xl border border-line/20 px-3 text-sm hover:bg-ink/10 disabled:opacity-50";
const METHOD_COLORS = { GET: "bg-emerald-500/15 text-emerald-400", POST: "bg-sky-500/15 text-sky-400", PUT: "bg-amber-500/15 text-amber-400",
  PATCH: "bg-amber-500/15 text-amber-400", DELETE: "bg-red-500/15 text-red-400" };
const AUTH_LABELS = { none: "No authentication", bearer: "Bearer token", header: "API key in a header", basic: "Basic (user:password)" };
const EMPTY_APP = { name: "", slug: "", description: "", base_url: "", auth_type: "none", auth_header: "", secret: "", audience: "all", send_user: true, enabled: true };
const EMPTY_ACTION = { name: "", label: "", description: "", method: "GET", path: "/", approval: false, params: [] };

function Err({ error }) {
  return error ? <p role="alert" className="flex items-start gap-1.5 text-sm text-red-400"><AlertTriangle size={15} className="mt-0.5 shrink-0" /> {error}</p> : null;
}

function Labeled({ label, children, hint }) {
  const id = useId();
  return (
    <div className="text-xs text-muted">
      <label className="block">
        <span className="mb-1 block font-medium text-ink/90">{label}</span>
        {hint ? cloneElement(children, { "aria-describedby": id }) : children}
      </label>
      {hint && <p id={id} className="mt-1">{hint}</p>}
    </div>
  );
}

function AppForm({ initial, onSaved, onCancel }) {
  const editing = !!initial?.id;
  const [f, setF] = useState(editing ? { ...EMPTY_APP, ...initial, secret: "", description: initial.description || "", auth_header: initial.auth_header || "" } : EMPTY_APP);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = { ...f };
    if (editing) { delete body.slug; if (!body.secret) delete body.secret; }
    if (body.auth_type !== "header") delete body.auth_header;
    if (body.auth_type === "none") delete body.secret;
    try { onSaved(editing ? await api.adminUpdateIntegration(initial.id, body) : await api.adminCreateIntegration(body)); }
    catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  const insecure = f.base_url.startsWith("http://") && f.auth_type !== "none";
  return (
    <form onSubmit={submit} className="space-y-3 rounded-2xl border border-line/15 p-4" aria-label={editing ? `Edit ${initial.name}` : "Add an app"}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Labeled label="Name"><input value={f.name} onChange={set("name")} maxLength={100} required placeholder="HR system" className={field} /></Labeled>
        <Labeled label="Short name" hint="Lowercase, used in tool names (e.g. hr, crm). Can't be changed later.">
          <input value={f.slug} onChange={set("slug")} maxLength={30} required={!editing} disabled={editing} pattern="[a-z][a-z0-9_]{1,29}" placeholder="hr" className={field} />
        </Labeled>
      </div>
      <Labeled label="What it's for" hint="The assistant reads this to decide when to use the app.">
        <input value={f.description} onChange={set("description")} maxLength={1000} placeholder="Employees, leave balances and HR tickets" className={field} />
      </Labeled>
      <Labeled label="Base URL"><input value={f.base_url} onChange={set("base_url")} maxLength={500} required type="url" placeholder="https://hr.internal.example.com/api" className={field} /></Labeled>
      <div className="grid gap-3 sm:grid-cols-2">
        <Labeled label="Authentication">
          <select value={f.auth_type} onChange={set("auth_type")} className={field}>
            {Object.entries(AUTH_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Labeled>
        {f.auth_type === "header" && <Labeled label="Header name"><input value={f.auth_header} onChange={set("auth_header")} maxLength={100} placeholder="X-API-Key" className={field} /></Labeled>}
      </div>
      {f.auth_type !== "none" && (
        <Labeled label={f.auth_type === "basic" ? "user:password" : "Token / API key"} hint={editing && initial.has_secret ? "A secret is saved. Leave empty to keep it." : "Stored encrypted. Never shown again or sent to the AI."}>
          <input type="password" value={f.secret} onChange={set("secret")} autoComplete="new-password" maxLength={4000} className={field} placeholder={editing && initial.has_secret ? "••••••••" : ""} />
        </Labeled>
      )}
      {insecure && <p className="flex items-start gap-1.5 text-xs text-amber-400"><AlertTriangle size={14} className="mt-0.5 shrink-0" /> This address uses http, so the key travels unencrypted. Use https if the app supports it.</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Labeled label="Who can use it">
          <select value={f.audience} onChange={set("audience")} className={field}>
            <option value="all">Everyone</option><option value="admin">Administrators only</option>
          </select>
        </Labeled>
        <div className="space-y-2 pt-5 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" checked={f.send_user} onChange={set("send_user")} /> Send who is asking (X-Nova-User-Email)</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={f.enabled} onChange={set("enabled")} /> Enabled</label>
        </div>
      </div>
      <Err error={error} />
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className={primary}>{busy && <Loader2 size={15} className="animate-spin" />} {editing ? "Save" : "Add app"}</button>
        <button type="button" onClick={onCancel} className={secondary}>Cancel</button>
      </div>
    </form>
  );
}

function ActionForm({ app, onSaved, onCancel }) {
  const [f, setF] = useState(EMPTY_ACTION);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const write = f.method !== "GET";
  const placeholders = [...f.path.matchAll(/\{([^}]*)\}/g)].map((m) => m[1]);
  const setParam = (i, k, v) => setF((x) => ({ ...x, params: x.params.map((p, j) => (j === i ? { ...p, [k]: v } : p)) }));
  const addParam = (name = "", where) => setF((x) => ({ ...x, params: [...x.params, { name, type: "string", in: where || (x.method === "GET" || x.method === "DELETE" ? "query" : "body"), required: !!name, description: "" }] }));
  // make sure each {placeholder} in the path has a path parameter
  useEffect(() => {
    setF((x) => {
      const have = new Set(x.params.filter((p) => p.in === "path").map((p) => p.name));
      const missing = placeholders.filter((n) => n && !have.has(n));
      return missing.length ? { ...x, params: [...x.params, ...missing.map((n) => ({ name: n, type: "string", in: "path", required: true, description: "" }))] } : x;
    });
  }, [f.path]); // eslint-disable-line react-hooks/exhaustive-deps
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try { onSaved(await api.adminAddAction(app.id, f)); } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl border border-accent/30 bg-accent/5 p-3" aria-label="Add an action">
      <div className="grid gap-3 sm:grid-cols-2">
        <Labeled label="Action name" hint="Lowercase, e.g. get_employee"><input value={f.name} onChange={set("name")} pattern="[a-z][a-z0-9_]{1,29}" maxLength={30} required className={field} /></Labeled>
        <Labeled label="Label people see"><input value={f.label} onChange={set("label")} maxLength={100} required placeholder="Look up an employee" className={field} /></Labeled>
      </div>
      <Labeled label="When to use it (for the assistant)"><input value={f.description} onChange={set("description")} maxLength={1000} required placeholder="Get an employee's details and leave balance by employee id" className={field} /></Labeled>
      <div className="grid gap-3 sm:grid-cols-[8rem_1fr]">
        <Labeled label="Method">
          <select value={f.method} onChange={set("method")} className={field}>{["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m}>{m}</option>)}</select>
        </Labeled>
        <Labeled label="Path" hint="Added to the base URL. Use {name} for values in the path."><input value={f.path} onChange={set("path")} maxLength={500} required placeholder="/employees/{employee_id}" className={`${field} font-mono`} /></Labeled>
      </div>
      <fieldset className="space-y-2">
        <legend className="text-xs font-medium">Parameters</legend>
        {f.params.map((p, i) => (
          <div key={i} className="grid grid-cols-2 gap-2 rounded-lg bg-ink/5 p-2 sm:grid-cols-[1fr_7rem_6rem_1.5fr_auto]">
            <input aria-label="Parameter name" value={p.name} onChange={(e) => setParam(i, "name", e.target.value)} placeholder="name" className={`${field} font-mono`} />
            <select aria-label="Type" value={p.type} onChange={(e) => setParam(i, "type", e.target.value)} className={field}>{["string", "number", "integer", "boolean"].map((t) => <option key={t}>{t}</option>)}</select>
            <select aria-label="Sent in" value={p.in} onChange={(e) => setParam(i, "in", e.target.value)} className={field}>
              <option value="path">path</option><option value="query">query</option>{f.method !== "GET" && f.method !== "DELETE" && <option value="body">body</option>}
            </select>
            <input aria-label="Description" value={p.description} onChange={(e) => setParam(i, "description", e.target.value)} placeholder="description" className={field} />
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={p.required || p.in === "path"} disabled={p.in === "path"} onChange={(e) => setParam(i, "required", e.target.checked)} /> required</label>
              <button type="button" aria-label={`Remove parameter ${p.name}`} onClick={() => setF((x) => ({ ...x, params: x.params.filter((_, j) => j !== i) }))} className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-red-500/10 hover:text-red-400"><X size={15} /></button>
            </div>
          </div>
        ))}
        <button type="button" onClick={() => addParam()} className={secondary}><Plus size={14} /> Parameter</button>
      </fieldset>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={write || f.approval} disabled={write} onChange={set("approval")} />
        Ask the user to approve each call {write && <span className="text-xs text-muted">(always on for {f.method})</span>}
      </label>
      <Err error={error} />
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className={primary}>{busy && <Loader2 size={15} className="animate-spin" />} Add action</button>
        <button type="button" onClick={onCancel} className={secondary}>Cancel</button>
      </div>
    </form>
  );
}

function TestBox({ app, action, onClose }) {
  const [text, setText] = useState(JSON.stringify(Object.fromEntries(action.params.map((p) => [p.name, ""])), null, 2));
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    let args;
    try { args = JSON.parse(text || "{}"); } catch { setRes({ ok: false, error: "That isn't valid JSON." }); return; }
    setBusy(true);
    try { setRes(await api.adminTestAction(app.id, action.id, args)); } catch (e) { setRes({ ok: false, error: e.message }); } finally { setBusy(false); }
  };
  return (
    <div className="mt-2 space-y-2 rounded-xl bg-ink/5 p-3">
      <p className="text-xs text-muted">Runs the real request now, as you{action.method !== "GET" ? ". This may change data in the app." : "."}</p>
      <label htmlFor={`args-${action.id}`} className="sr-only">Arguments (JSON)</label>
      <textarea id={`args-${action.id}`} value={text} onChange={(e) => setText(e.target.value)} rows={Math.min(8, action.params.length + 2)} className={`${field} py-2 font-mono text-xs`} />
      <div className="flex gap-2">
        <button type="button" onClick={run} disabled={busy} className={secondary}>{busy ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Run test</button>
        <button type="button" onClick={onClose} className={secondary}>Close</button>
      </div>
      {res && (res.ok ? (
        <div><p className="flex items-center gap-1.5 text-xs text-emerald-400"><CheckCircle2 size={13} /> {res.summary}</p>
          <pre className="thin-scroll mt-1 max-h-60 overflow-auto rounded-lg bg-black/40 p-2 text-[11px] text-[#e6e9ff]">{res.result}</pre></div>
      ) : <Err error={res.error} />)}
    </div>
  );
}

function AppCard({ app, onChange, onDeleted }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [testing, setTesting] = useState(null);
  const [error, setError] = useState(null);
  const act = async (fn, ok) => { setError(null); try { const r = await fn(); if (ok) toast.success(ok); return r; } catch (e) { setError(e.message); return undefined; } };
  if (editing) return <AppForm initial={app} onCancel={() => setEditing(false)} onSaved={(a) => { setEditing(false); onChange(a); toast.success("App saved."); }} />;
  return (
    <section className="glass min-w-0 rounded-2xl p-4" aria-label={app.name}>
      <div className="flex flex-wrap items-start gap-2">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/15 text-accent"><Plug size={18} /></span>
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 font-display font-semibold">{app.name}
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-normal ${app.enabled ? "bg-emerald-500/15 text-emerald-400" : "bg-ink/10 text-muted"}`}>{app.enabled ? "Enabled" : "Off"}</span>
            {app.audience === "admin" && <span className="rounded-full bg-volt/15 px-2 py-0.5 text-[11px] font-normal text-volt">Admins only</span>}</p>
          <p className="break-all font-mono text-xs text-muted">{app.base_url}</p>
          <p className="text-xs text-muted">{AUTH_LABELS[app.auth_type]}{app.has_secret ? " · secret saved" : ""}{app.description ? ` · ${app.description}` : ""}</p>
        </div>
        <div className="flex gap-1">
          <button type="button" onClick={() => setEditing(true)} aria-label={`Edit ${app.name}`} className="flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-ink/10 hover:text-ink"><Pencil size={16} /></button>
          <button type="button" aria-label={`Delete ${app.name}`} className="flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-red-500/10 hover:text-red-400"
            onClick={async () => { if (window.confirm(`Delete ${app.name} and all its actions?`) && await act(() => api.adminDeleteIntegration(app.id), "App deleted.")) onDeleted(app.id); }}><Trash2 size={16} /></button>
        </div>
      </div>
      <ul className="mt-3 space-y-2" aria-label={`${app.name} actions`}>
        {app.actions.length === 0 && <li className="text-sm text-muted">No actions yet. Add what the assistant may do with this app.</li>}
        {app.actions.map((a) => (
          <li key={a.id} className="rounded-xl border border-line/10 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-md px-1.5 py-0.5 font-mono text-[11px] font-semibold ${METHOD_COLORS[a.method]}`}>{a.method}</span>
              <span className="min-w-0 break-all font-mono text-xs">{a.path}</span>
              {a.approval && <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] text-amber-400"><ShieldCheck size={11} /> approval</span>}
              {!a.enabled && <span className="rounded-full bg-ink/10 px-2 py-0.5 text-[11px] text-muted">off</span>}
            </div>
            <p className="mt-1 font-medium">{a.label} <span className="font-mono text-[11px] font-normal text-muted">{a.tool}</span></p>
            <p className="text-xs text-muted">{a.description}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button type="button" onClick={() => setTesting(testing === a.id ? null : a.id)} className={secondary}><Play size={14} /> Test</button>
              <button type="button" onClick={async () => { const r = await act(() => api.adminUpdateAction(app.id, a.id, { enabled: !a.enabled })); if (r) onChange(r); }} className={secondary}>{a.enabled ? "Turn off" : "Turn on"}</button>
              <button type="button" onClick={async () => { const r = await act(() => api.adminDeleteAction(app.id, a.id), "Action deleted."); if (r) onChange(r); }} className={`${secondary} text-red-400`}><Trash2 size={14} /> Delete</button>
            </div>
            {testing === a.id && <TestBox app={app} action={a} onClose={() => setTesting(null)} />}
          </li>
        ))}
      </ul>
      <div className="mt-3">
        {adding ? <ActionForm app={app} onCancel={() => setAdding(false)} onSaved={(r) => { setAdding(false); onChange(r); toast.success("Action added."); }} />
          : <button type="button" onClick={() => setAdding(true)} className={secondary}><Plus size={15} /> Add action</button>}
      </div>
      <div className="mt-2"><Err error={error} /></div>
    </section>
  );
}

export default function AdminIntegrations() {
  const [apps, setApps] = useState(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState(null);
  const load = useCallback(() => api.adminIntegrations().then(setApps).catch((e) => setError(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const replace = (a) => setApps((list) => list.map((x) => (x.id === a.id ? a : x)));
  return (
    <div className="space-y-4">
      <section className="glass rounded-2xl p-4 sm:p-5">
        <h2 className="font-display text-base font-semibold">Internal apps</h2>
        <p className="mt-1 text-sm text-muted">Connect your company's own systems (HR, CRM, tickets, inventory…) through their REST API. Each action becomes something the assistant can do. Read actions run directly; anything that changes data asks the user to approve first. Every call is in the audit and tool logs.</p>
        {!adding && <button type="button" onClick={() => setAdding(true)} className={`${primary} mt-3`}><Plus size={16} /> Add an app</button>}
      </section>
      {adding && <AppForm onCancel={() => setAdding(false)} onSaved={(a) => { setAdding(false); setApps((l) => [...(l || []), a]); }} />}
      <Err error={error} />
      {!apps ? <div className="h-24 animate-pulse rounded-2xl bg-ink/10" aria-busy="true" /> : apps.length === 0 && !adding ? (
        <p className="text-center text-sm text-muted">No internal apps yet.</p>
      ) : apps.map((a) => <AppCard key={a.id} app={a} onChange={replace} onDeleted={(id) => setApps((l) => l.filter((x) => x.id !== id))} />)}
    </div>
  );
}
