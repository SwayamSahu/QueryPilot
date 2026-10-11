"""Per-request tracing: one span per pipeline stage, exported to the DB, Prometheus and the UI."""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any

from querypilot.core.models import Span
from querypilot.ops import metrics as prom


class Tracer:
    def __init__(self):
        self.trace_id = str(uuid.uuid4())
        self.t0 = time.perf_counter()
        self.spans: list[Span] = []

    @contextmanager
    def span(self, name: str, **attrs: Any):
        start = time.perf_counter()
        holder: dict[str, Any] = dict(attrs)
        status = "ok"
        try:
            yield holder
        except Exception as e:
            status = "error"
            holder["error"] = f"{type(e).__name__}: {e}"[:500]
            raise
        finally:
            dur = (time.perf_counter() - start) * 1000
            self.spans.append(Span(name=name, start_ms=round((start - self.t0) * 1000, 2), duration_ms=round(dur, 2),
                                   status=holder.pop("_status", status), attributes=_clean(holder)))
            prom.STAGE_LATENCY.labels(stage=name).observe(dur / 1000)

    def total_ms(self) -> float:
        return round((time.perf_counter() - self.t0) * 1000, 2)

    def stage_totals(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for s in self.spans:
            out[s.name] = round(out.get(s.name, 0) + s.duration_ms, 2)
        return out


def _clean(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v if not isinstance(v, str) else v[:2000]
        elif isinstance(v, (list, tuple)):
            out[k] = [x if isinstance(x, (str, int, float, bool)) else str(x) for x in v][:50]
        else:
            out[k] = str(v)[:2000]
    return out
