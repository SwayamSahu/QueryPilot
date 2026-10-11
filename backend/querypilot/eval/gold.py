"""Independent gold-SQL builder for the benchmark.

Deliberately shares NO code with the semantic compiler or time parser:
  * dates are hand-written literals (reference date 2026-10-10), inclusive BETWEEN ranges
  * comparisons use conditional aggregation (CASE WHEN ... ) instead of CTE + FULL JOIN
  * joins use different aliases/order and explicit tenant (and region) filters, because
    gold queries run as the table owner, which bypasses row-level security
It shares only the *business definitions* (that is the point of a governed metric layer).
"""
from __future__ import annotations

REFERENCE_DATE = "2026-10-10"

# label -> (first_day, last_day) inclusive. Hand-computed for reference date 2026-10-10.
PERIODS = {
    "Q3 2026": ("2026-07-01", "2026-09-30"), "Q2 2026": ("2026-04-01", "2026-06-30"),
    "Q1 2026": ("2026-01-01", "2026-03-31"), "Q4 2025": ("2025-10-01", "2025-12-31"),
    "Q3 2025": ("2025-07-01", "2025-09-30"), "Q2 2025": ("2025-04-01", "2025-06-30"),
    "Q1 2025": ("2025-01-01", "2025-03-31"), "Q4 2024": ("2024-10-01", "2024-12-31"),
    "2026YTD": ("2026-01-01", "2026-10-10"), "2025": ("2025-01-01", "2025-12-31"), "2024": ("2024-01-01", "2024-12-31"),
    "Sep 2026": ("2026-09-01", "2026-09-30"), "Aug 2026": ("2026-08-01", "2026-08-31"),
    "Mar 2026": ("2026-03-01", "2026-03-31"), "Feb 2026": ("2026-02-01", "2026-02-28"),
    "Mar 2025": ("2025-03-01", "2025-03-31"), "Dec 2025": ("2025-12-01", "2025-12-31"),
    "Nov 2025": ("2025-11-01", "2025-11-30"), "Dec 2024": ("2024-12-01", "2024-12-31"),
    "H1 2026": ("2026-01-01", "2026-06-30"), "H2 2025": ("2025-07-01", "2025-12-31"),
    "H1 2025": ("2025-01-01", "2025-06-30"), "H2 2024": ("2024-07-01", "2024-12-31"),
    "L6M": ("2026-04-01", "2026-09-30"), "L12M": ("2025-10-01", "2026-09-30"),
    "ALL": ("2024-01-01", "2026-09-30"),
}
PREVIOUS = {"Q3 2026": "Q2 2026", "Q2 2026": "Q1 2026", "Q1 2026": "Q4 2025", "Q4 2025": "Q3 2025", "Q3 2025": "Q2 2025",
            "Q2 2025": "Q1 2025", "2025": "2024", "Sep 2026": "Aug 2026", "Mar 2026": "Feb 2026", "H1 2026": "H2 2025",
            "Dec 2025": "Nov 2025"}
YOY = {"Q3 2026": "Q3 2025", "Q2 2026": "Q2 2025", "Q1 2026": "Q1 2025", "Q4 2025": "Q4 2024", "2025": "2024",
       "Mar 2026": "Mar 2025", "Dec 2025": "Dec 2024", "H1 2026": "H1 2025"}

# per-line expressions; metrics are built from SUMs of these
LINE = {
    "rev": "i.quantity * i.unit_price * (1 - i.discount)",
    "gs": "i.quantity * i.unit_price",
    "disc": "i.quantity * i.unit_price * i.discount",
    "cogs": "i.quantity * i.unit_cost",
    "qty": "i.quantity",
    "refund": "rt.refund_amount",
}
DIM = {"region": "g.region_name", "category": "p.category", "channel": "o.channel", "segment": "c.segment",
       "product": "p.product_name", "country": "g.country",
       "month": "to_char(o.order_date, 'YYYY-MM')", "quarter": "extract(year from o.order_date)::int || '-Q' || extract(quarter from o.order_date)::int",
       "year": "extract(year from o.order_date)::int"}
METRIC_FORMAT = {"revenue": "currency", "gross_sales": "currency", "discount_amount": "currency", "discount_rate": "percent",
                 "cogs": "currency", "gross_profit": "currency", "gross_margin": "percent", "orders": "number",
                 "units": "number", "aov": "currency", "customers": "number", "refunds": "currency", "return_rate": "percent"}


def _sum(key: str, cond: str | None) -> str:
    e = LINE[key]
    if key == "refund":
        return f"coalesce(sum(CASE WHEN {cond} THEN {e} END), 0)" if cond else f"coalesce(sum({e}), 0)"
    return f"sum(CASE WHEN {cond} THEN {e} END)" if cond else f"sum({e})"


def metric(m: str, cond: str | None = None) -> str:
    rev, cogs = _sum("rev", cond), _sum("cogs", cond)
    distinct = (lambda col: f"count(DISTINCT CASE WHEN {cond} THEN {col} END)") if cond else (lambda col: f"count(DISTINCT {col})")
    return {
        "revenue": rev,
        "gross_sales": _sum("gs", cond),
        "discount_amount": _sum("disc", cond),
        "discount_rate": f"{_sum('disc', cond)} / nullif({_sum('gs', cond)}, 0)",
        "cogs": cogs,
        "gross_profit": f"{rev} - {cogs}",
        "gross_margin": f"1 - {cogs} / nullif({rev}, 0)",
        "orders": distinct("o.order_id"),
        "units": _sum("qty", cond),
        "aov": f"{rev} / nullif({distinct('o.order_id')}, 0)",
        "customers": distinct("o.customer_id"),
        "refunds": _sum("refund", cond),
        "return_rate": f"{_sum('refund', cond)} / nullif({rev}, 0)",
    }[m]


def _from(needs: set[str]) -> str:
    sql = "sales.orders o JOIN sales.order_items i ON i.order_id = o.order_id"
    if "g" in needs:
        sql += " JOIN sales.regions g ON g.region_id = o.region_id"
    if "p" in needs:
        sql += " JOIN sales.products p ON p.product_id = i.product_id"
    if "c" in needs:
        sql += " JOIN sales.customers c ON c.customer_id = o.customer_id"
    if "rt" in needs:
        sql += " LEFT JOIN sales.returns rt ON rt.order_item_id = i.order_item_id"
    return sql


def _needs(metrics: list[str], dims: list[str], filters: dict[str, list[str]]) -> set[str]:
    n: set[str] = set()
    for d in list(dims) + list(filters):
        n |= {"region": {"g"}, "country": {"g"}, "category": {"p"}, "product": {"p"}, "segment": {"c"}}.get(d, set())
    if any(m in ("refunds", "return_rate") for m in metrics):
        n.add("rt")
    return n


def _filters(tenant: int, filters: dict[str, list[str]], excl: dict[str, list[str]], regions: list[str] | None) -> list[str]:
    w = [f"o.tenant_id = {tenant}", "o.status <> 'cancelled'"]
    for d, vals in filters.items():
        w.append(f"{DIM[d]} IN (" + ", ".join("'" + v.replace("'", "''") + "'" for v in vals) + ")")
    for d, vals in excl.items():
        w.append(f"{DIM[d]} NOT IN (" + ", ".join("'" + v.replace("'", "''") + "'" for v in vals) + ")")
    if regions:  # regional-manager scope (what RLS enforces for the app)
        w.append("o.region_id IN (SELECT region_id FROM sales.regions WHERE region_name IN ("
                 + ", ".join(f"'{r}'" for r in regions) + f") AND tenant_id = {tenant})")
    return w


def aggregate(metrics: list[str], period: str, dims: list[str] | None = None, filters: dict | None = None,
              excl: dict | None = None, order: str | None = None, desc: bool = True, limit: int | None = None,
              tenant: int = 1, regions: list[str] | None = None) -> str:
    dims, filters, excl = dims or [], filters or {}, excl or {}
    needs = _needs(metrics, dims, {**filters, **excl})
    if regions:
        needs.add("g")
    a, b = PERIODS[period]
    w = _filters(tenant, filters, excl, regions) + [f"o.order_date BETWEEN DATE '{a}' AND DATE '{b}'"]
    sel = [f"{DIM[d]} AS {d}" for d in dims] + [f"{metric(m)} AS {m}" for m in metrics]
    sql = f"SELECT {', '.join(sel)} FROM {_from(needs)} WHERE {' AND '.join(w)}"
    if dims:
        sql += " GROUP BY " + ", ".join(DIM[d] for d in dims)
    if order:
        sql += f" ORDER BY {order} {'DESC' if desc else 'ASC'}"
    if limit:
        sql += f" LIMIT {limit}"
    return sql


def compare(metric_key: str, cur: str, prev: str, dims: list[str] | None = None, filters: dict | str | None = None,
            direction: str | None = None, order_change: str | None = None, limit: int | None = None,
            tenant: int = 1, regions: list[str] | None = None) -> str:
    """Current vs previous period via conditional aggregation. Columns: dims, cur, prev, change."""
    if isinstance(filters, str):  # allow compare(m, cur, prev, dims, "declining")
        filters, direction = None, filters
    dims, filters = dims or [], filters or {}
    needs = _needs([metric_key], dims, filters)
    if regions:
        needs.add("g")
    (ca, cb), (pa, pb) = PERIODS[cur], PERIODS[prev]
    cc = f"o.order_date BETWEEN DATE '{ca}' AND DATE '{cb}'"
    pc = f"o.order_date BETWEEN DATE '{pa}' AND DATE '{pb}'"
    w = _filters(tenant, filters, {}, regions) + [f"({cc} OR {pc})"]
    inner = [f"{DIM[d]} AS {d}" for d in dims] + [f"{metric(metric_key, cc)} AS cur", f"{metric(metric_key, pc)} AS prev"]
    sql = f"SELECT {', '.join(inner)} FROM {_from(needs)} WHERE {' AND '.join(w)}"
    if dims:
        sql += " GROUP BY " + ", ".join(DIM[d] for d in dims)
    outer = f"SELECT {', '.join(dims + ['cur', 'prev'])}, cur - prev AS change FROM ({sql}) t"
    if direction == "declining":
        outer += " WHERE cur < prev"
    elif direction == "increasing":
        outer += " WHERE cur > prev"
    if order_change:
        outer += f" ORDER BY change {order_change}"
    if limit:
        outer += f" LIMIT {limit}"
    return outer
