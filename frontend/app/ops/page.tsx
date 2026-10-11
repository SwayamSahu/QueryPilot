"use client";
import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import { RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { useSession } from "@/components/SessionProvider";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card px-4 py-3">
      <div className="text-[12px] text-fog-400">{label}</div>
      <div className="tabular mt-1 text-[22px] font-semibold text-violet-300">{value}</div>
      {sub && <div className="text-[11px] text-fog-500">{sub}</div>}
    </div>
  );
}

export default function OpsPage() {
  const { session } = useSession();
  const [sum, setSum] = useState<any>(null);
  const [bench, setBench] = useState<any>(null);
  const [audit, setAudit] = useState<any[] | null>(null);
  const [drift, setDrift] = useState<any>(null);
  const [auditErr, setAuditErr] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!session) return;
    api.summary(session.token).then(setSum).catch(() => setSum(null));
    api.benchmark(session.token).then(setBench).catch(() => setBench(null));
    api.drift(session.token).then(setDrift).catch(() => setDrift(null));
    api.audit(session.token).then((a) => { setAudit(a); setAuditErr(null); }).catch((e) => { setAudit(null); setAuditErr(e.message); });
  }, [session]);
  useEffect(load, [load]);

  return (
    <div className="scroll-thin h-full space-y-5 overflow-y-auto p-5">
      <div className="flex items-center justify-between">
        <h1 className="text-[18px] font-semibold">Operations &amp; evaluation</h1>
        <button className="btn" onClick={load}><RefreshCw size={13} />Refresh</button>
      </div>

      {sum && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
            <Stat label="Questions (last 500)" value={String(sum.requests)} sub={`${sum.status.answered} answered · ${sum.status.blocked} blocked`} />
            <Stat label="Latency p50" value={sum.p50_ms != null ? `${sum.p50_ms.toFixed(0)} ms` : "—"} sub="end to end" />
            <Stat label="Latency p95" value={sum.p95_ms != null ? `${sum.p95_ms.toFixed(0)} ms` : "—"} />
            <Stat label="DB time p50" value={sum.db_p50_ms != null ? `${sum.db_p50_ms.toFixed(0)} ms` : "—"} />
            <Stat label="Avg LLM tokens" value={sum.avg_llm_tokens != null ? String(Math.round(sum.avg_llm_tokens)) : "0"} sub={sum.llm.model ?? "rules planner (no LLM)"} />
            <Stat label="Result cache hit rate" value={pct(sum.cache_hit_rate)} sub={`planner: ${sum.planner} · retriever: ${sum.retriever}`} />
          </div>
          <section className="card">
            <div className="card-h font-medium">Latency by pipeline stage</div>
            <table className="w-full text-[12.5px]">
              <thead><tr className="text-fog-400"><th className="px-4 py-2 text-left font-medium">Stage</th><th className="px-4 py-2 text-right font-medium">p50</th><th className="px-4 py-2 text-right font-medium">p95</th><th className="px-4 py-2 text-right font-medium">Spans</th></tr></thead>
              <tbody>{sum.stages.map((s: any) => (
                <tr key={s.stage} className="border-t border-ink-800"><td className="px-4 py-1.5 font-mono">{s.stage}</td>
                  <td className="tabular px-4 py-1.5 text-right">{s.p50_ms?.toFixed(1)} ms</td><td className="tabular px-4 py-1.5 text-right">{s.p95_ms?.toFixed(1)} ms</td>
                  <td className="tabular px-4 py-1.5 text-right text-fog-400">{s.n}</td></tr>))}</tbody>
            </table>
          </section>
        </>
      )}

      <section className="card">
        <div className="card-h"><span className="font-medium">Benchmark</span>
          {bench?.config && <span className="text-[11px] text-fog-400">{bench.overall.n} cases · planner {bench.config.planner} · LLM {bench.config.llm_provider} · ref. date {bench.config.reference_date} · {new Date(bench.generated_at).toLocaleString()}</span>}</div>
        {!bench || bench.status === "not_run" ? (
          <p className="p-4 text-[13px] text-fog-400">Not run yet. Run <code className="font-mono">python -m querypilot.eval.run</code> in the backend.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead><tr className="text-fog-400">{["Slice", "n", "Exec success", "Execution acc.", "Result acc.", "Safety pass", "False blocks", "Abstention", "p50", "p95"].map((h) => <th key={h} className="whitespace-nowrap px-3 py-2 text-right font-medium first:text-left">{h}</th>)}</tr></thead>
              <tbody>
                {[["Overall", bench.overall], ...Object.entries(bench.by_split).map(([k, v]) => [`split: ${k}`, v]), ...Object.entries(bench.by_category)].map(([name, b]: any) => (
                  <tr key={name} className={clsx("border-t border-ink-800", name === "Overall" && "font-semibold")}>
                    <td className="px-3 py-1.5">{name}</td><td className="tabular px-3 text-right">{b.n}</td>
                    <td className="tabular px-3 text-right">{pct(b.execution_success)}</td><td className="tabular px-3 text-right">{pct(b.execution_accuracy)}</td>
                    <td className="tabular px-3 text-right text-violet-300">{pct(b.result_accuracy)}</td><td className="tabular px-3 text-right">{pct(b.safety_pass_rate)}</td>
                    <td className="tabular px-3 text-right">{pct(b.false_block_rate)}</td><td className="tabular px-3 text-right">{pct(b.abstention_rate)}</td>
                    <td className="tabular px-3 text-right text-fog-400">{b.latency_p50_ms} ms</td><td className="tabular px-3 text-right text-fog-400">{b.latency_p95_ms} ms</td>
                  </tr>))}
              </tbody>
            </table>
            <p className="border-t border-ink-800 px-4 py-2.5 text-[12px] text-fog-400">
              Held-out phrasings were not used to tune the planner; dev phrasings were. Retrieval recall@{bench.config.retrieval_k}: {pct(bench.overall.retrieval_recall_at_k)} ·
              unsafe statements executed: {bench.overall.unsafe_executed} of {bench.overall.unsafe_cases} · confident errors: {bench.calibration?.confident_errors}. See benchmark/README.md for method and limitations.
            </p>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-h"><span className="font-medium">Usage drift (PSI, recent 100 vs earlier)</span><span className="text-[11px] text-fog-400">{drift?.status}</span></div>
        <div className="p-4 text-[12.5px]">
          {drift?.features ? (
            <div className="grid gap-3 md:grid-cols-4">{Object.entries(drift.features).map(([k, f]: any) => (
              <div key={k} className="rounded-lg border border-ink-700 p-3"><div className="text-fog-400">{k}</div>
                <div className={clsx("tabular text-[18px] font-semibold", f.level === "stable" ? "text-good" : f.level === "moderate" ? "text-warn" : "text-bad")}>{f.psi}</div>
                <div className="text-[11px] text-fog-500">{f.level}</div></div>))}</div>
          ) : <p className="text-fog-400">Needs at least {drift?.needed ?? 120} traced questions (have {drift?.have ?? 0}).</p>}
        </div>
      </section>

      <section className="card">
        <div className="card-h font-medium">Audit log (append-only)</div>
        {auditErr ? <p className="p-4 text-[13px] text-fog-400">{auditErr.includes("403") ? "The audit log requires the CFO or analyst role." : auditErr}</p> : (
          <div className="scroll-thin max-h-[420px] overflow-auto">
            <table className="w-full text-[12px]">
              <thead className="sticky top-0 bg-ink-850"><tr className="text-fog-400">{["Time", "User", "Role", "Question", "Status", "Rows", "ms"].map((h) => <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>)}</tr></thead>
              <tbody>{(audit ?? []).map((a, i) => (
                <tr key={i} className="border-t border-ink-800 align-top">
                  <td className="whitespace-nowrap px-3 py-1.5 text-fog-400">{new Date(a.ts).toLocaleTimeString()}</td><td className="px-3 py-1.5">{a.user_id}</td>
                  <td className="px-3 py-1.5 text-fog-400">{a.role}</td><td className="max-w-[420px] px-3 py-1.5">{a.question}{a.blocked_reason && <div className="text-[11px] text-bad">{a.blocked_reason}</div>}</td>
                  <td className="px-3 py-1.5"><span className={clsx("chip", a.status === "answered" ? "text-good" : a.status === "blocked" ? "text-bad" : "text-warn")}>{a.status}</span></td>
                  <td className="tabular px-3 py-1.5">{a.row_count ?? "—"}</td><td className="tabular px-3 py-1.5 text-fog-400">{Math.round(a.latency_ms)}</td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
