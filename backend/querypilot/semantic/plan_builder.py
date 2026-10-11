"""Turns a planner's *draft* (from rules or an LLM) into a fully resolved QueryPlan.

Both planners emit the same Draft shape; date arithmetic, defaults and comparison periods
are decided here, deterministically, and every default is recorded as an assumption.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from querypilot.core.models import Filter, QueryPlan, TimeSpec
from querypilot.semantic import timeparse as tp
from querypilot.semantic.catalog import Catalog


@dataclass
class Draft:
    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    time: TimeSpec | None = None
    compare_to: TimeSpec | None = None
    comparison: str = "none"  # none | previous_period | yoy | custom
    change_filter: str = "none"
    order_by: str | None = None
    order_dir: str = "desc"
    limit: int | None = None
    drivers: bool = False
    assumptions: list[str] = field(default_factory=list)


def assemble(d: Draft, cat: Catalog, today: date, bounds: tuple[date, date]) -> QueryPlan:
    lo, hi = bounds  # hi is exclusive
    assumptions = list(d.assumptions)
    dims = list(dict.fromkeys(d.dimensions))
    grain_dims = [x for x in dims if cat.dimensions[x].time_grain]
    if len(grain_dims) > 1:  # keep the finest explicit grain
        keep = grain_dims[0]
        dims = [x for x in dims if not cat.dimensions[x].time_grain or x == keep]

    time = d.time
    comparison = d.comparison
    if time is None:
        if comparison != "none" or d.drivers or d.change_filter != "none":
            cur_q = tp.quarter_start(today)
            s = tp.add_months(cur_q, -3)
            time = TimeSpec(start=s, end=cur_q, label=tp.q_label(s), grain="quarter")
            assumptions.append(f"No period given - compared the last complete quarter ({time.label}).")
        elif grain_dims:
            cur_m = date(today.year, today.month, 1)
            time = tp.months_range(tp.add_months(cur_m, -12), cur_m)
            assumptions.append(f"No period given - showing the last 12 complete months ({time.label}).")
        else:
            time = tp.clip(TimeSpec(start=lo, end=hi, label="", grain="all"), lo, hi)
            assumptions.append(f"No period given - used {time.label}.")
    elif time.grain == "all":
        time = tp.clip(time, lo, hi)

    if d.drivers and comparison == "none":
        comparison = "previous_period"
    if d.change_filter != "none" and comparison == "none":
        comparison = "previous_period"

    compare_time = None
    if comparison != "none" and grain_dims:
        # A trend already shows every period; widen it to include the comparison period instead.
        other = d.compare_to or (tp.same_period_last_year(time) if comparison == "yoy" else tp.previous_period(time))
        start = min(other.start, time.start)
        time = TimeSpec(start=start, end=max(other.end, time.end), label=f"{other.label} to {time.label}", grain="custom")
        assumptions.append("Trend view shows both periods side by side instead of a single comparison.")
        comparison = "none"
    elif comparison == "custom":
        compare_time = d.compare_to
        if compare_time is None:
            comparison, compare_time = "previous_period", tp.previous_period(time)
    elif comparison == "previous_period":
        compare_time = tp.previous_period(time)
    elif comparison == "yoy":
        compare_time = tp.same_period_last_year(time)

    if time.end > hi and time.grain != "all" and time.start < hi:
        assumptions.append(f"Data is available through {(hi - timedelta(days=1)).isoformat()}; {time.label} is partial.")
    if time.start >= hi or time.end <= lo:
        assumptions.append(f"{time.label} is outside the available data ({lo.isoformat()} to {(hi - timedelta(days=1)).isoformat()}).")

    order_by = d.order_by
    if order_by and comparison == "none" and order_by.endswith("_change"):
        order_by = order_by[: -len("_change")]
    plan = QueryPlan(
        metrics=list(dict.fromkeys(d.metrics)),
        dimensions=dims,
        filters=d.filters,
        time=time,
        comparison=comparison,  # type: ignore[arg-type]
        compare_to=compare_time,
        change_filter=d.change_filter if comparison != "none" else "none",  # type: ignore[arg-type]
        order_by=order_by,
        order_dir=d.order_dir,  # type: ignore[arg-type]
        limit=d.limit,
        drivers=d.drivers and comparison != "none",
        assumptions=assumptions,
    )
    return plan


def draft_from_plan(plan: QueryPlan) -> Draft:
    return Draft(
        metrics=list(plan.metrics), dimensions=list(plan.dimensions), filters=[f.model_copy() for f in plan.filters],
        time=plan.time, compare_to=plan.compare_to if plan.comparison == "custom" else None,
        comparison=plan.comparison, change_filter=plan.change_filter, order_by=plan.order_by,
        order_dir=plan.order_dir, limit=plan.limit, drivers=False, assumptions=[],
    )
