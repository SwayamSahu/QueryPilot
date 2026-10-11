"""Public entry point: QueryPilot.ask(principal, question, conversation_id) -> AskResponse."""
from __future__ import annotations

import logging
import time
from functools import lru_cache

from querypilot.config import get_settings
from querypilot.core.models import AskResponse, MetricRef, Principal, QueryPlan
from querypilot.llm.client import Usage, build_llm
from querypilot.ops import metrics as prom
from querypilot.ops.store import OpsStore, new_conversation_id, plan_to_state
from querypilot.ops.tracing import Tracer
from querypilot.pipeline.executor import Executor, TTLCache
from querypilot.pipeline.graph import RequestCtx, Services, build_graph
from querypilot.safety.policy import get_policy
from querypilot.semantic.catalog import get_catalog
from querypilot.semantic.retriever import get_retriever
from querypilot.semantic.rules_planner import RulePlanner

log = logging.getLogger(__name__)


class QueryPilot:
    def __init__(self, store: OpsStore | None = None, executor: Executor | None = None, llm=None, persist: bool = True):
        s = get_settings()
        cat = get_catalog()
        self.svc = Services(settings=s, catalog=cat, policy=get_policy(), executor=executor or Executor(),
                            rules=RulePlanner(cat), retriever=get_retriever(), llm=llm if llm is not None else build_llm(),
                            plan_cache=TTLCache(256, 3600))
        self.graph = build_graph(self.svc)
        self.store = store or OpsStore()
        self.persist = persist

    def ask(self, principal: Principal, question: str, conversation_id: str | None = None,
            previous_plan: QueryPlan | None = None) -> AskResponse:
        t0 = time.perf_counter()
        tracer = Tracer()
        ctx = RequestCtx(principal=principal, tracer=tracer, usage=Usage())
        conv_id = conversation_id or new_conversation_id()
        state = self.store.load_state(conv_id, principal) if (conversation_id and self.persist) else {}
        if previous_plan is None and state.get("plan"):
            try:
                previous_plan = QueryPlan.model_validate(state["plan"])
            except Exception:  # noqa: BLE001
                previous_plan = None
        try:
            out = self.graph.invoke({"ctx": ctx, "question": question, "previous_plan": previous_plan, "status": "running"},
                                    {"recursion_limit": 40})
        except Exception as e:  # noqa: BLE001 - never leak a stack trace to the user
            log.exception("pipeline failure")
            out = {"status": "error", "message": f"Internal error while answering ({type(e).__name__}). It has been logged."}
        resp = self._response(question, conv_id, out, ctx, t0)
        if self.persist:
            history = (state.get("history") or [])[-9:] + [{"q": question, "status": resp.status, "answer": resp.answer[:400]}]
            new_plan = resp.plan if resp.status == "answered" and resp.plan else previous_plan
            self.store.save_state(conv_id, principal, {"plan": plan_to_state(new_plan), "history": history})
            self.store.record(principal, resp, out.get("intent"))
        prom.REQUESTS.labels(status=resp.status, route=resp.route).inc()
        prom.REQUEST_LATENCY.observe(resp.timings["total_ms"] / 1000)
        prom.LLM_TOKENS.labels(direction="input").inc(ctx.usage.input_tokens)
        prom.LLM_TOKENS.labels(direction="output").inc(ctx.usage.output_tokens)
        prom.CONFIDENCE.observe(resp.confidence)
        return resp

    def _response(self, question: str, conv_id: str, out: dict, ctx: RequestCtx, t0: float) -> AskResponse:
        cat = self.svc.catalog
        status = out.get("status", "error")
        if status == "running":
            status = "error"
        plan: QueryPlan | None = out.get("plan")
        route_raw = out.get("route")
        route = "semantic" if route_raw in ("rules", "llm") and plan is not None else ("sql" if route_raw in ("llm_sql", "user_sql") else "none")
        checks = out.get("checks", [])
        conf = float(out.get("confidence") or 0.0)
        if route_raw == "user_sql" and status == "answered":
            conf = 0.9
        conf -= 0.15 * int(out.get("attempts") or 0)
        conf -= sum(0.1 for c in checks if not c.passed and c.severity == "warn")
        conf -= sum(0.35 for c in checks if not c.passed and c.severity == "error")
        conf = max(0.0, min(1.0, round(conf, 3))) if status == "answered" else 0.0
        answer = out.get("answer") or out.get("message") or ""
        if status == "answered" and conf < self.svc.settings.min_confidence:
            answer = "⚠ Low confidence - please verify against the SQL and checks below. " + answer
        exec_r = out.get("exec")
        stage = ctx.tracer.stage_totals()
        metrics_used = []
        if plan is not None:
            names = list(plan.metrics)
            for d in out.get("drivers") or []:
                for k in ("gross_sales", "discount_amount", "cogs", "revenue"):
                    if d.get("components") and k not in names and plan.metrics[0] in ("gross_margin", "gross_profit"):
                        names.append(k)
            for m in dict.fromkeys(names):
                md = cat.metrics[m]
                metrics_used.append(MetricRef(name=m, label=md.label, version=md.version, definition=md.description, sql=cat.expand(m)))
        assumptions = list(plan.assumptions) if plan else []
        validated = out.get("validated")
        return AskResponse(
            status=status, question=question, answer=answer, conversation_id=conv_id, trace_id=ctx.tracer.trace_id,
            route=route, assumptions=assumptions, kpis=out.get("kpis") or [], table=out.get("table"), chart=out.get("chart"),
            drivers=out.get("drivers") or [], sql=validated.sql if validated else out.get("sql"), plan=plan,
            metrics_used=metrics_used, checks=checks, confidence=conf, suggestions=out.get("suggestions") or [],
            blocked_reason=out.get("blocked_reason"), cache_hit=bool(exec_r and exec_r.cache_hit),
            catalog_version=cat.version,
            timings={"total_ms": round((time.perf_counter() - t0) * 1000, 2), "db_ms": exec_r.db_ms if exec_r else 0.0, **stage},
            usage={"input_tokens": ctx.usage.input_tokens, "output_tokens": ctx.usage.output_tokens, "llm_calls": ctx.usage.calls,
                   "est_cost_usd": ctx.usage.cost_usd(), "db_plan_cost": exec_r.plan_cost if exec_r else 0.0,
                   "supporting_queries": len(ctx.supporting_sql), "supporting_sql": ctx.supporting_sql,
                   "blocked_category": out.get("blocked_category"), "intent": out.get("intent"),
                   "repairs": int(out.get("attempts") or 0), "model": getattr(self.svc.llm, "model", None)},
            spans=ctx.tracer.spans,
        )


@lru_cache
def get_pilot() -> QueryPilot:
    return QueryPilot()
