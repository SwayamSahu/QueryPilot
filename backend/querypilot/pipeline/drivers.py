"""'What changed?' - decomposes a period-over-period change into business drivers.

Gross margin  m = 1 - CR / (1 - DR)  with  CR = COGS / gross sales,  DR = discount / gross sales.
  * discount effect = m(CR_prev, DR_cur) - m(CR_prev, DR_prev)
  * cost effect     = m(CR_cur,  DR_cur) - m(CR_prev, DR_cur)      (exactly sums to the change)
  * category mix    = sum_k (share_k,cur - share_k,prev) * margin_k,prev   (part of the cost/price effect)
Additive metrics (revenue, units, ...) are decomposed into per-category contributions.

All inputs come from additional governed queries that are shown as evidence.
"""
from __future__ import annotations

from collections.abc import Callable

from querypilot.core.models import Filter, QueryPlan, TableResult
from querypilot.pipeline.explain import fmt
from querypilot.semantic.catalog import Catalog

RunPlan = Callable[[QueryPlan], tuple[TableResult, str]]
MARGIN_METRICS = {"gross_margin", "gross_profit"}


def _rows(t: TableResult) -> list[dict]:
    keys = [c.key for c in t.columns]
    return [dict(zip(keys, r)) for r in t.rows]


def _pp(x: float) -> str:
    return f"{x * 100:+.1f} pp"


def _margin(cr: float, dr: float) -> float:
    return 1 - cr / (1 - dr) if dr < 1 else float("nan")


def analyse(plan: QueryPlan, result: TableResult, cat: Catalog, run: RunPlan) -> tuple[list[dict], list[str]]:
    if not plan.drivers or plan.comparison == "none" or not result.rows:
        return [], []
    dims = [d for d in plan.dimensions if not cat.dimensions[d].time_grain]
    if len(dims) > 1:
        return [], []
    dim = dims[0] if dims else None
    m0 = plan.metrics[0]
    rows = _rows(result)[:3]
    members = [r[dim] for r in rows] if dim else ["Total"]
    total_label = ", ".join(v for f in plan.filters if f.op == "in" for v in f.values) or "Total"
    breakdown = "category" if dim != "category" else "channel"
    base_filters = [f for f in plan.filters if f.dimension != dim] + ([Filter(dimension=dim, values=members)] if dim else [])

    def sub(metrics: list[str], extra_dim: str | None) -> QueryPlan:
        return plan.model_copy(update={
            "metrics": metrics, "dimensions": ([dim] if dim else []) + ([extra_dim] if extra_dim else []),
            "filters": base_filters, "change_filter": "none", "order_by": None, "limit": None, "drivers": False,
            "assumptions": []})

    sqls: list[str] = []
    out: list[dict] = []
    if m0 in MARGIN_METRICS:
        ta, sa = run(sub(["gross_sales", "discount_amount", "cogs", "revenue"], None))
        tb, sb = run(sub(["revenue", "cogs"], breakdown))
        sqls += [sa, sb]
        a = {(r[dim] if dim else "Total"): r for r in _rows(ta)}
        b = _rows(tb)
        for mem in members:
            r = a.get(mem)
            if not r or not r["gross_sales_previous"] or not r["gross_sales"]:
                continue
            gs_c, gs_p = r["gross_sales"], r["gross_sales_previous"]
            dr_c, dr_p = r["discount_amount"] / gs_c, r["discount_amount_previous"] / gs_p
            cr_c, cr_p = r["cogs"] / gs_c, r["cogs_previous"] / gs_p
            m_c, m_p = _margin(cr_c, dr_c), _margin(cr_p, dr_p)
            disc_eff = _margin(cr_p, dr_c) - _margin(cr_p, dr_p)
            cost_eff = m_c - _margin(cr_p, dr_c)
            # category mix vs within-category rate
            cats = [x for x in b if (x.get(dim) if dim else "Total") == mem] if dim else b
            rev_c = sum(x["revenue"] or 0 for x in cats)
            rev_p = sum(x["revenue_previous"] or 0 for x in cats)
            mix = 0.0
            worst = None
            for x in cats:
                if not x["revenue_previous"] or not x["revenue"]:
                    continue
                s_c, s_p = x["revenue"] / rev_c, x["revenue_previous"] / rev_p
                mk_c, mk_p = 1 - x["cogs"] / x["revenue"], 1 - x["cogs_previous"] / x["revenue_previous"]
                mix += (s_c - s_p) * mk_p
                contrib = s_c * (mk_c - mk_p)
                if worst is None or contrib < worst[1]:
                    worst = (x[breakdown], contrib, mk_p, mk_c, s_p, s_c)
            effects = {"discounting": disc_eff, "unit costs vs. price": cost_eff - mix, "category mix": mix}
            main = min(effects, key=effects.get) if m_c < m_p else max(effects, key=effects.get)
            name = mem if dim else total_label
            stmt = (f"{name}: gross margin {fmt(m_p, 'percent')} → {fmt(m_c, 'percent')} ({_pp(m_c - m_p)}), driven mainly by "
                    f"{main}. Discount rate {fmt(dr_p, 'percent')} → {fmt(dr_c, 'percent')} ({_pp(disc_eff)} on margin); "
                    f"COGS as share of gross sales {fmt(cr_p, 'percent')} → {fmt(cr_c, 'percent')} ({_pp(cost_eff)}, of which "
                    f"{_pp(mix)} from {breakdown} mix).")
            if worst and worst[1] < -0.003:
                stmt += f" Largest hit: {worst[0]} ({fmt(worst[2], 'percent')} → {fmt(worst[3], 'percent')})."
            out.append({"member": name, "metric": m0, "change": m_c - m_p, "main_driver": main, "statement": stmt,
                        "components": {"discount_effect": disc_eff, "cost_effect": cost_eff - mix, "mix_effect": mix},
                        "discount_rate": [dr_p, dr_c], "cogs_ratio": [cr_p, cr_c],
                        "top_breakdown": worst[0] if worst else None})
    elif cat.metrics[m0].additive:
        tb, sb = run(sub([m0], breakdown))
        sqls.append(sb)
        f0 = cat.metrics[m0].format
        for mem in members:
            parts = [x for x in _rows(tb) if (x.get(dim) if dim else "Total") == mem] if dim else _rows(tb)
            deltas = [(x[breakdown], (x[m0] or 0) - (x[f"{m0}_previous"] or 0)) for x in parts]
            total = sum(d for _, d in deltas)
            if not deltas:
                continue
            top = sorted(deltas, key=lambda d: d[1] if total < 0 else -d[1])[:2]
            name = mem if dim else total_label
            stmt = (f"{name}: {cat.metrics[m0].label.lower()} changed {'+' if total >= 0 else '−'}{fmt(abs(total), f0)}; biggest contributors "
                    + " and ".join(f"{k} ({'+' if v >= 0 else '−'}{fmt(abs(v), f0)})" for k, v in top) + ".")
            out.append({"member": name, "metric": m0, "change": total, "main_driver": top[0][0], "statement": stmt,
                        "components": {k: v for k, v in deltas}})
    return out, sqls
