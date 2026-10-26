import { useCallback, useState } from "react";
import { AlertTriangle } from "lucide-react";

export const field = "min-h-11 w-full rounded-xl border border-line/15 bg-ink/5 px-3 text-base placeholder:text-muted/70 focus:border-accent focus:outline-none sm:text-sm";
export const primary = "btn-gradient inline-flex min-h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold disabled:opacity-50";
export const secondary = "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-xl border border-line/20 px-3 text-sm hover:bg-ink/10 disabled:opacity-50";
export const danger = "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-xl px-3 text-sm text-red-400 hover:bg-red-500/10 disabled:opacity-50";

export function Card({ title, actions, children, description }) {
  return (
    <section className="glass min-w-0 rounded-2xl p-4 sm:p-5" aria-label={typeof title === "string" ? title : undefined}>
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            {title && <h2 className="font-display text-base font-semibold">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Err({ error }) {
  return error ? (
    <p role="alert" className="flex items-start gap-1.5 text-sm text-red-400"><AlertTriangle size={15} className="mt-0.5 shrink-0" /> {error}</p>
  ) : null;
}

export function Skeleton({ h = "h-20" }) {
  return <div className={`${h} animate-pulse rounded-xl bg-ink/10`} aria-busy="true" />;
}

export function Badge({ children, tone = "muted" }) {
  const tones = { muted: "bg-ink/10 text-muted", ok: "bg-emerald-500/15 text-emerald-400", warn: "bg-amber-500/15 text-amber-400",
    bad: "bg-red-500/15 text-red-400", info: "bg-sky-500/15 text-sky-400", accent: "bg-accent/15 text-accent" };
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>{children}</span>;
}

/** Wraps an async action with busy and error state. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const run = useCallback(async (fn) => {
    setBusy(true);
    setError(null);
    try { return await fn(); } catch (e) { setError(e.message); return undefined; } finally { setBusy(false); }
  }, []);
  return { busy, error, setError, run };
}

/** Value for <input type="datetime-local"> from a Date. */
export function localInput(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function when(us) {
  return us ? new Date(us / 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
}

/** Browsers still report some old time-zone names (e.g. Asia/Calcutta); show and save the current ones. */
const TZ_ALIASES = { "Asia/Calcutta": "Asia/Kolkata", "Asia/Saigon": "Asia/Ho_Chi_Minh", "Asia/Katmandu": "Asia/Kathmandu",
  "Asia/Rangoon": "Asia/Yangon", "Asia/Dacca": "Asia/Dhaka", "Asia/Thimbu": "Asia/Thimphu", "Asia/Ulan_Bator": "Asia/Ulaanbaatar",
  "Atlantic/Faeroe": "Atlantic/Faroe", "Europe/Kiev": "Europe/Kyiv", "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
  "Pacific/Truk": "Pacific/Chuuk", "Pacific/Ponape": "Pacific/Pohnpei", "Pacific/Enderbury": "Pacific/Kanton" };
export const canonicalTz = (tz) => TZ_ALIASES[tz] || tz;

export function timeZones(current) {
  let list = [];
  try { list = Intl.supportedValuesOf("timeZone"); } catch { list = []; }
  return [...new Set([...list.map(canonicalTz), ...(current ? [current] : []), "UTC"])].sort();
}
