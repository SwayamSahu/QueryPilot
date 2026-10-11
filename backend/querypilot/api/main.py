"""FastAPI application."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from querypilot.config import get_settings
from querypilot.core.models import AskRequest, AskResponse, Principal
from querypilot.ops.drift import drift_report
from querypilot.pipeline.service import get_pilot
from querypilot.safety.policy import get_policy
from querypilot.semantic.catalog import get_catalog

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("querypilot.api")

app = FastAPI(title="QueryPilot", version="1.0.0",
              description="Governed natural-language analytics: NL -> verified SQL -> evidence-backed answers.")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ auth
class LoginRequest(BaseModel):
    user_id: str


def issue_token(user_id: str) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    return jwt.encode({"sub": user_id, "iat": now, "exp": now + timedelta(minutes=s.jwt_ttl_minutes)}, s.jwt_secret, algorithm="HS256")


def current_principal(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(auth[7:], get_settings().jwt_secret, algorithms=["HS256"])
        return get_policy().principal(claims["sub"])
    except (jwt.PyJWTError, KeyError) as e:
        raise HTTPException(401, "invalid token") from e


# ------------------------------------------------------------------ rate limit (per user, in-process)
_hits: dict[str, deque] = defaultdict(deque)
_rl_lock = threading.Lock()
RATE_PER_MIN = 30


def rate_limit(p: Principal = Depends(current_principal)) -> Principal:
    now = time.time()
    with _rl_lock:
        q = _hits[f"{p.tenant_id}:{p.user_id}"]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_PER_MIN:
            raise HTTPException(429, f"rate limit: {RATE_PER_MIN} questions per minute")
        q.append(now)
    return p


# ------------------------------------------------------------------ routes
@app.get("/healthz")
def healthz():
    return {"ok": True, "catalog_version": get_catalog().version}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/auth/demo-users")
def demo_users():
    return [{"user_id": k, **v} for k, v in get_policy().demo_users.items()]


@app.post("/api/auth/demo-login")
def demo_login(body: LoginRequest):
    if body.user_id not in get_policy().demo_users:
        raise HTTPException(404, "unknown demo user")
    return {"token": issue_token(body.user_id), "user": get_policy().principal(body.user_id)}


@app.get("/api/me")
def me(p: Principal = Depends(current_principal)):
    role = get_policy().role(p.role)
    return {**p.model_dump(), "max_rows": role.max_rows, "tables": sorted(role.tables)}


@app.post("/api/ask", response_model=AskResponse)
async def ask(body: AskRequest, p: Principal = Depends(rate_limit)):
    pilot = get_pilot()
    return await run_in_threadpool(pilot.ask, p, body.question, body.conversation_id)


@app.get("/api/catalog")
def catalog(_: Principal = Depends(current_principal)):
    pol = get_policy()
    return {**get_catalog().describe(), "policy_version": pol.version,
            "roles": {k: {"tables": sorted(r.tables), "region_scope": r.region_scope, "max_rows": r.max_rows} for k, r in pol.roles.items()}}


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str, p: Principal = Depends(current_principal)):
    return get_pilot().store.load_state(conversation_id, p)


@app.get("/api/ops/summary")
def ops_summary(p: Principal = Depends(current_principal)):
    store = get_pilot().store
    out = store.summary(p.tenant_id)
    ex = get_pilot().svc.executor
    out["result_cache"] = {"hits": ex.cache.hits, "misses": ex.cache.misses}
    out["planner"] = get_settings().planner
    out["llm"] = {"provider": get_settings().llm_provider, "model": get_settings().llm_model if get_pilot().svc.llm else None}
    out["retriever"] = getattr(get_pilot().svc.retriever, "name", "?")
    return out


@app.get("/api/ops/audit")
def ops_audit(limit: int = 50, p: Principal = Depends(current_principal)):
    if p.role == "regional_manager":
        raise HTTPException(403, "audit log requires cfo or analyst role")
    return get_pilot().store.audit(p.tenant_id, min(limit, 500))


@app.get("/api/ops/traces/{trace_id}")
def ops_trace(trace_id: str, p: Principal = Depends(current_principal)):
    t = get_pilot().store.trace(p.tenant_id, trace_id)
    if not t:
        raise HTTPException(404, "trace not found")
    return t


@app.get("/api/ops/drift")
def ops_drift(p: Principal = Depends(current_principal)):
    return drift_report(get_pilot().store.recent_traces(p.tenant_id))


@app.get("/api/ops/benchmark")
def ops_benchmark(_: Principal = Depends(current_principal)):
    path = get_settings().benchmark_report_path
    if not path.exists():
        return JSONResponse({"status": "not_run", "hint": "python -m querypilot.eval.run"}, status_code=200)
    data = json.loads(path.read_text())
    data.pop("cases", None)
    return data


@app.on_event("startup")
def warm():
    try:
        pilot = get_pilot()
        for u in get_policy().demo_users.values():
            pilot.svc.executor.data_bounds(u["tenant_id"])
            pilot.svc.executor.dimension_values(u["tenant_id"], pilot.svc.catalog)
        log.info("QueryPilot ready: planner=%s llm=%s retriever=%s catalog=%s", get_settings().planner,
                 get_settings().llm_provider, getattr(pilot.svc.retriever, "name", "?"), pilot.svc.catalog.version)
    except Exception as e:  # noqa: BLE001
        log.warning("warmup failed (database not ready?): %s", e)
