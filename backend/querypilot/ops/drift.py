"""Usage drift monitoring (custom, dependency-free alternative to Evidently).

Compares a reference window of questions against the most recent window using the
Population Stability Index on: outcome status, requested metrics, planner intent and
the confidence distribution. PSI < 0.1 stable, 0.1-0.25 moderate shift, > 0.25 significant.
A rising clarify/blocked share or falling confidence usually means users are asking things
the semantic layer doesn't cover yet - a signal to extend the catalog or benchmark.
"""
from __future__ import annotations

import math
from collections import Counter


def psi(ref: Counter, cur: Counter, eps: float = 1e-4) -> float:
    keys = set(ref) | set(cur)
    nr, nc = sum(ref.values()) or 1, sum(cur.values()) or 1
    total = 0.0
    for k in keys:
        p, q = max(ref[k] / nr, eps), max(cur[k] / nc, eps)
        total += (q - p) * math.log(q / p)
    return round(total, 4)


def _bin(c: float | None) -> str:
    if c is None:
        return "none"
    return f"{min(int(float(c) * 10), 9) / 10:.1f}"


def drift_report(traces: list[dict], recent: int = 100) -> dict:
    """`traces` newest-first. Recent window = newest `recent`; reference = everything before it."""
    if len(traces) < recent + 20:
        return {"status": "insufficient_data", "needed": recent + 20, "have": len(traces)}
    cur, ref = traces[:recent], traces[recent:]
    feats = {
        "status": (Counter(t["status"] for t in ref), Counter(t["status"] for t in cur)),
        "intent": (Counter(t["intent"] or "none" for t in ref), Counter(t["intent"] or "none" for t in cur)),
        "metric": (Counter(m for t in ref for m in (t["metrics"] or ["none"])), Counter(m for t in cur for m in (t["metrics"] or ["none"]))),
        "confidence": (Counter(_bin(t["confidence"]) for t in ref), Counter(_bin(t["confidence"]) for t in cur)),
    }
    out = {}
    for name, (r, c) in feats.items():
        v = psi(r, c)
        out[name] = {"psi": v, "level": "stable" if v < 0.1 else "moderate" if v < 0.25 else "significant",
                     "reference": dict(r.most_common(8)), "recent": dict(c.most_common(8))}
    worst = max(out.values(), key=lambda x: x["psi"])
    return {"status": worst["level"], "reference_n": len(ref), "recent_n": len(cur), "features": out}
