"""LLM-backed planning: structured semantic plans first, raw SQL only as a governed fallback.

The model never invents metric formulas or does date arithmetic. It picks catalog keys and
writes a time *expression*; the deterministic builder resolves dates and the compiler writes
SQL. The raw-SQL route exists for questions the semantic layer can't express, and its output
goes through the same validator, RLS-scoped executor and repair loop.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from querypilot.core.models import Filter, QueryPlan
from querypilot.llm.client import LLM, LLMError, Usage
from querypilot.semantic import timeparse as tp
from querypilot.semantic.catalog import Catalog
from querypilot.semantic.plan_builder import Draft
from querypilot.semantic.retriever import Hit

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["semantic", "sql", "clarify"]},
        "metrics": {"type": "array", "items": {"type": "string"}},
        "dimensions": {"type": "array", "items": {"type": "string"}},
        "filters": {"type": "array", "items": {"type": "object", "properties": {
            "dimension": {"type": "string"}, "op": {"type": "string", "enum": ["in", "not_in"]},
            "values": {"type": "array", "items": {"type": "string"}}}, "required": ["dimension", "op", "values"]}},
        "time_expression": {"type": ["string", "null"]},
        "comparison": {"type": "string", "enum": ["none", "previous_period", "yoy", "custom"]},
        "compare_to_expression": {"type": ["string", "null"]},
        "change_filter": {"type": "string", "enum": ["none", "declining", "increasing"]},
        "order_by": {"type": ["string", "null"]},
        "order_dir": {"type": "string", "enum": ["asc", "desc"]},
        "limit": {"type": ["integer", "null"]},
        "drivers": {"type": "boolean"},
        "clarification": {"type": ["string", "null"]},
    },
    "required": ["route", "metrics", "dimensions", "filters", "time_expression", "comparison", "change_filter",
                 "order_dir", "drivers"],
}

SQL_SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}, "rationale": {"type": "string"}},
    "required": ["sql", "rationale"],
}

PLAN_SYSTEM = """You translate business questions into a JSON query plan over a governed semantic layer.
Rules:
- Use ONLY metric and dimension keys listed in CONTEXT. Never invent formulas.
- route="semantic" whenever the question can be answered with the listed metrics/dimensions.
- route="sql" only if it needs data the semantic layer cannot express (e.g. raw row listings, signup dates).
- route="clarify" if the question is ambiguous, about the future, or outside the available data; put a short
  question for the user in "clarification".
- time_expression: copy the user's period wording verbatim or normalise it ("last quarter", "Q2 2026",
  "2025", "last 6 months", "March 2026"). Never compute dates yourself. null if no period is mentioned.
- comparison: previous_period for "vs previous quarter/QoQ/change/growth"; yoy for "year over year / vs last year";
  custom when two explicit periods are compared (put the older one in compare_to_expression).
- change_filter="declining"/"increasing" only when the user asks WHICH items went down/up.
- drivers=true when the user asks why / what changed / what drove it.
- order_by is a metric key, "<metric>_change", or a dimension key; limit for top/bottom N (1 for "which X had the highest").
- Filter values must be exact values from CONTEXT's value lists.
- Follow-up questions modify PREVIOUS_PLAN: keep everything the user did not change."""

SQL_SYSTEM = """You write a single read-only PostgreSQL SELECT statement.
Rules:
- Only the tables and columns in CONTEXT. Always schema-qualify (sales.orders, ...).
- Never filter on tenant_id; tenant and region scoping are applied automatically.
- Never select email, phone or salary. No SELECT *. No DML/DDL. No system catalogs.
- Exclude cancelled orders (orders.status <> 'cancelled') when computing money or counts.
- Revenue = SUM(quantity * unit_price * (1 - discount)) from sales.order_items.
- Prefer aggregates; add ORDER BY and LIMIT 100 for listings.
Return JSON: {"sql": "...", "rationale": "one sentence"}"""


@dataclass
class LLMPlanResult:
    route: str
    draft: Draft | None = None
    sql: str | None = None
    clarification: str | None = None
    raw: dict | None = None


def _context(cat: Catalog, hits: list[Hit], values: dict[str, list[str]]) -> str:
    metric_keys = [h.doc.doc_id.split(":", 1)[1] for h in hits if h.doc.kind == "metric"]
    dim_keys = [h.doc.doc_id.split(":", 1)[1] for h in hits if h.doc.kind == "dimension"]
    # Always expose the full (short) key lists; describe the retrieved ones in detail.
    lines = ["METRICS (key: meaning):"]
    for k, m in cat.metrics.items():
        lines.append(f"- {k}: {m.label}" + (f" - {m.description}" if k in metric_keys else ""))
    lines.append("DIMENSIONS (key: meaning [values]):")
    for k, d in cat.dimensions.items():
        vals = values.get(k)
        detail = f" values={vals[:25]}" if vals and (k in dim_keys or len(vals) <= 8) else ""
        lines.append(f"- {k}: {d.label}{detail}")
    return "\n".join(lines)


def _schema_context(cat: Catalog, hits: list[Hit]) -> str:
    tables = []
    for h in hits:
        if h.doc.kind in ("table", "column"):
            t = ".".join(h.doc.doc_id.split(":", 1)[1].split(".")[:2])
            if t not in tables:
                tables.append(t)
    for core in ("sales.orders", "sales.order_items"):
        if core not in tables:
            tables.append(core)
    out = []
    for t in tables:
        info = cat.tables.get(t, {})
        cols = "\n".join(f"    {c}: {d}" for c, d in info.get("columns", {}).items())
        out.append(f"{t} -- {info.get('description', '')}\n{cols}")
    return "\n".join(out)


def llm_plan(llm: LLM, question: str, cat: Catalog, hits: list[Hit], values: dict[str, list[str]], today: date,
             bounds: tuple[date, date], previous: QueryPlan | None, usage: Usage, feedback: str | None = None) -> LLMPlanResult:
    prev = previous.model_dump(mode="json", exclude={"assumptions", "time", "compare_to"}) if previous else None
    if previous is not None:
        prev["period"] = previous.time.label if previous.time else None
    user = (f"TODAY: {today.isoformat()} (data available {bounds[0]} to {bounds[1]})\n\nCONTEXT:\n{_context(cat, hits, values)}\n\n"
            f"PREVIOUS_PLAN: {json.dumps(prev) if prev else 'none'}\n\nQUESTION: {question}")
    if feedback:
        user += f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED: {feedback}\nReturn a corrected plan."
    raw = llm.complete_json(PLAN_SYSTEM, user, PLAN_SCHEMA, usage, "plan")
    route = raw.get("route", "semantic")
    if route == "clarify":
        return LLMPlanResult(route="clarify", clarification=raw.get("clarification") or "Could you rephrase the question?", raw=raw)
    if route == "sql":
        return LLMPlanResult(route="sql", raw=raw)
    d = Draft(metrics=list(raw.get("metrics") or []), dimensions=list(raw.get("dimensions") or []))
    lower = {k: {v.lower(): v for v in vs} for k, vs in values.items()}
    for f in raw.get("filters") or []:
        dim = f.get("dimension")
        canon = []
        for v in f.get("values") or []:
            c = lower.get(dim, {}).get(str(v).lower())
            if c is None and dim in lower:
                raise LLMError(f"filter value '{v}' is not a known {dim}; valid: {list(lower[dim].values())}")
            canon.append(c or str(v))
        d.filters.append(Filter(dimension=dim, op=f.get("op", "in"), values=canon))
    te = raw.get("time_expression")
    if te:
        m = tp.resolve(te, today)
        if m is None:
            raise LLMError(f"time_expression '{te}' could not be resolved; use forms like 'last quarter', 'Q2 2026', '2025'")
        d.time = m.spec
        if m.assumption:
            d.assumptions.append(m.assumption)
    d.comparison = raw.get("comparison") or "none"
    ce = raw.get("compare_to_expression")
    if d.comparison == "custom" and ce:
        m = tp.resolve(ce, today)
        if m:
            d.compare_to = m.spec
    d.change_filter = raw.get("change_filter") or "none"
    d.order_by = raw.get("order_by")
    d.order_dir = raw.get("order_dir") or "desc"
    d.limit = raw.get("limit")
    d.drivers = bool(raw.get("drivers"))
    return LLMPlanResult(route="semantic", draft=d, raw=raw)


def llm_sql(llm: LLM, question: str, cat: Catalog, hits: list[Hit], today: date, usage: Usage,
            feedback: str | None = None, previous_sql: str | None = None) -> str:
    user = f"TODAY: {today.isoformat()}\n\nCONTEXT (PostgreSQL tables):\n{_schema_context(cat, hits)}\n\nQUESTION: {question}"
    if feedback:
        user += f"\n\nPREVIOUS SQL:\n{previous_sql}\n\nIT FAILED WITH: {feedback}\nFix it and return the corrected SQL."
    raw = llm.complete_json(SQL_SYSTEM, user, SQL_SCHEMA, usage, "sql" if not feedback else "sql_repair")
    sql = (raw.get("sql") or "").strip().rstrip(";")
    if not sql:
        raise LLMError("model returned empty SQL")
    return sql
