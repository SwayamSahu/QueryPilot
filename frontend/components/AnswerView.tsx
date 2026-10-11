"use client";
import { useState } from "react";
import clsx from "clsx";
import { AlertTriangle, BarChart3, CheckCircle2, Code2, Copy, Database, Gauge, Info, Lightbulb, ShieldAlert, Table2, XCircle } from "lucide-react";
import type { AskResponse } from "@/lib/types";
import { fmt } from "@/lib/format";
import { Chart } from "./Chart";
import { TraceWaterfall } from "./Trace";

const TABS = ["SQL", "Metric definitions", "Checks", "Trace", "Plan"] as const;

export function StatusBadge({ r }: { r: AskResponse }) {
  const m = {
    answered: ["Verified answer", "text-good border-good/30 bg-good/10", CheckCircle2],
    clarify: ["Needs clarification", "text-warn border-warn/30 bg-warn/10", Info],
    blocked: ["Blocked by policy", "text-bad border-bad/30 bg-bad/10", ShieldAlert],
    error: ["Error", "text-bad border-bad/30 bg-bad/10", XCircle],
  } as const;
  const [label, cls, Icon] = m[r.status];
  return <span className={clsx("inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium", cls)}><Icon size={12} />{label}</span>;
}

function Confidence({ v }: { v: number }) {
  const pct = Math.round(v * 100);
  const color = v >= 0.8 ? "bg-good" : v >= 0.55 ? "bg-warn" : "bg-bad";
  return (
    <span className="chip" title="Confidence combines planner coverage, repairs and result checks">
      <Gauge size={11} /> confidence
      <span className="relative h-1.5 w-12 overflow-hidden rounded bg-ink-700"><span className={clsx("absolute inset-y-0 left-0", color)} style={{ width: `${pct}%` }} /></span>
      <span className="tabular">{pct}%</span>
    </span>
  );
}

export function AnswerView({ r }: { r: AskResponse }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>(r.sql ? "SQL" : "Trace");
  const failed = r.checks.filter((c) => !c.passed);
  const rows = r.table?.row_count ?? 0;
  const dbms = r.timings.db_ms ?? 0;

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------ headline */}
      <section className="card p-5">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <StatusBadge r={r} />
          {r.status === "answered" && <Confidence v={r.confidence} />}
          {r.route !== "none" && <span className="chip">{r.route === "semantic" ? "governed semantic layer" : "validated SQL"}</span>}
          {r.cache_hit && <span className="chip">cached result</span>}
          <span className="chip tabular">{r.timings.total_ms?.toFixed(0)} ms</span>
        </div>
        <h1 className="text-[17px] font-semibold leading-snug text-fog-100">{r.question}</h1>
        <p className="mt-2 max-w-4xl text-[14px] leading-relaxed text-fog-300">{r.answer}</p>
        {r.assumptions.length > 0 && (
          <ul className="mt-3 space-y-1">
            {r.assumptions.map((a, i) => (
              <li key={i} className="flex items-start gap-1.5 text-[12px] text-fog-400"><Info size={13} className="mt-0.5 shrink-0 text-sky-400" />{a}</li>
            ))}
          </ul>
        )}
        {r.blocked_reason && r.status === "blocked" && (
          <p className="mt-3 rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-[13px] text-bad">
            Nothing was executed. Category: <b>{String(r.usage.blocked_category ?? "policy")}</b>. This attempt is recorded in the audit log.
          </p>
        )}
      </section>

      {/* ------------------------------------------------ KPI tiles */}
      {r.kpis.length > 0 && (
        <section className={clsx("grid gap-3", r.kpis.length >= 3 ? "grid-cols-2 lg:grid-cols-4" : "grid-cols-2")}>
          {r.kpis.map((k, i) => (
            <div key={i} className="card px-4 py-3.5">
              <div className="truncate text-[12px] text-fog-400">{k.label}</div>
              <div className="tabular mt-1 text-[26px] font-semibold tracking-tight text-violet-300">{k.display}</div>
              {k.change !== undefined && k.change !== null && (
                <div className="tabular mt-0.5 text-[12px]">
                  <span className={k.change < 0 ? "text-bad" : "text-good"}>{k.change_display}</span>
                  <span className="text-fog-500"> vs {fmt(k.previous ?? null, k.format)} ({k.previous_label})</span>
                </div>
              )}
            </div>
          ))}
        </section>
      )}

      {/* ------------------------------------------------ chart + drivers */}
      {r.table && r.chart && r.chart.type !== "kpi" && r.chart.type !== "table" && (
        <section className={clsx("grid gap-4", r.drivers.length ? "xl:grid-cols-[1.4fr_1fr]" : "")}>
          <div className="card">
            <div className="card-h">
              <span className="flex items-center gap-2 font-medium"><BarChart3 size={14} className="text-fog-400" />{r.chart.title}</span>
              <span className="flex items-center gap-1.5 text-[11px] text-fog-400"><Code2 size={12} />SQL · {rows} rows · {dbms.toFixed(0)} ms</span>
            </div>
            <div className="p-3"><Chart spec={r.chart} table={r.table} /></div>
          </div>
          {r.drivers.length > 0 && (
            <div className="card">
              <div className="card-h"><span className="flex items-center gap-2 font-medium"><Lightbulb size={14} className="text-warn" />What changed</span>
                <span className="text-[11px] text-fog-400">{Number(r.usage.supporting_queries ?? 0)} supporting queries</span></div>
              <div className="space-y-3 p-4">
                {r.drivers.map((d, i) => (
                  <div key={i} className="rounded-lg border border-ink-700 bg-ink-850 p-3">
                    <div className="mb-1 flex items-center justify-between">
                      <span className="text-[13px] font-semibold">{d.member}</span>
                      <span className="chip">main driver: {d.main_driver}</span>
                    </div>
                    {d.components && d.discount_rate && (
                      <div className="mb-2 grid grid-cols-3 gap-2 text-center">
                        {[["Discounting", d.components.discount_effect], ["Cost vs price", d.components.cost_effect], ["Category mix", d.components.mix_effect]].map(([l, v]) => (
                          <div key={String(l)} className="rounded-md bg-ink-800 px-2 py-1.5">
                            <div className="text-[10px] uppercase tracking-wide text-fog-500">{l}</div>
                            <div className={clsx("tabular text-[13px] font-semibold", Number(v) < 0 ? "text-bad" : "text-good")}>{fmt(Number(v), "pp")}</div>
                          </div>
                        ))}
                      </div>
                    )}
                    <p className="text-[12px] leading-relaxed text-fog-400">{d.statement}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* ------------------------------------------------ table */}
      {r.table && r.table.rows.length > 0 && (
        <section className="card">
          <div className="card-h">
            <span className="flex items-center gap-2 font-medium"><Table2 size={14} className="text-fog-400" />Result</span>
            <span className="text-[11px] text-fog-400">{r.table.row_count} rows{r.table.truncated ? " · truncated at row cap" : ""}</span>
          </div>
          <div className="scroll-thin max-h-[360px] overflow-auto">
            <table className="w-full text-[12.5px]">
              <thead className="sticky top-0 bg-ink-850">
                <tr>{r.table.columns.map((c) => (
                  <th key={c.key} className={clsx("whitespace-nowrap px-4 py-2 font-medium text-fog-400", c.kind === "dimension" ? "text-left" : "text-right")}>{c.label}</th>
                ))}</tr>
              </thead>
              <tbody>
                {r.table.rows.slice(0, 200).map((row, i) => (
                  <tr key={i} className="border-t border-ink-800 hover:bg-ink-850">
                    {row.map((v, j) => {
                      const c = r.table!.columns[j];
                      const neg = typeof v === "number" && v < 0 && (c.kind === "change" || c.kind === "change_pct");
                      return <td key={j} className={clsx("tabular whitespace-nowrap px-4 py-1.5", c.kind === "dimension" ? "text-left" : "text-right", neg && "text-bad")}>{fmt(v, c.format)}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ------------------------------------------------ evidence */}
      {(r.sql || r.spans.length > 0) && (
        <section className="card">
          <div className="flex items-center gap-1 border-b border-ink-700 px-2" role="tablist">
            {TABS.map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                className={clsx("relative px-3 py-2.5 text-[12.5px] transition", tab === t ? "text-fog-100" : "text-fog-400 hover:text-fog-100")}>
                {t}{t === "Checks" && failed.length > 0 && <span className="ml-1 rounded bg-warn/20 px-1 text-[10px] text-warn">{failed.length}</span>}
                {tab === t && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded bg-violet-400" />}
              </button>
            ))}
            <span className="ml-auto pr-2 text-[11px] text-fog-500">catalog {r.catalog_version} · trace {r.trace_id.slice(0, 8)}</span>
          </div>
          <div className="p-4">
            {tab === "SQL" && (
              <div className="space-y-3">
                {r.sql ? <SqlBlock sql={r.sql} label="Executed (validated, read-only, tenant-scoped by row-level security)" /> : <p className="text-[13px] text-fog-400">No SQL was generated.</p>}
                {(r.usage.supporting_sql as string[] | undefined)?.map((s, i) => <SqlBlock key={i} sql={s} label={`Supporting query ${i + 1} (reconciliation / driver analysis)`} />)}
              </div>
            )}
            {tab === "Metric definitions" && (
              r.metrics_used.length ? (
                <div className="space-y-3">
                  {r.metrics_used.map((m) => (
                    <div key={m.name} className="rounded-lg border border-ink-700 p-3">
                      <div className="flex items-center gap-2 text-[13px] font-semibold">{m.label}<span className="chip font-mono">{m.name} v{m.version}</span></div>
                      <p className="mt-1 text-[12.5px] text-fog-400">{m.definition}</p>
                      <pre className="sql mt-2">{m.sql}</pre>
                    </div>
                  ))}
                </div>
              ) : <p className="text-[13px] text-fog-400">Raw SQL route: no governed metric definitions were involved.</p>
            )}
            {tab === "Checks" && (
              <ul className="space-y-1.5">
                {r.checks.map((c, i) => (
                  <li key={i} className="flex items-start gap-2 text-[12.5px]">
                    {c.passed ? <CheckCircle2 size={14} className="mt-0.5 text-good" /> : c.severity === "error" ? <XCircle size={14} className="mt-0.5 text-bad" /> : <AlertTriangle size={14} className="mt-0.5 text-warn" />}
                    <span className="font-mono text-fog-300">{c.name}</span><span className="text-fog-400">{c.detail}</span>
                  </li>
                ))}
              </ul>
            )}
            {tab === "Trace" && (
              <div className="space-y-4">
                <TraceWaterfall spans={r.spans} total={r.timings.total_ms ?? 0} />
                <div className="flex flex-wrap gap-2 text-[11px]">
                  <span className="chip"><Database size={11} /> DB {dbms.toFixed(1)} ms · EXPLAIN cost {Number(r.usage.db_plan_cost ?? 0).toFixed(0)}</span>
                  <span className="chip">LLM {Number(r.usage.llm_calls ?? 0)} calls · {Number(r.usage.input_tokens ?? 0) + Number(r.usage.output_tokens ?? 0)} tokens</span>
                  <span className="chip">repairs {Number(r.usage.repairs ?? 0)}</span>
                  <span className="chip">est. cost ${Number(r.usage.est_cost_usd ?? 0).toFixed(4)}</span>
                </div>
              </div>
            )}
            {tab === "Plan" && <pre className="sql">{JSON.stringify(r.plan, null, 2) ?? "null"}</pre>}
          </div>
        </section>
      )}
    </div>
  );
}

function SqlBlock({ sql, label }: { sql: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[11px] text-fog-500">
        <span>{label}</span>
        <button className="inline-flex items-center gap-1 hover:text-fog-100" onClick={() => {
          navigator.clipboard?.writeText(sql).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); }).catch(() => {});
        }}><Copy size={11} />{copied ? "copied" : "copy"}</button>
      </div>
      <pre className="sql max-h-80">{sql}</pre>
    </div>
  );
}
