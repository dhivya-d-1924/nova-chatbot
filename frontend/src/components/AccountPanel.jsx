import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Copy, Download, KeyRound, Laptop, Loader2, LogOut, ShieldCheck, Trash2, UserCog, X } from "lucide-react";
import { api } from "../lib/api";
import { formatWhen, useCopy, useDialog } from "../lib/hooks";
import { overlay, spring } from "../lib/motion";
import { useSettings } from "../lib/settings";
import { useToast } from "../lib/toast";

const TABS = [["profile", "Profile"], ["security", "Security"], ["devices", "Devices"], ["data", "Your data"]];
const input = "min-h-11 w-full rounded-xl border border-line/15 bg-ink/5 px-3 text-base placeholder:text-muted/70 focus:border-accent focus:outline-none sm:text-sm";
const primary = "btn-gradient inline-flex min-h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold disabled:opacity-50";
const secondary = "inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-line/20 px-4 text-sm font-medium hover:bg-ink/10 disabled:opacity-50";

function Section({ title, children, description }) {
  return (
    <section className="rounded-2xl border border-line/15 p-4">
      <h3 className="font-display text-sm font-semibold">{title}</h3>
      {description && <p className="mt-1 text-xs text-muted">{description}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const run = useCallback(async (fn) => {
    setBusy(true);
    setError(null);
    try { return await fn(); } catch (e) { setError(e.message); return undefined; } finally { setBusy(false); }
  }, []);
  return { busy, error, setError, run };
}

const ErrorText = ({ error }) => error ? <p role="alert" className="mt-2 flex items-start gap-1.5 text-sm text-red-400"><AlertTriangle size={15} className="mt-0.5 shrink-0" /> {error}</p> : null;

function Profile({ user, auth }) {
  const toast = useToast();
  const { settings, update } = useSettings();
  const [name, setName] = useState(user.name);
  const a = useAction();
  const save = (e) => {
    e.preventDefault();
    a.run(async () => { const r = await api.updateProfile(name.trim()); auth.updateUser(r.user); toast.success("Name updated."); });
  };
  const verify = () => a.run(async () => toast.success((await api.sendMyVerification()).message));
  return (
    <div className="space-y-3">
      <Section title="Your name">
        <form onSubmit={save} className="flex flex-col gap-2 sm:flex-row">
          <label htmlFor="acct-name" className="sr-only">Name</label>
          <input id="acct-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} className={input} autoComplete="name" />
          <button type="submit" disabled={a.busy || !name.trim() || name.trim() === user.name} className={primary}>Save</button>
        </form>
      </Section>
      <Section title="Email">
        <p className="break-all text-sm">{user.email}</p>
        {user.email_verified ? (
          <p className="mt-1 inline-flex items-center gap-1 text-xs text-emerald-400"><CheckCircle2 size={13} /> Verified</p>
        ) : (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="text-xs text-amber-400">Not verified yet.</span>
            <button type="button" onClick={verify} disabled={a.busy} className="min-h-9 text-xs font-semibold text-volt hover:underline">Send verification link</button>
          </div>
        )}
      </Section>
      <Section title="Appearance" description="Saved to your account, so it applies on all your devices.">
        <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-1 rounded-xl bg-ink/5 p-1 text-sm">
          {[["dark", "Dark"], ["light", "Light"], ["system", "System"]].map(([v, l]) => (
            <button key={v} type="button" role="radio" aria-checked={settings.theme === v} onClick={() => update({ theme: v })}
              className={`min-h-10 rounded-lg ${settings.theme === v ? "bg-panel font-semibold shadow" : "text-muted hover:text-ink"}`}>{l}</button>
          ))}
        </div>
      </Section>
      <ErrorText error={a.error} />
    </div>
  );
}

function ChangePassword() {
  const toast = useToast();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const a = useAction();
  const submit = (e) => {
    e.preventDefault();
    a.run(async () => { toast.success((await api.changePassword(cur, next)).message); setCur(""); setNext(""); });
  };
  return (
    <Section title="Change password" description="Other devices are signed out when you change it.">
      <form onSubmit={submit} className="space-y-2">
        <label htmlFor="pw-cur" className="sr-only">Current password</label>
        <input id="pw-cur" type="password" autoComplete="current-password" placeholder="Current password" value={cur} onChange={(e) => setCur(e.target.value)} className={input} />
        <label htmlFor="pw-new" className="sr-only">New password</label>
        <input id="pw-new" type="password" autoComplete="new-password" placeholder="New password (8+ characters)" value={next} onChange={(e) => setNext(e.target.value)} minLength={8} maxLength={128} className={input} />
        <button type="submit" disabled={a.busy || !cur || next.length < 8} className={primary}>{a.busy && <Loader2 size={15} className="animate-spin" />} Change password</button>
      </form>
      <ErrorText error={a.error} />
    </Section>
  );
}

function RecoveryCodes({ codes, onDone }) {
  const [copied, copy] = useCopy();
  const text = codes.join("\n");
  const download = () => {
    const url = URL.createObjectURL(new Blob([`Nova recovery codes (each works once)\n\n${text}\n`], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url; a.download = "nova-recovery-codes.txt"; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <div className="rounded-xl border border-amber-400/40 bg-amber-400/10 p-3">
      <p className="text-sm font-semibold">Save your recovery codes</p>
      <p className="mt-1 text-xs text-muted">Each code signs you in once if you lose your phone. They won't be shown again.</p>
      <ul className="mt-2 grid grid-cols-2 gap-1 font-mono text-sm">{codes.map((c) => <li key={c}>{c}</li>)}</ul>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" onClick={() => copy(text)} className={secondary}><Copy size={15} /> {copied ? "Copied" : "Copy"}</button>
        <button type="button" onClick={download} className={secondary}><Download size={15} /> Download</button>
        <button type="button" onClick={onDone} className={primary}>I've saved them</button>
      </div>
    </div>
  );
}

function TwoStep({ user, auth }) {
  const toast = useToast();
  const [step, setStep] = useState("idle"); // idle | password | scan | codes | disable
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState(null);
  const [codes, setCodes] = useState([]);
  const [copied, copy] = useCopy();
  const a = useAction();
  const reset = () => { setStep("idle"); setPassword(""); setCode(""); setSetup(null); a.setError(null); };

  const start = (e) => { e.preventDefault(); a.run(async () => { setSetup(await api.mfaSetup(password)); setPassword(""); setStep("scan"); }); };
  const enable = (e) => {
    e.preventDefault();
    a.run(async () => { const r = await api.mfaEnable(code); setCodes(r.recovery_codes); auth.updateUser(r.user); setStep("codes"); setCode(""); });
  };
  const disable = (e) => {
    e.preventDefault();
    a.run(async () => { const r = await api.mfaDisable(password, code); auth.updateUser(r.user); toast.success("Two-step verification is off."); reset(); });
  };

  return (
    <Section title="Two-step verification"
      description="Ask for a code from an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password…) when you sign in.">
      {step === "codes" ? <RecoveryCodes codes={codes} onDone={() => { reset(); toast.success("Two-step verification is on."); }} />
        : user.mfa_enabled && step !== "disable" ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="inline-flex items-center gap-1.5 text-sm text-emerald-400"><ShieldCheck size={16} /> On</p>
            <button type="button" onClick={() => setStep("disable")} className={secondary}>Turn off</button>
          </div>
        ) : step === "disable" ? (
          <form onSubmit={disable} className="space-y-2">
            <input type="password" autoComplete="current-password" placeholder="Your password" aria-label="Your password" value={password} onChange={(e) => setPassword(e.target.value)} className={input} />
            <input inputMode="numeric" autoComplete="one-time-code" placeholder="Code from your app or a recovery code" aria-label="Code" value={code} onChange={(e) => setCode(e.target.value)} className={input} />
            <div className="flex gap-2">
              <button type="submit" disabled={a.busy || !password || !code} className={primary}>Turn off</button>
              <button type="button" onClick={reset} className={secondary}>Cancel</button>
            </div>
          </form>
        ) : step === "password" ? (
          <form onSubmit={start} className="space-y-2">
            <input type="password" autoComplete="current-password" placeholder="Confirm your password" aria-label="Confirm your password" value={password} onChange={(e) => setPassword(e.target.value)} className={input} data-autofocus />
            <div className="flex gap-2">
              <button type="submit" disabled={a.busy || !password} className={primary}>Continue</button>
              <button type="button" onClick={reset} className={secondary}>Cancel</button>
            </div>
          </form>
        ) : step === "scan" && setup ? (
          <form onSubmit={enable} className="space-y-3">
            <ol className="list-decimal space-y-1 pl-5 text-sm">
              <li>In your authenticator app, add an account using this setup key{" "}
                <a href={setup.uri} className="text-volt underline">(or tap here on your phone)</a>:</li>
            </ol>
            <div className="flex items-center gap-2 rounded-xl bg-ink/5 p-2">
              <code className="min-w-0 flex-1 break-all font-mono text-sm">{setup.secret.match(/.{1,4}/g).join(" ")}</code>
              <button type="button" onClick={() => copy(setup.secret)} aria-label="Copy setup key" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg hover:bg-ink/10">
                {copied ? <CheckCircle2 size={16} className="text-emerald-400" /> : <Copy size={16} />}
              </button>
            </div>
            <label htmlFor="mfa-code" className="block text-sm">2. Enter the 6-digit code it shows:</label>
            <input id="mfa-code" inputMode="numeric" autoComplete="one-time-code" maxLength={7} value={code} onChange={(e) => setCode(e.target.value)} className={input} placeholder="123456" />
            <div className="flex gap-2">
              <button type="submit" disabled={a.busy || code.trim().length < 6} className={primary}>Turn on</button>
              <button type="button" onClick={reset} className={secondary}>Cancel</button>
            </div>
          </form>
        ) : (
          <button type="button" onClick={() => setStep("password")} className={primary}><KeyRound size={15} /> Set up</button>
        )}
      <ErrorText error={a.error} />
    </Section>
  );
}

function Devices({ auth }) {
  const toast = useToast();
  const [list, setList] = useState(null);
  const a = useAction();
  const load = useCallback(() => a.run(async () => setList(await api.sessions())), [a.run]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  const revoke = (s) => a.run(async () => {
    await api.revokeSession(s.id);
    if (s.current) { await auth.logout(); return; }
    toast.success("Signed out that device.");
    await load();
  });
  const all = () => a.run(async () => { await api.logoutAll(); await auth.logout(); });
  return (
    <div className="space-y-3">
      <Section title="Where you're signed in" description="Sign out of devices you don't recognise.">
        {!list ? <div className="h-16 animate-pulse rounded-xl bg-ink/10" aria-busy="true" /> : (
          <ul className="space-y-2">
            {list.map((s) => (
              <li key={s.id} className="flex items-start gap-3 rounded-xl border border-line/10 p-3">
                <Laptop size={18} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
                <div className="min-w-0 flex-1 text-sm">
                  <p className="break-words font-medium">{describeAgent(s.user_agent)}{s.current && <span className="ml-1.5 rounded-full bg-accent/20 px-2 py-0.5 text-[11px] text-accent">This device</span>}</p>
                  <p className="text-xs text-muted">Last active {formatWhen(s.last_seen)} · {s.ip || "unknown IP"}</p>
                </div>
                <button type="button" onClick={() => revoke(s)} disabled={a.busy} className="min-h-9 shrink-0 rounded-lg px-2 text-xs font-semibold text-red-400 hover:bg-red-500/10">Sign out</button>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <button type="button" onClick={all} disabled={a.busy} className={`${secondary} w-full`}><LogOut size={15} /> Sign out everywhere</button>
      <ErrorText error={a.error} />
    </div>
  );
}

function describeAgent(ua = "") {
  const os = /iPhone|iPad/.test(ua) ? (/iPad/.test(ua) ? "iPad" : "iPhone") : /Android/.test(ua) ? "Android" : /Mac OS X/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "Unknown device";
  const br = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  return `${br} on ${os}`;
}

function YourData({ auth }) {
  const [pw, setPw] = useState("");
  const [typed, setTyped] = useState("");
  const a = useAction();
  const remove = (e) => { e.preventDefault(); a.run(async () => { await api.deleteAccount(pw); await auth.logout(); }); };
  return (
    <div className="space-y-3">
      <Section title="Download your data" description="Your profile, conversations, document list, memories and reports as a JSON file.">
        <button type="button" onClick={() => a.run(() => api.exportAccount())} disabled={a.busy} className={secondary}><Download size={15} /> Download</button>
      </Section>
      <Section title="Delete your account" description="Permanently removes your account, conversations, documents, images, memories and reports. This can't be undone.">
        <form onSubmit={remove} className="space-y-2">
          <input type="password" autoComplete="current-password" placeholder="Your password" aria-label="Your password" value={pw} onChange={(e) => setPw(e.target.value)} className={input} />
          <label htmlFor="del-confirm" className="block text-xs text-muted">Type DELETE to confirm</label>
          <input id="del-confirm" value={typed} onChange={(e) => setTyped(e.target.value)} autoCapitalize="characters" className={input} />
          <button type="submit" disabled={a.busy || !pw || typed !== "DELETE"} className="inline-flex min-h-11 items-center gap-2 rounded-xl bg-red-500 px-4 text-sm font-semibold text-white hover:bg-red-600 disabled:opacity-50">
            <Trash2 size={15} /> Delete my account
          </button>
        </form>
      </Section>
      <ErrorText error={a.error} />
    </div>
  );
}

export default function AccountPanel({ open, onClose, user, auth }) {
  const [tab, setTab] = useState("profile");
  const ref = useRef(null);
  useDialog(open, onClose, ref);
  return (
    <AnimatePresence>
      {open && (
        <motion.div {...overlay} className="fixed inset-0 z-[55] flex items-end justify-center bg-black/60 backdrop-blur-sm sm:items-center sm:p-4"
          onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
          <motion.div ref={ref} role="dialog" aria-modal="true" aria-labelledby="account-title"
            initial={{ opacity: 0, y: 40 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 24 }} transition={spring}
            className="glass flex max-h-[92dvh] w-full max-w-lg flex-col rounded-t-3xl bg-panel pb-[env(safe-area-inset-bottom)] shadow-2xl sm:rounded-3xl">
            <div className="flex items-start gap-3 p-5 pb-3">
              <span className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent/15 text-accent"><UserCog size={20} /></span>
              <div className="min-w-0 flex-1">
                <h2 id="account-title" className="font-display text-lg font-semibold">Account</h2>
                <p className="truncate text-sm text-muted">{user.email}</p>
              </div>
              <button type="button" onClick={onClose} aria-label="Close" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-muted hover:bg-ink/10 hover:text-ink"><X size={20} /></button>
            </div>
            <div role="tablist" aria-label="Account sections" className="mx-5 grid grid-cols-2 gap-1 rounded-xl bg-ink/5 p-1 text-sm min-[420px]:grid-cols-4">
              {TABS.map(([id, label]) => (
                <button key={id} type="button" role="tab" aria-selected={tab === id} aria-controls={`acct-${id}`} onClick={() => setTab(id)}
                  className={`min-h-10 whitespace-nowrap rounded-lg px-2 ${tab === id ? "bg-panel font-semibold shadow" : "text-muted hover:text-ink"}`}>{label}</button>
              ))}
            </div>
            <div id={`acct-${tab}`} role="tabpanel" className="thin-scroll min-h-0 flex-1 space-y-3 overflow-y-auto p-5">
              {tab === "profile" && <Profile user={user} auth={auth} />}
              {tab === "security" && <><TwoStep user={user} auth={auth} /><ChangePassword /></>}
              {tab === "devices" && <Devices auth={auth} />}
              {tab === "data" && <YourData auth={auth} />}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
