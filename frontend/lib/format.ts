import type { Fmt } from "./types";

export function fmt(v: unknown, f: Fmt | string): string {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "number") return String(v);
  switch (f) {
    case "currency": {
      const a = Math.abs(v), s = v < 0 ? "−" : "";
      if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
      if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(2)}M`;
      if (a >= 1e4) return `${s}$${(a / 1e3).toFixed(1)}K`;
      return `${s}$${a.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }
    case "percent": return `${(v * 100).toFixed(1)}%`;
    case "pp": return `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)} pp`;
    case "number": return Number.isInteger(v) || Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : v.toFixed(2);
    default: return String(v);
  }
}

export function axisFmt(f: Fmt | string) {
  return (v: number) => {
    if (f === "percent") return `${(v * 100).toFixed(0)}%`;
    if (f === "currency") return Math.abs(v) >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : Math.abs(v) >= 1e3 ? `$${(v / 1e3).toFixed(0)}K` : `$${v}`;
    return Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(0)}K` : `${v}`;
  };
}
