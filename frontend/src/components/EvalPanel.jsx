import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Play, Plus, Trash2, XCircle } from "lucide-react";
import { api } from "../lib/api";

const when = (us) => new Date(us / 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
const pct = (a, b) => (b ? `${Math.round((a / b) * 100)}%` : "—");

function Tile({ label, value, hint }) {
  return (
    <div className="rounded-xl border border-line/10 p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 font-display text-xl font-semibold">{value}</p>
      {hint && <p className="text-[11px] text-muted">{hint}</p>}
    </div>
  );
}

const Check = ({ ok, label }) => ok == null ? null : (
  <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${ok ? "bg-emerald-500/15 text-emerald-400" : "bg-red-500/15 text-red-400"}`}>{ok ? "✓" : "✗"} {label}</span>
);

export default function EvalPanel() {
  const [cases, setCases] = useState([]);
  const [runs, setRuns] = useState([]);
  const [toolNames, setToolNames] = useState([]);
  const [run, setRun] = useState(null); // full run being shown
  const [judge, setJudge] = useState(false);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ name: "", question: "", keywords: "", expected_tool: "" });
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await api.adminEvals();
      setCases(d.cases); setRuns(d.runs); setToolNames(d.tools);
      return d;
    } catch (e) { setError(e.message); return null; }
  }, []);

  const show = useCallback(async (id) => {
    try { setRun(await api.adminEvalRun(id)); } catch (e) { setError(e.message); }
  }, []);

  useEffect(() => { load().then((d) => d?.runs[0] && show(d.runs[0].id)); return () => clearInterval(timer.current); }, [load, show]);

  useEffect(() => {
    clearInterval(timer.current);
    if (run?.status !== "running") return undefined;
    timer.current = setInterval(async () => {
      const r = await api.adminEvalRun(run.id).catch(() => null);
      if (r) { setRun(r); if (r.status !== "running") load(); }
    }, 1500);
    return () => clearInterval(timer.current);
  }, [run?.id, run?.status, load]);

  const start = async () => {
    setError(null);
    try { const { id } = await api.adminRunEval(judge); await show(id); } catch (e) { setError(e.message); }
  };
  const addCase = async (e) => {
    e.preventDefault();
    try { await api.adminAddEvalCase({ ...form, expected_tool: form.expected_tool || null }); setForm({ name: "", question: "", keywords: "", expected_tool: "" }); await load(); }
    catch (err) { setError(err.message); }
  };
  const removeCase = async (id) => { try { await api.adminDeleteEvalCase(id); await load(); } catch (e) { setError(e.message); } };

  const sm = run?.summary;
  const running = run?.status === "running";

  return (
    <section className="glass rounded-2xl p-4 sm:p-5" aria-labelledby="eval-title">
      <h2 id="eval-title" className="font-display text-base font-semibold">Quality evaluation</h2>
      <p className="mt-1 text-sm text-muted">Runs your test questions through the assistant and checks the answer text, tool use and speed. Each case uses one to a few model calls (plus one more per case if the relevance judge is on).</p>

      {error && <p role="alert" className="mt-3 flex items-start gap-2 rounded-xl border border-red-400/40 bg-red-500/10 p-3 text-sm"><AlertTriangle size={16} className="mt-0.5 shrink-0 text-red-400" /> {error}</p>}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" onClick={start} disabled={running || cases.length === 0} className="btn-gradient inline-flex min-h-11 items-center gap-2 rounded-xl px-4 text-sm font-semibold shadow-glow disabled:opacity-50">
          {running ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
          {running ? `Running ${sm.done}/${sm.total}…` : "Run evaluation"}
        </button>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input type="checkbox" checked={judge} onChange={(e) => setJudge(e.target.checked)} disabled={running} className="h-4 w-4 accent-[rgb(var(--accent))]" />
          Rate relevance with the model (1–5)
        </label>
      </div>

      {run && sm.done > 0 && (
        <div className="mt-4">
          <p className="text-xs text-muted">{when(run.created_at)} · {run.model}{run.status === "failed" && " · the run did not finish"}</p>
          <div className="mt-2 grid grid-cols-1 gap-2 min-[360px]:grid-cols-2 lg:grid-cols-4">
            <Tile label="Passed" value={`${sm.passed}/${sm.done}`} hint={pct(sm.passed, sm.done)} />
            <Tile label="Average speed" value={`${(sm.avg_latency_ms / 1000).toFixed(1)} s`} hint={`slowest ${(sm.max_latency_ms / 1000).toFixed(1)} s`} />
            <Tile label="Relevance" value={sm.avg_relevance != null ? `${sm.avg_relevance} / 5` : "—"} hint={run.judge ? "model-rated" : "judge was off"} />
            <Tile label="Tool use" value={sm.tool_success != null ? `${Math.round(sm.tool_success * 100)}%` : "—"} hint="expected tool used" />
          </div>
          <div className="thin-scroll mt-3 overflow-x-auto">
            <table className="w-full min-w-[620px] text-left text-sm">
              <thead className="text-muted"><tr>{["Case", "Result", "Checks", "Time", "Answer"].map((h) => <th key={h} className="pb-2 pr-3 font-medium">{h}</th>)}</tr></thead>
              <tbody>
                {run.results.map((r) => (
                  <tr key={r.case_id} className="border-t border-line/10 align-top">
                    <td className="py-2 pr-3 font-medium">{r.name}</td>
                    <td className="pr-3">{r.passed ? <span className="inline-flex items-center gap-1 text-emerald-400"><CheckCircle2 size={15} /> Pass</span> : <span className="inline-flex items-center gap-1 text-red-400"><XCircle size={15} /> Fail</span>}</td>
                    <td className="pr-3"><div className="flex flex-wrap gap-1"><Check ok={r.keywords_ok} label="answer" /><Check ok={r.tool_ok} label="tool" />{r.relevance != null && <span className="rounded-full bg-ink/10 px-2 py-0.5 text-[11px]">relevance {r.relevance}/5</span>}</div></td>
                    <td className="pr-3 text-muted">{(r.latency_ms / 1000).toFixed(1)} s</td>
                    <td className="max-w-[16rem] text-xs text-muted">{r.error ? <span className="text-red-400">{r.error}</span> : <details><summary className="cursor-pointer select-none">View</summary><p className="mt-1 whitespace-pre-wrap break-words">{r.answer}</p></details>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {runs.length > 1 && (
        <div className="mt-4">
          <p className="text-sm font-medium">Earlier runs</p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {runs.map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => show(r.id)} aria-pressed={run?.id === r.id}
                  className={`min-h-9 rounded-lg border px-3 text-xs transition-colors ${run?.id === r.id ? "border-accent bg-accent/15" : "border-line/15 hover:bg-ink/10"}`}>
                  {when(r.created_at)} · {pct(r.summary.passed, r.summary.done)}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-5 border-t border-line/10 pt-4">
        <p className="text-sm font-medium">Test cases ({cases.length})</p>
        <ul className="mt-2 space-y-2">
          {cases.map((c) => (
            <li key={c.id} className="flex items-start gap-3 rounded-xl border border-line/10 p-3 text-sm">
              <div className="min-w-0 flex-1">
                <p className="font-medium">{c.name}</p>
                <p className="break-words text-muted">{c.question}</p>
                <p className="mt-1 text-xs text-muted">
                  {c.keywords.length ? `Answer must contain: ${c.keywords.join(", ")}` : "No keyword check"}{c.expected_tool ? ` · must use ${c.expected_tool}` : ""}{c.use_tools && !c.expected_tool ? " · tools allowed" : ""}
                </p>
              </div>
              <button type="button" onClick={() => removeCase(c.id)} aria-label={`Delete test case: ${c.name}`} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-red-500/15 hover:text-red-400"><Trash2 size={16} /></button>
            </li>
          ))}
        </ul>

        <form onSubmit={addCase} className="mt-3 grid gap-2 sm:grid-cols-2">
          <label className="sr-only" htmlFor="ev-name">Case name</label>
          <input id="ev-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required maxLength={100} placeholder="Case name" className="min-h-11 rounded-xl border border-line/15 bg-ink/5 px-3 text-base focus:border-accent focus:outline-none sm:text-sm" />
          <label className="sr-only" htmlFor="ev-kw">Answer must contain (comma separated)</label>
          <input id="ev-kw" value={form.keywords} onChange={(e) => setForm({ ...form, keywords: e.target.value })} placeholder="Answer must contain (comma separated)" className="min-h-11 rounded-xl border border-line/15 bg-ink/5 px-3 text-base focus:border-accent focus:outline-none sm:text-sm" />
          <label className="sr-only" htmlFor="ev-q">Question</label>
          <textarea id="ev-q" value={form.question} onChange={(e) => setForm({ ...form, question: e.target.value })} required maxLength={1000} rows={2} placeholder="Question to ask the assistant" className="resize-none rounded-xl border border-line/15 bg-ink/5 p-3 text-sm focus:border-accent focus:outline-none sm:col-span-2" />
          <label className="sr-only" htmlFor="ev-tool">Tool that must be used</label>
          <select id="ev-tool" value={form.expected_tool} onChange={(e) => setForm({ ...form, expected_tool: e.target.value })} className="min-h-11 rounded-xl border border-line/15 bg-panel px-2 text-sm">
            <option value="">No tool required</option>
            {toolNames.map((t) => <option key={t} value={t}>Must use {t}</option>)}
          </select>
          <button type="submit" className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-line/20 px-4 text-sm font-semibold transition-colors hover:bg-ink/10"><Plus size={16} /> Add test case</button>
        </form>
      </div>
    </section>
  );
}
