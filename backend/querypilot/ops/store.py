"""Persistence for audit log (append-only), traces and conversation state."""
from __future__ import annotations

import json
import logging
import uuid
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from querypilot.config import get_settings
from querypilot.core.models import AskResponse, Principal, QueryPlan

log = logging.getLogger(__name__)


@lru_cache
def app_engine() -> Engine:
    return create_engine(get_settings().db_app_url, pool_size=5, max_overflow=5, pool_pre_ping=True)


class OpsStore:
    def __init__(self, engine: Engine | None = None):
        self.engine = engine or app_engine()

    # ---- conversations ---------------------------------------------------------
    def load_state(self, conversation_id: str, principal: Principal) -> dict:
        try:
            with self.engine.connect() as conn:
                row = conn.execute(text("SELECT state FROM qp_ops.conversations WHERE conversation_id = :c "
                                        "AND tenant_id = :t AND user_id = :u"),
                                   {"c": conversation_id, "t": principal.tenant_id, "u": principal.user_id}).first()
            return row[0] if row else {}
        except Exception as e:  # noqa: BLE001
            log.warning("load_state failed: %s", e)
            return {}

    def save_state(self, conversation_id: str, principal: Principal, state: dict) -> None:
        try:
            with self.engine.begin() as conn:
                conn.execute(text("INSERT INTO qp_ops.conversations (conversation_id, tenant_id, user_id, state) "
                                  "VALUES (:c, :t, :u, CAST(:s AS jsonb)) ON CONFLICT (conversation_id) DO UPDATE "
                                  "SET state = EXCLUDED.state, updated_at = now() "
                                  "WHERE qp_ops.conversations.tenant_id = EXCLUDED.tenant_id AND qp_ops.conversations.user_id = EXCLUDED.user_id"),
                             {"c": conversation_id, "t": principal.tenant_id, "u": principal.user_id, "s": json.dumps(state, default=str)})
        except Exception as e:  # noqa: BLE001
            log.warning("save_state failed: %s", e)

    # ---- audit + traces ----------------------------------------------------------
    def record(self, principal: Principal, resp: AskResponse, intent: str | None) -> None:
        try:
            with self.engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO qp_ops.audit_log (trace_id, user_id, tenant_id, role, conversation_id, question, status, "
                    "blocked_reason, sql_text, row_count, latency_ms, cache_hit) VALUES (:tr, :u, :t, :r, :c, :q, :s, :b, :sql, :rc, :l, :ch)"),
                    {"tr": resp.trace_id, "u": principal.user_id, "t": principal.tenant_id, "r": principal.role,
                     "c": resp.conversation_id, "q": resp.question, "s": resp.status, "b": resp.blocked_reason, "sql": resp.sql,
                     "rc": resp.table.row_count if resp.table else None, "l": resp.timings.get("total_ms"), "ch": resp.cache_hit})
                conn.execute(text(
                    "INSERT INTO qp_ops.traces (trace_id, tenant_id, user_id, status, intent, metrics, confidence, total_ms, "
                    "llm_tokens, db_ms, spans) VALUES (:tr, :t, :u, :s, :i, :m, :c, :tot, :tok, :db, CAST(:sp AS jsonb))"),
                    {"tr": resp.trace_id, "t": principal.tenant_id, "u": principal.user_id, "s": resp.status, "i": intent,
                     "m": resp.plan.metrics if resp.plan else [], "c": resp.confidence, "tot": resp.timings.get("total_ms"),
                     "tok": int(resp.usage.get("input_tokens", 0)) + int(resp.usage.get("output_tokens", 0)),
                     "db": resp.timings.get("db_ms"), "sp": json.dumps([s.model_dump() for s in resp.spans])})
        except Exception as e:  # noqa: BLE001
            log.warning("audit/trace write failed: %s", e)

    def audit(self, tenant_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT ts, user_id, role, question, status, blocked_reason, row_count, latency_ms, cache_hit, "
                                     "trace_id FROM qp_ops.audit_log WHERE tenant_id = :t ORDER BY ts DESC LIMIT :n"),
                                {"t": tenant_id, "n": limit}).mappings().all()
        return [{**r, "ts": r["ts"].isoformat(), "latency_ms": float(r["latency_ms"] or 0), "trace_id": str(r["trace_id"])} for r in rows]

    def trace(self, tenant_id: int, trace_id: str) -> dict | None:
        try:
            uuid.UUID(trace_id)
        except ValueError:
            return None
        with self.engine.connect() as conn:
            r = conn.execute(text("SELECT * FROM qp_ops.traces WHERE trace_id = :i AND tenant_id = :t"),
                             {"i": trace_id, "t": tenant_id}).mappings().first()
        if not r:
            return None
        return {k: (v.isoformat() if hasattr(v, "isoformat") else float(v) if k in ("total_ms", "db_ms", "confidence") and v is not None
                    else str(v) if k == "trace_id" else v) for k, v in r.items()}

    def summary(self, tenant_id: int, last_n: int = 500) -> dict[str, Any]:
        with self.engine.connect() as conn:
            r = conn.execute(text(
                "WITH t AS (SELECT * FROM qp_ops.traces WHERE tenant_id = :t ORDER BY ts DESC LIMIT :n) "
                "SELECT count(*) AS n, "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms) AS p50, "
                "percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms) AS p95, "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY db_ms) AS db_p50, "
                "avg(llm_tokens) AS avg_tokens, avg(confidence) AS avg_conf, "
                "count(*) FILTER (WHERE status = 'answered') AS answered, "
                "count(*) FILTER (WHERE status = 'blocked') AS blocked, "
                "count(*) FILTER (WHERE status = 'clarify') AS clarify, "
                "count(*) FILTER (WHERE status = 'error') AS errors FROM t"), {"t": tenant_id, "n": last_n}).mappings().one()
            stages = conn.execute(text(
                "WITH t AS (SELECT spans FROM qp_ops.traces WHERE tenant_id = :t ORDER BY ts DESC LIMIT :n) "
                "SELECT s->>'name' AS stage, percentile_cont(0.5) WITHIN GROUP (ORDER BY (s->>'duration_ms')::float) AS p50, "
                "percentile_cont(0.95) WITHIN GROUP (ORDER BY (s->>'duration_ms')::float) AS p95, count(*) AS n "
                "FROM t, jsonb_array_elements(spans) s GROUP BY 1 ORDER BY 2 DESC"), {"t": tenant_id, "n": last_n}).mappings().all()
            cache = conn.execute(text("SELECT count(*) FILTER (WHERE cache_hit) AS hits, count(*) AS n FROM "
                                      "(SELECT cache_hit FROM qp_ops.audit_log WHERE tenant_id = :t ORDER BY ts DESC LIMIT :n) a"),
                                 {"t": tenant_id, "n": last_n}).mappings().one()
        f = lambda v: round(float(v), 2) if v is not None else None  # noqa: E731
        return {"requests": r["n"], "p50_ms": f(r["p50"]), "p95_ms": f(r["p95"]), "db_p50_ms": f(r["db_p50"]),
                "avg_llm_tokens": f(r["avg_tokens"]), "avg_confidence": f(r["avg_conf"]),
                "status": {"answered": r["answered"], "blocked": r["blocked"], "clarify": r["clarify"], "error": r["errors"]},
                "cache_hit_rate": f(cache["hits"] / cache["n"]) if cache["n"] else None,
                "stages": [{"stage": s["stage"], "p50_ms": f(s["p50"]), "p95_ms": f(s["p95"]), "n": s["n"]} for s in stages]}

    def recent_traces(self, tenant_id: int, limit: int = 1000) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT ts, status, intent, metrics, confidence FROM qp_ops.traces WHERE tenant_id = :t "
                                     "ORDER BY ts DESC LIMIT :n"), {"t": tenant_id, "n": limit}).mappings().all()
        return [dict(r) for r in rows]


def plan_to_state(plan: QueryPlan | None) -> dict | None:
    return plan.model_dump(mode="json") if plan else None


def new_conversation_id() -> str:
    return str(uuid.uuid4())
