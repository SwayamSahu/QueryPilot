"""LangGraph orchestration of the QueryPilot pipeline.

    guard -> resolve -> retrieve -> generate -> validate --valid--> execute -> check -> explain -> END
                 |                     ^            |                  |
                 |                     +-- repair <-+--invalid---------+ (bounded by QP_MAX_REPAIRS)
                 +--> END (clarify / blocked)

Each node is wrapped in a tracing span; the state carries a per-request context object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from querypilot.config import Settings
from querypilot.core.models import Check, Principal, QueryPlan, TableResult
from querypilot.llm.client import LLM, LLMError, Usage
from querypilot.llm.planner import llm_plan, llm_sql
from querypilot.ops import metrics as prom
from querypilot.ops.tracing import Tracer
from querypilot.pipeline import drivers as drv
from querypilot.pipeline.checks import check_result
from querypilot.pipeline.executor import ExecResult, ExecutionError, Executor, TTLCache
from querypilot.pipeline.explain import chart_for, narrative, numbers_grounded, sql_narrative, sql_result_table
from querypilot.safety.policy import Policy, authorize_plan, guard_question
from querypilot.safety.validator import SQLRejected, ValidatedSQL, validate_sql
from querypilot.semantic.catalog import Catalog
from querypilot.semantic.compiler import CompiledQuery, PlanError, compile_plan
from querypilot.semantic.plan_builder import assemble
from querypilot.semantic.retriever import retriever_for
from querypilot.semantic.rules_planner import RuleParse, RulePlanner

HYBRID_RULE_THRESHOLD = 0.8  # rule parses at/above this coverage skip the LLM entirely


@dataclass
class Services:
    settings: Settings
    catalog: Catalog
    policy: Policy
    executor: Executor
    rules: RulePlanner
    retriever: Any
    llm: LLM | None
    plan_cache: TTLCache


@dataclass
class RequestCtx:
    principal: Principal
    tracer: Tracer
    usage: Usage = field(default_factory=Usage)
    supporting_sql: list[str] = field(default_factory=list)


class QPState(TypedDict, total=False):
    ctx: RequestCtx
    question: str
    previous_plan: QueryPlan | None
    status: str  # running | answered | clarify | blocked | error
    route: str  # rules | llm | llm_sql | user_sql
    intent: str
    rule: RuleParse
    confidence: float
    hits: list
    plan: QueryPlan | None
    compiled: CompiledQuery | None
    sql: str | None
    validated: ValidatedSQL | None
    exec: ExecResult | None
    attempts: int
    feedback: str | None
    message: str
    blocked_reason: str | None
    blocked_category: str | None
    suggestions: list[str]
    checks: list[Check]
    table: TableResult | None
    answer: str
    kpis: list
    chart: Any
    drivers: list
    assumptions: list[str]
    _needs_repair: bool


SUGGESTIONS = [
    "Which regions had declining gross margins last quarter, and what changed?",
    "Revenue by product category in 2025",
    "Monthly revenue trend for the last 12 months",
    "Top 5 products by gross profit last quarter",
    "Average order value by channel, year over year",
]


def build_graph(svc: Services):
    s, cat = svc.settings, svc.catalog

    # ------------------------------------------------------------------ nodes
    def guard(st: QPState) -> QPState:
        ctx = st["ctx"]
        with ctx.tracer.span("intent_guard") as sp:
            g = guard_question(st["question"], ctx.principal, svc.executor.tenant_names())
            sp["allowed"] = g.allowed
            if not g.allowed:
                sp["category"] = g.category
                prom.BLOCKED.labels(category=g.category).inc()
                return {"status": "blocked", "blocked_reason": g.reason, "blocked_category": g.category, "message": g.reason,
                        "intent": "blocked"}
        return {"status": "running"}

    def resolve(st: QPState) -> QPState:
        ctx = st["ctx"]
        with ctx.tracer.span("intent_resolver") as sp:
            values = svc.executor.dimension_values(ctx.principal.tenant_id, cat)
            rp = svc.rules.parse(st["question"], s.today(), values, st.get("previous_plan"))
            sp.update(route=rp.route, coverage=rp.coverage, confidence=rp.confidence, followup=rp.is_followup,
                      unknown_terms=rp.unknown_terms)
            out: QPState = {"rule": rp, "confidence": rp.confidence, "intent": "followup" if rp.is_followup else rp.route,
                            "attempts": 0, "feedback": None, "assumptions": []}
            if rp.route == "sql":
                out.update(route="user_sql", sql=st["question"].strip().rstrip(";"), intent="user_sql")
                return out
            if rp.route == "unsupported":
                msg = (f"I can only answer questions about historical data that exists in the warehouse; "
                       f"'{rp.reason}' isn't something I can compute.")
                if rp.reason.startswith(("forecast", "predict", "project", "will", "going", "next")):
                    msg = ("Forecasting isn't supported - QueryPilot only reports on recorded data. I can show the historical "
                           "trend instead, e.g. 'monthly revenue trend for the last 12 months'.")
                out.update(status="clarify", message=msg, suggestions=SUGGESTIONS[:3])
                return out
            use_rules = rp.route == "semantic" and (
                s.planner == "rules" or svc.llm is None or (s.planner == "hybrid" and rp.confidence >= HYBRID_RULE_THRESHOLD))
            if use_rules:
                if rp.confidence < s.min_confidence:
                    out.update(status="clarify", message=_clarify_msg(rp), suggestions=SUGGESTIONS[:3])
                    return out
                out["route"] = "rules"
                return out
            if svc.llm is None:
                out.update(status="clarify", message=_clarify_msg(rp), suggestions=SUGGESTIONS[:4])
                return out
            out["route"] = "llm"
            sp["handoff"] = "llm"
            return out

    def retrieve(st: QPState) -> QPState:
        ctx = st["ctx"]
        with ctx.tracer.span("retrieval", backend=getattr(svc.retriever, "name", "?")) as sp:
            q = st["question"]
            prev = st.get("previous_plan")
            if prev:
                q += " " + " ".join(prev.metrics + prev.dimensions)
            values = svc.executor.dimension_values(ctx.principal.tenant_id, cat)
            hits = retriever_for(ctx.principal.tenant_id, values).search(q, k=s.retrieval_k)
            sp["hits"] = [f"{h.doc.doc_id}:{h.score:.2f}" for h in hits]
        return {"hits": hits}

    def generate(st: QPState) -> QPState:
        ctx = st["ctx"]
        route = st["route"]
        with ctx.tracer.span("generate", route=route, attempt=st.get("attempts", 0)) as sp:
            today = s.today()
            bounds = svc.executor.data_bounds(ctx.principal.tenant_id)
            try:
                if route == "user_sql":
                    return {"sql": st["sql"]}
                if route == "rules":
                    plan = assemble(st["rule"].draft, cat, today, bounds)
                    return _compiled(plan, ctx, sp)
                if route == "llm":
                    values = svc.executor.dimension_values(ctx.principal.tenant_id, cat)
                    key = f"{ctx.principal.tenant_id}|{cat.fingerprint}|{today}|{st['question'].lower().strip()}|" + (
                        st["previous_plan"].signature() if st.get("previous_plan") else "")
                    res = svc.plan_cache.get(key) if not st.get("feedback") else None
                    if res is None:
                        res = llm_plan(svc.llm, st["question"], cat, st.get("hits", []), values, today, bounds,
                                       st.get("previous_plan"), ctx.usage, st.get("feedback"))
                        if res.route != "sql":
                            svc.plan_cache.put(key, res)
                    sp["llm_route"] = res.route
                    if res.route == "clarify":
                        return {"status": "clarify", "message": res.clarification, "suggestions": SUGGESTIONS[:3]}
                    if res.route == "sql":
                        sql = llm_sql(svc.llm, st["question"], cat, st.get("hits", []), today, ctx.usage)
                        sp["sql_route"] = True
                        return {"route": "llm_sql", "sql": sql, "plan": None, "compiled": None, "confidence": 0.6}
                    plan = assemble(res.draft, cat, today, bounds)
                    out = _compiled(plan, ctx, sp)
                    out.setdefault("confidence", 0.8)
                    return out
                if route == "llm_sql":
                    sql = llm_sql(svc.llm, st["question"], cat, st.get("hits", []), today, ctx.usage,
                                  st.get("feedback"), st.get("sql"))
                    return {"sql": sql}
            except (PlanError, LLMError, ValueError) as e:
                sp["_status"] = "error"
                sp["error"] = str(e)
                if svc.llm is not None and st.get("attempts", 0) < s.max_repairs:
                    nxt = "llm" if route in ("rules", "llm") else route
                    return {"route": nxt, "feedback": str(e), "sql": None, "_needs_repair": True}
                return {"status": "clarify", "message": f"I couldn't build a reliable query for that ({e}). Could you rephrase?",
                        "suggestions": SUGGESTIONS[:3]}
        return {}

    def _compiled(plan: QueryPlan, ctx: RequestCtx, sp: dict) -> QPState:
        auth = authorize_plan(plan, ctx.principal)
        if not auth.allowed:
            prom.BLOCKED.labels(category=auth.category).inc()
            return {"status": "blocked", "blocked_reason": auth.reason, "blocked_category": auth.category,
                    "message": auth.reason, "plan": plan}
        if ctx.principal.regions and ("region" in plan.dimensions or not any(f.dimension == "region" for f in plan.filters)):
            plan.assumptions.append(f"Results are limited to your authorised region(s): {', '.join(ctx.principal.regions)}.")
        compiled = compile_plan(plan, cat)
        sp.update(metrics=plan.metrics, dimensions=plan.dimensions, comparison=plan.comparison, period=plan.time.label)
        return {"plan": plan, "compiled": compiled, "sql": compiled.sql}

    def validate(st: QPState) -> QPState:
        ctx = st["ctx"]
        with ctx.tracer.span("sql_validation") as sp:
            role = svc.policy.role(ctx.principal.role)
            try:
                v = validate_sql(st["sql"], role, svc.policy, s.max_rows)
                sp["tables"] = sorted(v.tables)
                return {"validated": v, "checks": list(v.checks)}
            except SQLRejected as e:
                sp["_status"] = "error"
                sp.update(category=e.category, reason=str(e))
                if st["route"] in ("llm", "llm_sql") and st.get("attempts", 0) < s.max_repairs:
                    return {"feedback": f"SQL rejected by validator: {e}", "checks": e.checks, "_needs_repair": True}
                prom.BLOCKED.labels(category=e.category).inc()
                status = "blocked" if e.category not in ("syntax",) else "error"
                msg = f"Query blocked by policy: {e}." if status == "blocked" else f"The SQL is invalid: {e}."
                return {"status": status, "blocked_reason": str(e) if status == "blocked" else None,
                        "blocked_category": e.category, "message": msg, "checks": e.checks}

    def repair(st: QPState) -> QPState:
        ctx = st["ctx"]
        with ctx.tracer.span("repair", attempt=st.get("attempts", 0) + 1, feedback=st.get("feedback")):
            prom.REPAIRS.labels(reason=(st.get("feedback") or "")[:30].split(":")[0]).inc()
            return {"attempts": st.get("attempts", 0) + 1, "_needs_repair": False}

    def execute(st: QPState) -> QPState:
        ctx = st["ctx"]
        with ctx.tracer.span("execution") as sp:
            role = svc.policy.role(ctx.principal.role)
            try:
                r = svc.executor.run(st["validated"].sql, ctx.principal, min(s.max_rows, role.max_rows), cat.fingerprint)
            except ExecutionError as e:
                sp["_status"] = "error"
                sp.update(kind=e.kind, error=str(e))
                if e.repairable and st["route"] in ("llm", "llm_sql") and st.get("attempts", 0) < s.max_repairs:
                    return {"feedback": f"Execution failed ({e.kind}): {e}", "_needs_repair": True}
                if e.kind in ("permission", "read_only"):
                    prom.BLOCKED.labels(category="database").inc()
                    return {"status": "blocked", "blocked_reason": str(e), "blocked_category": "database",
                            "message": f"The database refused this query: {e}."}
                return {"status": "error", "message": f"The query could not be executed: {e}."}
            sp.update(rows=len(r.rows), db_ms=r.db_ms, plan_cost=r.plan_cost, cache_hit=r.cache_hit)
            prom.CACHE.labels(result="hit" if r.cache_hit else "miss").inc()
            if not r.cache_hit:
                prom.DB_LATENCY.observe(r.db_ms / 1000)
            return {"exec": r}

    def run_plan(plan: QueryPlan, ctx: RequestCtx) -> tuple[TableResult, str]:
        comp = compile_plan(plan, cat)
        v = validate_sql(comp.sql, svc.policy.role(ctx.principal.role), svc.policy, s.max_rows)
        r = svc.executor.run(v.sql, ctx.principal, s.max_rows, cat.fingerprint)
        return TableResult(columns=comp.columns, rows=r.rows, row_count=len(r.rows), truncated=r.truncated), v.sql

    def check(st: QPState) -> QPState:
        ctx = st["ctx"]
        with ctx.tracer.span("result_validation") as sp:
            r = st["exec"]
            plan = st.get("plan")
            if st.get("compiled") is not None:
                table = TableResult(columns=st["compiled"].columns, rows=r.rows, row_count=len(r.rows), truncated=r.truncated)
            else:
                table = sql_result_table(r.columns, r.rows, r.truncated)

            def total_for(p: QueryPlan):
                t, sql = run_plan(p, ctx)
                ctx.supporting_sql.append(sql)
                return t.rows[0][len(p.dimensions)] if t.rows else None

            bounds = svc.executor.data_bounds(ctx.principal.tenant_id)
            chks = check_result(plan, table, cat, bounds, total_for if plan is not None else None)
            sp["failed"] = [c.name for c in chks if not c.passed]
            return {"table": table, "checks": st.get("checks", []) + chks}

    def explain(st: QPState) -> QPState:
        ctx = st["ctx"]
        with ctx.tracer.span("explanation") as sp:
            table, plan = st["table"], st.get("plan")
            if plan is None:
                return {"status": "answered", "answer": sql_narrative(table), "kpis": [], "chart": None, "drivers": []}
            drivers, sqls = [], []
            if plan.drivers:
                try:
                    drivers, sqls = drv.analyse(plan, table, cat, lambda p: run_plan(p, ctx))
                except (ExecutionError, SQLRejected, PlanError) as e:
                    sp["drivers_error"] = str(e)
            ctx.supporting_sql.extend(sqls)
            text, kpis = narrative(plan, table, cat, drivers)
            if s.llm_narrative and svc.llm is not None:
                text = _rephrase(text, ctx) or text
            sp.update(drivers=len(drivers), chars=len(text))
            return {"status": "answered", "answer": text, "kpis": kpis, "chart": chart_for(plan, table, cat), "drivers": drivers}

    def _rephrase(text: str, ctx: RequestCtx) -> str | None:
        try:
            out = svc.llm.complete_json(
                "Rewrite the analytics summary for an executive in 2-4 crisp sentences. Use ONLY numbers that appear in "
                "the input, formatted identically. Return JSON {\"text\": \"...\"}.", text,
                {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, ctx.usage, "narrative")
            cand = out.get("text", "")
            return cand if cand and numbers_grounded(cand, text) else None
        except LLMError:
            return None

    # ------------------------------------------------------------------ edges
    def after_guard(st):
        return END if st.get("status") == "blocked" else "resolve"

    def after_resolve(st):
        return END if st.get("status") in ("clarify", "blocked") else "retrieve"

    def after_generate(st):
        if st.get("status") in ("clarify", "blocked", "error"):
            return END
        if st.get("_needs_repair"):
            return "repair"
        return "validate"

    def after_validate(st):
        if st.get("status") in ("blocked", "error"):
            return END
        return "repair" if st.get("_needs_repair") else "execute"

    def after_execute(st):
        if st.get("status") in ("blocked", "error"):
            return END
        return "repair" if st.get("_needs_repair") else "check"

    g = StateGraph(QPState)
    for name, fn in [("guard", guard), ("resolve", resolve), ("retrieve", retrieve), ("generate", generate),
                     ("validate", validate), ("repair", repair), ("execute", execute), ("check", check), ("explain", explain)]:
        g.add_node(name, fn)
    g.set_entry_point("guard")
    g.add_conditional_edges("guard", after_guard, {"resolve": "resolve", END: END})
    g.add_conditional_edges("resolve", after_resolve, {"retrieve": "retrieve", END: END})
    g.add_edge("retrieve", "generate")
    g.add_conditional_edges("generate", after_generate, {"validate": "validate", "repair": "repair", END: END})
    g.add_conditional_edges("validate", after_validate, {"execute": "execute", "repair": "repair", END: END})
    g.add_edge("repair", "generate")
    g.add_conditional_edges("execute", after_execute, {"check": "check", "repair": "repair", END: END})
    g.add_edge("check", "explain")
    g.add_edge("explain", END)
    return g.compile()


def _clarify_msg(rp: RuleParse) -> str:
    if not rp.draft.metrics:
        return ("I couldn't match that to a governed metric. I can answer questions about revenue, gross profit, gross margin, "
                "COGS, discounts, orders, units, average order value, customers, refunds and return rate - by region, "
                "category, product, channel, segment or over time.")
    unk = ", ".join(f"'{t}'" for t in rp.unknown_terms[:5])
    return f"I'm not confident I understood {unk}. Could you rephrase using the metric and breakdown you want?"

