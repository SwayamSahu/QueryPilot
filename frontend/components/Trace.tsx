import type { Span } from "@/lib/types";
import clsx from "clsx";

export function TraceWaterfall({ spans, total }: { spans: Span[]; total: number }) {
  const end = Math.max(total, ...spans.map((s) => s.start_ms + s.duration_ms), 1);
  return (
    <div className="space-y-1.5">
      {spans.map((s, i) => (
        <div key={i} className="grid grid-cols-[150px_1fr_70px] items-center gap-3 text-[12px]">
          <span className={clsx("truncate font-mono", s.status === "error" ? "text-bad" : "text-fog-300")} title={JSON.stringify(s.attributes)}>
            {s.name}
          </span>
          <div className="relative h-3.5 rounded bg-ink-800">
            <div className={clsx("absolute h-full rounded", s.status === "error" ? "bg-bad/70" : "bg-violet-500/70")}
              style={{ left: `${(s.start_ms / end) * 100}%`, width: `${Math.max((s.duration_ms / end) * 100, 0.6)}%` }} />
          </div>
          <span className="tabular text-right text-fog-400">{s.duration_ms.toFixed(1)} ms</span>
        </div>
      ))}
    </div>
  );
}
