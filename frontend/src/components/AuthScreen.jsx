import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, CheckCircle2, Eye, EyeOff, Loader2 } from "lucide-react";
import Avatar from "./Avatar";
import ThemeToggle from "./ThemeToggle";
import { api } from "../lib/api";
import { fadeUp, stagger } from "../lib/motion";

const TITLES = {
  login: ["Welcome back", "Sign in to continue your conversations."],
  register: ["Create your account", "Your chats stay private to your account."],
  forgot: ["Reset your password", "Enter your email and we'll send you a reset link."],
  reset: ["Choose a new password", "Use at least 8 characters that are hard to guess."],
  mfa: ["Two-step verification", "Enter the 6-digit code from your authenticator app, or a recovery code."],
};
const SUBMIT = { login: "Sign in", register: "Create account", forgot: "Send reset link", reset: "Set new password", mfa: "Verify" };
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function Field({ label, id, hint, error, children, ...props }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium">{label}</label>
      <div className="relative">
        <input
          id={id} aria-invalid={!!error} aria-describedby={hint || error ? `${id}-hint` : undefined} {...props}
          className={`min-h-12 w-full rounded-xl border bg-ink/5 px-3.5 text-base placeholder:text-muted/70 transition focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/40 ${error ? "border-red-400/70" : "border-line/15"} ${children ? "pr-12" : ""}`}
        />
        {children}
      </div>
      {(hint || error) && <p id={`${id}-hint`} className={`mt-1 text-xs ${error ? "text-red-400" : "text-muted"}`}>{error || hint}</p>}
    </div>
  );
}

function PasswordField(props) {
  const [show, setShow] = useState(false);
  return (
    <Field {...props} type={show ? "text" : "password"}>
      <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? "Hide password" : "Show password"} aria-pressed={show}
        className="absolute right-1 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-lg text-muted hover:bg-ink/10 hover:text-ink">
        {show ? <EyeOff size={17} /> : <Eye size={17} />}
      </button>
    </Field>
  );
}

const clearQuery = () => window.history.replaceState({}, "", window.location.pathname);

export default function AuthScreen({ auth }) {
  const params = new URLSearchParams(window.location.search);
  const resetToken = params.get("reset");
  const verifyToken = params.get("verify");
  const [mode, setMode] = useState(resetToken ? "reset" : "login");
  const [form, setForm] = useState({ name: "", email: "", password: "", code: "" });
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState(null);
  const [errorCode, setErrorCode] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [mfaToken, setMfaToken] = useState(null);
  const [config, setConfig] = useState({ registration_open: true });
  const verified = useRef(false);
  const [title, subtitle] = TITLES[mode];
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  useEffect(() => { api.authConfig().then(setConfig).catch(() => {}); }, []);
  useEffect(() => {
    if (!verifyToken || verified.current) return;
    verified.current = true;
    clearQuery();
    api.verifyEmail(verifyToken).then((r) => setNotice(r.message)).catch((e) => setError(e.message));
  }, [verifyToken]);

  const go = (m) => { setMode(m); setError(null); setErrorCode(null); setNotice(null); setTouched(false); };

  const fieldErrors = {
    name: mode === "register" && !form.name.trim() ? "Enter your name." : null,
    email: ["login", "register", "forgot"].includes(mode) && !EMAIL_RE.test(form.email.trim()) ? "Enter a valid email address." : null,
    password: ["login"].includes(mode) ? (!form.password ? "Enter your password." : null)
      : ["register", "reset"].includes(mode) && form.password.length < 8 ? "Use at least 8 characters." : null,
    code: mode === "mfa" && form.code.trim().length < 6 ? "Enter the code." : null,
  };
  const invalid = Object.values(fieldErrors).some(Boolean);

  const submit = async (e) => {
    e.preventDefault();
    setTouched(true);
    if (invalid) return;
    setError(null);
    setErrorCode(null);
    setNotice(null);
    setBusy(true);
    try {
      let res;
      if (mode === "login") res = await auth.login(form.email.trim(), form.password);
      else if (mode === "register") res = await auth.register(form.name.trim(), form.email.trim(), form.password);
      else if (mode === "mfa") res = await auth.loginMfa(mfaToken, form.code.trim());
      else if (mode === "forgot") setNotice((await api.forgot(form.email.trim())).message);
      else {
        res = await auth.resetPassword(resetToken, form.password);
        clearQuery();
      }
      if (res?.mfa) { setMfaToken(res.mfa); setForm((f) => ({ ...f, code: "" })); go("mfa"); }
      else if (res?.verify) { go("login"); setNotice(res.verify); }
      else if (res?.notice) { go("login"); setNotice(res.notice); }
    } catch (err) {
      setError(err.message);
      setErrorCode(err.code);
      if (err.code === "mfa_expired") go("login");
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    try { setNotice((await api.resendVerification(form.email.trim())).message); setError(null); }
    catch (e) { setError(e.message); }
  };

  const show = (k) => touched && fieldErrors[k];

  return (
    <div className="relative flex h-dvh items-start justify-center overflow-y-auto px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-[max(3.5rem,env(safe-area-inset-top))] sm:items-center sm:pt-4">
      <div className="absolute right-[max(0.75rem,env(safe-area-inset-right))] top-[max(0.75rem,env(safe-area-inset-top))]"><ThemeToggle /></div>
      <motion.main
        variants={stagger(0.07, 0.05)} initial="hidden" animate="show" aria-labelledby="auth-title"
        className="glass my-auto w-full max-w-md rounded-3xl p-5 shadow-2xl min-[380px]:p-6 sm:p-8"
      >
        <motion.div variants={fadeUp} className="mb-5 flex flex-col items-center text-center">
          <Avatar size={56} float />
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={mode} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.18 }}>
              <h1 id="auth-title" className="mt-4 font-display text-2xl font-semibold">{title}</h1>
              <p className="mt-1 text-sm text-muted">{subtitle}</p>
            </motion.div>
          </AnimatePresence>
        </motion.div>

        <motion.form variants={fadeUp} onSubmit={submit} className="space-y-4" noValidate>
          {mode === "register" && (
            <Field label="Name" id="name" autoComplete="name" value={form.name} onChange={set("name")} required maxLength={100} error={show("name")} />
          )}
          {["login", "register", "forgot"].includes(mode) && (
            <Field label="Email" id="email" type="email" inputMode="email" autoComplete="email" autoCapitalize="none" spellCheck={false}
              value={form.email} onChange={set("email")} required maxLength={255} error={show("email")} />
          )}
          {["login", "register", "reset"].includes(mode) && (
            <PasswordField
              label={mode === "reset" ? "New password" : "Password"} id="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={form.password} onChange={set("password")} required minLength={mode === "login" ? undefined : 8} maxLength={128}
              hint={mode !== "login" ? "At least 8 characters. Avoid common passwords." : undefined} error={show("password")}
            />
          )}
          {mode === "mfa" && (
            <Field label="Verification code" id="code" inputMode="numeric" autoComplete="one-time-code" autoFocus
              value={form.code} onChange={set("code")} maxLength={20} error={show("code")} placeholder="123 456" />
          )}

          <div aria-live="polite" className="space-y-2">
            {error && (
              <div role="alert" className="flex items-start gap-2 rounded-xl border border-red-400/40 bg-red-500/10 p-3 text-sm">
                <AlertTriangle size={16} className="mt-0.5 shrink-0 text-red-400" />
                <div>
                  <p>{error}</p>
                  {errorCode === "email_unverified" && (
                    <button type="button" onClick={resend} className="mt-1 min-h-9 font-semibold text-volt underline-offset-4 hover:underline">Send the link again</button>
                  )}
                  {errorCode === "locked" && (
                    <button type="button" onClick={() => go("forgot")} className="mt-1 min-h-9 font-semibold text-volt underline-offset-4 hover:underline">Reset your password</button>
                  )}
                </div>
              </div>
            )}
            {notice && (
              <p className="flex items-start gap-2 rounded-xl border border-emerald-400/40 bg-emerald-500/10 p-3 text-sm">
                <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-400" /> {notice}
              </p>
            )}
          </div>

          <motion.button
            type="submit" disabled={busy} whileHover={{ scale: busy ? 1 : 1.02 }} whileTap={{ scale: 0.98 }}
            className="btn-gradient flex min-h-12 w-full items-center justify-center gap-2 rounded-xl font-semibold shadow-glow disabled:opacity-60"
          >
            {busy && <Loader2 size={18} className="animate-spin" />}
            {SUBMIT[mode]}
          </motion.button>
        </motion.form>

        <motion.div variants={fadeUp} className="mt-5 space-y-2 text-center text-sm text-muted">
          {mode === "login" && (
            <>
              <p><button type="button" onClick={() => go("forgot")} className="min-h-10 text-volt underline-offset-4 hover:underline">Forgot your password?</button></p>
              {config.registration_open ? (
                <p>New here? <button type="button" onClick={() => go("register")} className="min-h-10 font-semibold text-ink hover:underline">Create an account</button></p>
              ) : <p>New accounts are created by an administrator.</p>}
            </>
          )}
          {mode === "register" && (
            <p>Already have an account? <button type="button" onClick={() => go("login")} className="min-h-10 font-semibold text-ink hover:underline">Sign in</button></p>
          )}
          {["forgot", "reset", "mfa"].includes(mode) && (
            <p><button type="button" onClick={() => { clearQuery(); go("login"); }} className="min-h-10 font-semibold text-ink hover:underline">Back to sign in</button></p>
          )}
        </motion.div>
      </motion.main>
    </div>
  );
}
