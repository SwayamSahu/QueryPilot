"""Compiles a governed QueryPlan into PostgreSQL.

The compiler is deterministic: the same plan + catalog version always yields the same SQL.
Metric formulas come only from the catalog, so "gross margin" means one thing everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp

from querypilot.core.models import ColumnMeta, QueryPlan
from querypilot.semantic.catalog import Catalog


class PlanError(ValueError):
    """The plan references something the semantic layer does not define."""


@dataclass
class CompiledQuery:
    sql: str
    columns: list[ColumnMeta]
    plan: QueryPlan


def _lit(v: str) -> str:
    return exp.Literal.string(v).sql(dialect="postgres")


def _fmt(cat: Catalog, metric: str) -> str:
    return cat.metrics[metric].format


def validate_plan(plan: QueryPlan, cat: Catalog) -> None:
    if not plan.metrics:
        raise PlanError("plan has no metrics")
    for m in plan.metrics:
        if m not in cat.metrics:
            raise PlanError(f"unknown metric '{m}'. Known: {', '.join(cat.metrics)}")
    for d in plan.dimensions:
        if d not in cat.dimensions:
            raise PlanError(f"unknown dimension '{d}'. Known: {', '.join(cat.dimensions)}")
    for f in plan.filters:
        if f.dimension not in cat.dimensions:
            raise PlanError(f"unknown filter dimension '{f.dimension}'")
        if not f.values:
            raise PlanError(f"filter on {f.dimension} has no values")
    if plan.time is None:
        raise PlanError("plan has no resolved time range")
    if plan.limit is not None and not (1 <= plan.limit <= 10_000):
        raise PlanError("limit out of range")
    if len(set(plan.dimensions)) != len(plan.dimensions):
        raise PlanError("duplicate dimensions")


def _from_clause(cat: Catalog, aliases: set[str]) -> str:
    parts = [f"{cat.fact_table} {cat.fact_alias}"]
    for alias, j in cat.joins.items():
        if j.required or alias in aliases:
            kw = "LEFT JOIN" if j.type == "left" else "JOIN"
            parts.append(f"{kw} {j.table} {alias} ON {j.on}")
    return "\n  ".join(parts)


def _where(cat: Catalog, plan: QueryPlan, start, end) -> str:
    conds = list(cat.base_filters)
    conds.append(f"{cat.time_column} >= DATE '{start.isoformat()}'")
    conds.append(f"{cat.time_column} < DATE '{end.isoformat()}'")
    for f in plan.filters:
        col = cat.dimensions[f.dimension].sql
        vals = ", ".join(_lit(v) for v in f.values)
        conds.append(f"{col} {'NOT IN' if f.op == 'not_in' else 'IN'} ({vals})")
    return "\n  AND ".join(conds)


def _aggregate_select(cat: Catalog, plan: QueryPlan, start, end) -> str:
    aliases: set[str] = set()
    for m in plan.metrics:
        aliases |= cat.metric_requires(m)
    for d in plan.dimensions:
        aliases |= set(cat.dimensions[d].requires)
    for f in plan.filters:
        aliases |= set(cat.dimensions[f.dimension].requires)
    sel = [f"{cat.dimensions[d].sql} AS {d}" for d in plan.dimensions]
    sel += [f"{cat.expand(m)} AS {m}" for m in plan.metrics]
    sql = "SELECT " + ",\n       ".join(sel)
    sql += "\nFROM " + _from_clause(cat, aliases)
    sql += "\nWHERE " + _where(cat, plan, start, end)
    if plan.dimensions:
        sql += "\nGROUP BY " + ", ".join(str(i + 1) for i in range(len(plan.dimensions)))
    return sql


def _indent(sql: str, n: int = 2) -> str:
    return "\n".join(" " * n + line for line in sql.splitlines())


def compile_plan(plan: QueryPlan, cat: Catalog) -> CompiledQuery:
    validate_plan(plan, cat)
    dims, mets = plan.dimensions, plan.metrics
    cols = [ColumnMeta(key=d, label=cat.dimensions[d].label, kind="dimension", format="text") for d in dims]

    if plan.comparison == "none":
        sql = _aggregate_select(cat, plan, plan.time.start, plan.time.end)
        cols += [ColumnMeta(key=m, label=cat.metrics[m].label, kind="metric", format=_fmt(cat, m)) for m in mets]
        order = plan.order_by
        if order is None:
            grain = next((d for d in dims if cat.dimensions[d].time_grain), None)
            order, direction = (grain, "ASC") if grain else ((mets[0], "DESC") if dims else (None, None))
        else:
            direction = plan.order_dir.upper()
        if order and dims:
            if order not in dims and order not in mets:
                raise PlanError(f"cannot order by '{order}'")
            sql += f"\nORDER BY {order} {direction} NULLS LAST"
            if order not in dims:  # deterministic tie-break
                sql += ", " + ", ".join(f"{d} ASC" for d in dims)
        if plan.limit and dims:
            sql += f"\nLIMIT {plan.limit}"
        return CompiledQuery(sql=sql, columns=cols, plan=plan)

    # ---- period comparison: current vs previous period, joined on the dimensions ----
    previous_time = plan.compare_to
    if previous_time is None:
        raise PlanError("comparison requested without a comparison period")
    cur_sql = _aggregate_select(cat, plan, plan.time.start, plan.time.end)
    prev_sql = _aggregate_select(cat, plan, previous_time.start, previous_time.end)
    select = [f"COALESCE(cur.{d}, prev.{d}) AS {d}" for d in dims]
    for m in mets:
        f = _fmt(cat, m)
        select.append(f"cur.{m} AS {m}")
        select.append(f"prev.{m} AS {m}_previous")
        select.append(f"cur.{m} - prev.{m} AS {m}_change")
        cols += [
            ColumnMeta(key=m, label=f"{cat.metrics[m].label} ({plan.time.label})", kind="metric", format=f),
            ColumnMeta(key=f"{m}_previous", label=f"{cat.metrics[m].label} ({previous_time.label})", kind="previous", format=f),
            ColumnMeta(key=f"{m}_change", label="Change" + (" (pp)" if f == "percent" else ""), kind="change",
                       format="pp" if f == "percent" else f),
        ]
        if f != "percent":
            select.append(f"(cur.{m} - prev.{m}) / NULLIF(ABS(prev.{m}), 0) AS {m}_change_pct")
            cols.append(ColumnMeta(key=f"{m}_change_pct", label="Change %", kind="change_pct", format="percent"))
    if dims:
        # null-safe but still hash/merge-joinable (IS NOT DISTINCT FROM is not, for FULL JOIN)
        on = " AND ".join(f"COALESCE(CAST(cur.{d} AS text), '<null>') = COALESCE(CAST(prev.{d} AS text), '<null>')" for d in dims)
        join = f"cur FULL OUTER JOIN prev ON {on}"
    else:
        join = "cur CROSS JOIN prev"
    sql = f"WITH cur AS (\n{_indent(cur_sql)}\n), prev AS (\n{_indent(prev_sql)}\n)\n"
    sql += "SELECT " + ",\n       ".join(select) + f"\nFROM {join}"
    key = mets[0]
    if plan.change_filter == "declining":
        sql += f"\nWHERE cur.{key} < prev.{key}"
    elif plan.change_filter == "increasing":
        sql += f"\nWHERE cur.{key} > prev.{key}"
    if dims:
        order = plan.order_by
        if order is None:
            order, direction = f"{key}_change", ("ASC" if plan.change_filter == "declining" else "DESC")
        else:
            direction = plan.order_dir.upper()
        valid = set(dims) | set(mets) | {f"{m}_change" for m in mets} | {f"{m}_previous" for m in mets} | {
            f"{m}_change_pct" for m in mets if _fmt(cat, m) != "percent"}
        if order not in valid:
            raise PlanError(f"cannot order by '{order}'")
        sql += f"\nORDER BY {order} {direction} NULLS LAST, " + ", ".join(f"{d} ASC" for d in dims)
        if plan.limit:
            sql += f"\nLIMIT {plan.limit}"
    return CompiledQuery(sql=sql, columns=cols, plan=plan)
