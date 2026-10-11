"""Grounded explanations: every number in the narrative is read from the result set.

The narrative is template-built from the rows (no free-form generation), so it cannot
contain a figure the query did not return. If QP_LLM_NARRATIVE is enabled the model may
rephrase it, but its text is rejected unless every number it contains appears in ours.
"""
from __future__ import annotations

import re
from typing import Any

from querypilot.core.models import ChartSpec, ColumnMeta, QueryPlan, TableResult
from querypilot.semantic.catalog import Catalog


def fmt(v: Any, f: str) -> str:
    if v is None:
        return "n/a"
    if f == "currency":
        a = abs(v)
        sign = "-" if v < 0 else ""
        if a >= 1e9:
            return f"{sign}${a / 1e9:.2f}B"
        if a >= 1e6:
            return f"{sign}${a / 1e6:.2f}M"
        if a >= 1e4:
            return f"{sign}${a / 1e3:.1f}K"
        return f"{sign}${a:,.2f}"
    if f == "percent":
        return f"{v * 100:.1f}%"
    if f == "pp":
        return f"{v * 100:+.1f} pp"
    if f == "number":
        return f"{v:,.0f}" if float(v).is_integer() or abs(v) >= 100 else f"{v:,.2f}"
    return str(v)


def fmt_delta(v: Any, f: str) -> str:
    if v is None:
        return "n/a"
    if f == "percent":
        return f"{v * 100:+.1f} pp"
    s = fmt(abs(v), f)
    return ("+" if v >= 0 else "−") + s


def _idx(table: TableResult) -> dict[str, int]:
    return {c.key: i for i, c in enumerate(table.columns)}


def _label(row: list, table: TableResult) -> str:
    dims = [i for i, c in enumerate(table.columns) if c.kind == "dimension"]
    return " · ".join(str(row[i]) for i in dims) if dims else "Total"


def narrative(plan: QueryPlan, table: TableResult, cat: Catalog, drivers: list[dict]) -> tuple[str, list[dict]]:
    """Returns (answer text, KPI tiles)."""
    ix = _idx(table)
    m0 = plan.metrics[0]
    mdef = cat.metrics[m0]
    f0 = mdef.format
    period = plan.time.label if plan.time else ""
    kpis: list[dict] = []
    rows = table.rows
    scope = ""
    if plan.filters:
        scope = " for " + "; ".join(("excluding " if f.op == "not_in" else "") + ", ".join(f.values) for f in plan.filters)

    if not rows:
        if plan.change_filter == "declining":
            dim = cat.dimensions[plan.dimensions[0]].label.lower() if plan.dimensions else "item"
            return (f"No {dim} saw {mdef.label.lower()} decline in {period} versus {plan.compare_to.label if plan.compare_to else 'the prior period'}{scope}.", kpis)
        if plan.change_filter == "increasing":
            dim = cat.dimensions[plan.dimensions[0]].label.lower() if plan.dimensions else "item"
            return (f"No {dim} saw {mdef.label.lower()} increase in {period}{scope}.", kpis)
        return (f"No matching data for {period}{scope}.", kpis)

    # ---- single row, no dimensions ------------------------------------------------
    if not plan.dimensions:
        r = rows[0]
        parts = []
        for m in plan.metrics:
            md = cat.metrics[m]
            cur = r[ix[m]]
            tile = {"label": md.label, "value": cur, "format": md.format, "display": fmt(cur, md.format)}
            if plan.comparison != "none":
                prev, chg = r[ix[f"{m}_previous"]], r[ix[f"{m}_change"]]
                tile.update({"previous": prev, "change": chg, "change_display": fmt_delta(chg, md.format),
                             "previous_label": plan.compare_to.label})
                pct = f", {r[ix[f'{m}_change_pct']] * 100:+.1f}%" if f"{m}_change_pct" in ix and r[ix[f"{m}_change_pct"]] is not None else ""
                parts.append(f"{md.label} was {fmt(cur, md.format)} in {period} vs {fmt(prev, md.format)} in "
                             f"{plan.compare_to.label} ({fmt_delta(chg, md.format)}{pct})")
            else:
                parts.append(f"{md.label.lower() if parts else md.label} {fmt(cur, md.format)}")
            kpis.append(tile)
        if plan.comparison != "none":
            text = "; ".join(parts) + f"{scope}."
        elif len(parts) > 1:
            text = f"In {period}{scope}: " + ", ".join(parts) + "."
        else:
            text = f"{mdef.label} for {period}{scope} was {fmt(r[ix[m0]], f0)}."
        return text + _driver_text(drivers), kpis

    dim_label = cat.dimensions[plan.dimensions[0]].label
    grain = next((d for d in plan.dimensions if cat.dimensions[d].time_grain), None)

    # ---- comparison by dimension ------------------------------------------------------
    if plan.comparison != "none":
        chg_i, prev_i, cur_i = ix[f"{m0}_change"], ix[f"{m0}_previous"], ix[m0]
        items = [f"{_label(r, table)} ({fmt(r[prev_i], f0)} → {fmt(r[cur_i], f0)}, {fmt_delta(r[chg_i], f0)})" for r in rows[:5]]
        cmp = plan.compare_to.label if plan.compare_to else "the prior period"
        n = len(rows)
        noun = dim_label.lower() + ("s" if n != 1 and not dim_label.endswith("s") else "")
        if plan.change_filter == "declining":
            text = f"{n} {noun} saw {mdef.label.lower()} decline in {period} vs {cmp}{scope}: " + "; ".join(items)
        elif plan.change_filter == "increasing":
            text = f"{n} {noun} saw {mdef.label.lower()} increase in {period} vs {cmp}{scope}: " + "; ".join(items)
        else:
            valid = [r for r in rows if r[chg_i] is not None]
            if valid:
                up = max(valid, key=lambda r: r[chg_i])
                down = min(valid, key=lambda r: r[chg_i])
                text = (f"{mdef.label} {period} vs {cmp}{scope}: largest increase {_label(up, table)} "
                        f"({fmt_delta(up[chg_i], f0)}), largest decrease {_label(down, table)} ({fmt_delta(down[chg_i], f0)})")
            else:
                text = f"{mdef.label} {period} vs {cmp}{scope}"
        if len(rows) > 5:
            text += f" (+{len(rows) - 5} more in the table)"
        for r in rows[:4]:
            kpis.append({"label": _label(r, table), "value": r[cur_i], "format": f0, "display": fmt(r[cur_i], f0),
                         "previous": r[prev_i], "change": r[chg_i], "change_display": fmt_delta(r[chg_i], f0),
                         "previous_label": cmp})
        return text + "." + _driver_text(drivers), kpis

    # ---- trend ---------------------------------------------------------------------------
    if grain and len(plan.dimensions) == 1:
        vals = [(r[0], r[ix[m0]]) for r in rows if r[ix[m0]] is not None]
        if vals:
            hi, lo = max(vals, key=lambda x: x[1]), min(vals, key=lambda x: x[1])
            first, last = vals[0], vals[-1]
            change = ""
            if first[1] and len(vals) > 1:
                if f0 == "percent":
                    change = f" It moved from {fmt(first[1], f0)} ({first[0]}) to {fmt(last[1], f0)} ({last[0]}), {fmt_delta(last[1] - first[1], f0)}."
                else:
                    change = f" From {first[0]} to {last[0]} it changed {((last[1] - first[1]) / abs(first[1])) * 100:+.1f}%."
            text = (f"{mdef.label}{scope} by {dim_label.lower()}, {period}: peak {fmt(hi[1], f0)} in {hi[0]}, "
                    f"low {fmt(lo[1], f0)} in {lo[0]}.{change}")
            kpis = [{"label": f"Peak ({hi[0]})", "value": hi[1], "format": f0, "display": fmt(hi[1], f0)},
                    {"label": f"Low ({lo[0]})", "value": lo[1], "format": f0, "display": fmt(lo[1], f0)}]
            return text, kpis

    # ---- trend split by another dimension (one series per member) -----------------------
    if grain and len(plan.dimensions) == 2:
        other = next(d for d in plan.dimensions if d != grain)
        gi, oi, mi = ix[grain], ix[other], ix[m0]
        series: dict[str, list[tuple[str, float]]] = {}
        for r in rows:
            if r[mi] is not None:
                series.setdefault(str(r[oi]), []).append((str(r[gi]), r[mi]))
        summary = []
        for name, pts in series.items():
            pts.sort()
            agg = sum(v for _, v in pts) if mdef.additive else pts[-1][1]
            delta = (pts[-1][1] - pts[0][1]) if f0 == "percent" else ((pts[-1][1] - pts[0][1]) / abs(pts[0][1]) if pts[0][1] else None)
            summary.append((name, agg, delta, pts[0][0], pts[-1][0]))
        if summary:
            summary.sort(key=lambda x: -x[1])
            lead = summary[0]
            what = "total" if mdef.additive else "latest"
            text = (f"{mdef.label}{scope} by {grain} and {cat.dimensions[other].label.lower()}, {period}: {lead[0]} is highest "
                    f"({what} {fmt(lead[1], f0)}).")
            moves = [s for s in summary if s[2] is not None]
            if moves:
                up, down = max(moves, key=lambda s: s[2]), min(moves, key=lambda s: s[2])
                show = (lambda v: fmt_delta(v, f0)) if f0 == "percent" else (lambda v: f"{v * 100:+.1f}%")
                text += (f" From {lead[3]} to {lead[4]}, change ranged from {show(down[2])} ({down[0]}) to {show(up[2])} ({up[0]}).")
            kpis = [{"label": f"{s[0]} ({what})", "value": s[1], "format": f0, "display": fmt(s[1], f0)} for s in summary[:4]]
            return text, kpis

    # ---- ranking / breakdown ---------------------------------------------------------
    top = rows[0]
    total = sum((r[ix[m0]] or 0) for r in rows) if mdef.additive and not table.truncated else None
    if plan.limit == 1:
        text = f"{_label(top, table)} had the {'lowest' if plan.order_dir == 'asc' else 'highest'} {mdef.label.lower()} in {period}{scope}: {fmt(top[ix[m0]], f0)}."
    else:
        lead = f"{_label(top, table)} {'is lowest' if plan.order_dir == 'asc' and plan.order_by else 'leads'} with {fmt(top[ix[m0]], f0)}"
        if total and plan.limit is None and not plan.filters and total > 0 and len(rows) > 1:
            lead += f" ({top[ix[m0]] / total * 100:.0f}% of the total {fmt(total, f0)})"
        rest = ", ".join(f"{_label(r, table)} ({fmt(r[ix[m0]], f0)})" for r in rows[1:4])
        text = f"{mdef.label} by {dim_label.lower()}, {period}{scope}: {lead}" + (f", followed by {rest}" if rest else "") + "."
        if len(plan.metrics) > 1:
            others = "; ".join(f"{cat.metrics[m].label} {fmt(top[ix[m]], cat.metrics[m].format)}" for m in plan.metrics[1:])
            text += f" ({_label(top, table)}: {others}.)"
    for r in rows[:4]:
        kpis.append({"label": _label(r, table), "value": r[ix[m0]], "format": f0, "display": fmt(r[ix[m0]], f0)})
    return text, kpis


def _driver_text(drivers: list[dict]) -> str:
    if not drivers:
        return ""
    parts = []
    for d in drivers:
        p = f"{d['member']} mainly {d['main_driver']}" if d["metric"] in ("gross_margin", "gross_profit") else \
            f"{d['member']} led by {d['main_driver']}"
        if d.get("top_breakdown"):
            p += f" ({d['top_breakdown']} hit hardest)"
        parts.append(p)
    return " What changed: " + "; ".join(parts) + "."


def chart_for(plan: QueryPlan, table: TableResult, cat: Catalog) -> ChartSpec:
    m0 = plan.metrics[0]
    f0 = cat.metrics[m0].format
    if not plan.dimensions:
        return ChartSpec(type="kpi", title=cat.metrics[m0].label)
    grain = next((d for d in plan.dimensions if cat.dimensions[d].time_grain), None)
    if plan.comparison != "none":
        return ChartSpec(type="grouped_bar", x=plan.dimensions[0], title=f"{cat.metrics[m0].label}: {plan.time.label} vs {plan.compare_to.label}",
                         series=[{"key": f"{m0}_previous", "label": plan.compare_to.label, "format": f0},
                                 {"key": m0, "label": plan.time.label, "format": f0}])
    if grain:
        other = [d for d in plan.dimensions if d != grain]
        series = [{"key": m, "label": cat.metrics[m].label, "format": cat.metrics[m].format} for m in plan.metrics[:2]]
        spec = ChartSpec(type="line", x=grain, series=series, title=f"{cat.metrics[m0].label} by {grain}")
        if other:
            spec.series = [{"key": m0, "label": cat.metrics[m0].label, "format": f0, "pivot": other[0]}]
        return spec
    if len(table.rows) > 40:
        return ChartSpec(type="table", title=cat.metrics[m0].label)
    return ChartSpec(type="bar", x=plan.dimensions[0], title=f"{cat.metrics[m0].label} by {cat.dimensions[plan.dimensions[0]].label.lower()}",
                     series=[{"key": m0, "label": cat.metrics[m0].label, "format": f0}])


def sql_result_table(columns: list[str], rows: list[list], truncated: bool) -> TableResult:
    metas = []
    for i, c in enumerate(columns):
        sample = next((r[i] for r in rows if r[i] is not None), None)
        kind = "metric" if isinstance(sample, (int, float)) and not c.endswith("_id") else "dimension"
        metas.append(ColumnMeta(key=c, label=c.replace("_", " ").title(), kind=kind,
                                format="number" if kind == "metric" else "text"))
    return TableResult(columns=metas, rows=rows, row_count=len(rows), truncated=truncated)


def sql_narrative(table: TableResult) -> str:
    if not table.rows:
        return "The query returned no rows."
    if len(table.rows) == 1 and len(table.columns) <= 4:
        return "Result: " + ", ".join(f"{c.label} = {fmt(v, c.format) if c.kind == 'metric' else v}" for c, v in zip(table.columns, table.rows[0])) + "."
    return f"The query returned {table.row_count} row{'s' if table.row_count != 1 else ''}" + (" (truncated at the row cap)" if table.truncated else "") + ". See the table for details."


NUM_RX = re.compile(r"[-+−]?\$?\d[\d,]*(?:\.\d+)?\s*(?:[KMB%]|pp)?")


def numbers_grounded(candidate: str, reference: str) -> bool:
    """True if every number in `candidate` also appears in `reference` (used to vet LLM rephrasing)."""
    norm = lambda s: {re.sub(r"[\s,]", "", x).replace("−", "-").lstrip("+") for x in NUM_RX.findall(s)}  # noqa: E731
    return norm(candidate) <= norm(reference)
