"use client";
import { useEffect, useState } from "react";
import { BookOpen, Layers, Lock } from "lucide-react";
import { api } from "@/lib/api";
import { useSession } from "@/components/SessionProvider";

export default function CatalogPage() {
  const { session } = useSession();
  const [cat, setCat] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (session) api.catalog(session.token).then(setCat).catch((e) => setErr(e.message)); }, [session]);
  if (err) return <div className="p-6 text-bad">{err}</div>;
  if (!cat) return <div className="p-6 text-fog-400">Loading catalog…</div>;
  return (
    <div className="scroll-thin h-full overflow-y-auto p-5">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="text-[18px] font-semibold">Metric catalog</h1>
        <span className="chip">version {cat.catalog_version}</span><span className="chip">fingerprint {cat.fingerprint}</span>
        <span className="chip">policy {cat.policy_version}</span>
      </div>
      <p className="mb-5 max-w-3xl text-[13px] text-fog-400">Every answer is computed from these definitions. Changing one requires a version bump; the catalog version is part of the result-cache key, so answers never mix definitions.</p>
      <div className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
        <section className="card">
          <div className="card-h"><span className="flex items-center gap-2 font-medium"><BookOpen size={14} />Metrics ({cat.metrics.length})</span></div>
          <div className="divide-y divide-ink-800">
            {cat.metrics.map((m: any) => (
              <div key={m.key} className="px-4 py-3">
                <div className="flex flex-wrap items-center gap-2 text-[13px] font-semibold">{m.label}
                  <span className="chip font-mono">{m.key} v{m.version}</span><span className="chip">{m.format}</span></div>
                <p className="mt-1 text-[12.5px] text-fog-400">{m.description}</p>
                <pre className="sql mt-2">{m.sql}</pre>
                <p className="mt-1.5 text-[11px] text-fog-500">Synonyms: {m.synonyms.join(", ")}</p>
              </div>
            ))}
          </div>
        </section>
        <div className="space-y-4">
          <section className="card">
            <div className="card-h"><span className="flex items-center gap-2 font-medium"><Layers size={14} />Dimensions</span></div>
            <div className="divide-y divide-ink-800">
              {cat.dimensions.map((d: any) => (
                <div key={d.key} className="px-4 py-2.5 text-[12.5px]">
                  <div className="flex items-center gap-2"><b>{d.label}</b><span className="chip font-mono">{d.key}</span>{d.time_grain && <span className="chip">time grain</span>}</div>
                  <code className="mt-1 block font-mono text-[11px] text-fog-500">{d.sql}</code>
                </div>
              ))}
            </div>
          </section>
          <section className="card">
            <div className="card-h"><span className="flex items-center gap-2 font-medium"><Lock size={14} />Roles &amp; access</span></div>
            <div className="divide-y divide-ink-800">
              {Object.entries(cat.roles).map(([k, r]: [string, any]) => (
                <div key={k} className="px-4 py-2.5 text-[12.5px]">
                  <div className="flex items-center gap-2"><b className="capitalize">{k.replace("_", " ")}</b><span className="chip">regions: {r.region_scope}</span><span className="chip">≤ {r.max_rows} rows</span></div>
                  <p className="mt-1 text-[11px] text-fog-500">{r.tables.join(", ")}</p>
                </div>
              ))}
            </div>
          </section>
          <section className="card p-4 text-[12.5px]">
            <b>Changelog</b>
            <ul className="mt-2 space-y-1 text-fog-400">{cat.changelog.map((c: any) => <li key={c.version}><span className="font-mono text-fog-300">{c.version}</span> · {c.date} · {c.note}</li>)}</ul>
          </section>
        </div>
      </div>
    </div>
  );
}
