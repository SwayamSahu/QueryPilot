"""Post-execution result and metric validation."""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from querypilot.core.models import Check, QueryPlan, TableResult
from querypilot.semantic.catalog import Catalog


def check_result(plan: QueryPlan | None, table: TableResult, cat: Catalog, bounds,
                 total_for: Callable[[QueryPlan], float | None] | None = None) -> list[Check]:
    checks: list[Check] = []
    checks.append(Check(name="non_empty", passed=table.row_count > 0,
                        detail=f"{table.row_count} row(s) returned", severity="info" if table.row_count else "warn"))
    if table.truncated:
        checks.append(Check(name="row_cap", passed=False, detail=f"result truncated at {table.row_count} rows", severity="warn"))
    if plan is None:
        return checks

    keys = [c.key for c in table.columns]
    for m in plan.metrics:
        if m not in keys:
            continue
        i = keys.index(m)
        vals = [r[i] for r in table.rows]
        nulls = sum(v is None for v in vals)
        if vals and nulls / len(vals) > 0.5:
            checks.append(Check(name=f"nulls:{m}", passed=False, detail=f"{nulls}/{len(vals)} values are null", severity="warn"))
        if cat.metrics[m].format == "percent":
            bad = [v for v in vals if v is not None and not (-1.5 <= v <= 1.5)]
            checks.append(Check(name=f"range:{m}", passed=not bad, severity="error" if bad else "info",
                                detail=f"{cat.metrics[m].label} within plausible range" if not bad else f"{len(bad)} implausible ratio value(s)"))
        if m in ("revenue", "gross_sales", "units", "orders", "customers", "cogs"):
            neg = [v for v in vals if v is not None and v < 0]
            checks.append(Check(name=f"sign:{m}", passed=not neg, severity="error" if neg else "info",
                                detail="non-negative" if not neg else f"{len(neg)} negative value(s)"))

    lo, hi = bounds
    t = plan.time
    if t and (t.start < lo or t.end > hi):
        checks.append(Check(name="coverage", passed=False, severity="warn",
                            detail=f"{t.label} extends beyond available data ({lo} to {hi - timedelta(days=1)})"))
    else:
        checks.append(Check(name="coverage", passed=True, detail="period fully covered by data"))
    if plan.comparison != "none" and table.rows:
        pk = f"{plan.metrics[0]}_previous"
        if pk in keys:
            i = keys.index(pk)
            missing = sum(r[i] is None for r in table.rows)
            checks.append(Check(name="comparison_baseline", passed=missing == 0, severity="warn" if missing else "info",
                                detail="baseline present for all rows" if not missing else f"{missing} row(s) have no {plan.compare_to.label} baseline"))

    # Reconciliation: sum of an additive metric across groups must equal the ungrouped total.
    # This catches join fan-out / double counting in the generated SQL.
    if (total_for and plan.dimensions and plan.comparison == "none" and not plan.limit and not table.truncated
            and cat.metrics[plan.metrics[0]].additive):
        m = plan.metrics[0]
        i = keys.index(m)
        grouped = sum((r[i] or 0) for r in table.rows)
        total = total_for(plan.model_copy(update={"dimensions": [], "order_by": None, "limit": None}))
        if total is not None:
            ok = abs(grouped - total) <= max(1e-6, abs(total) * 1e-6)
            checks.append(Check(name="reconciliation", passed=ok, severity="info" if ok else "error",
                                detail=f"sum of groups {grouped:,.2f} {'matches' if ok else 'does NOT match'} independent total {total:,.2f}"))
    return checks
