"""Read-only SQL execution with tenant/region scoping, timeouts, cost guard and row caps."""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Any

from psycopg import errors as pgerr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from querypilot.config import get_settings
from querypilot.core.models import Principal


class ExecutionError(Exception):
    def __init__(self, message: str, kind: str, repairable: bool):
        super().__init__(message)
        self.kind = kind
        self.repairable = repairable


@dataclass
class ExecResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    db_ms: float
    plan_cost: float
    cache_hit: bool = False


def _py(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


class TTLCache:
    """Small thread-safe LRU with TTL for validated, read-only query results."""

    def __init__(self, max_entries: int, ttl_s: int):
        self.max, self.ttl = max_entries, ttl_s
        self._d: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key: str):
        with self._lock:
            item = self._d.get(key)
            if item and time.time() - item[0] < self.ttl:
                self._d.move_to_end(key)
                self.hits += 1
                return item[1]
            if item:
                del self._d[key]
            self.misses += 1
            return None

    def put(self, key: str, value: Any):
        with self._lock:
            self._d[key] = (time.time(), value)
            self._d.move_to_end(key)
            while len(self._d) > self.max:
                self._d.popitem(last=False)

    def clear(self):
        with self._lock:
            self._d.clear()


@lru_cache
def reader_engine() -> Engine:
    s = get_settings()
    return create_engine(s.db_reader_url, pool_size=10, max_overflow=10, pool_pre_ping=True, pool_recycle=1800)


class Executor:
    def __init__(self, engine: Engine | None = None, cache: TTLCache | None = None):
        s = get_settings()
        self.engine = engine or reader_engine()
        self.cache = cache or TTLCache(s.cache_max_entries, s.cache_ttl_s)
        self._region_ids: dict[int, dict[str, int]] = {}
        self._bounds: dict[int, tuple[date, date]] = {}
        self._values: dict[int, dict[str, list[str]]] = {}

    # ---- scoping helpers (run under the tenant's own RLS context) -------------
    def _scoped(self, conn, tenant_id: int, region_ids: str, timeout_ms: int):
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true), set_config('app.region_ids', :r, true), "
                          "set_config('statement_timeout', :ms, true)"),
                     {"t": str(tenant_id), "r": region_ids, "ms": str(timeout_ms)})

    def region_ids(self, principal: Principal) -> str:
        if not principal.regions:
            return ""
        if principal.tenant_id not in self._region_ids:
            with self.engine.connect() as conn, conn.begin():
                self._scoped(conn, principal.tenant_id, "", 2000)
                rows = conn.execute(text("SELECT region_name, region_id FROM sales.regions")).all()
            self._region_ids[principal.tenant_id] = {r[0].lower(): r[1] for r in rows}
        m = self._region_ids[principal.tenant_id]
        ids = [str(m[r.lower()]) for r in principal.regions if r.lower() in m]
        return ",".join(ids) if ids else "-1"  # unknown region => sees nothing (fail closed)

    def data_bounds(self, tenant_id: int) -> tuple[date, date]:
        if tenant_id not in self._bounds:
            with self.engine.connect() as conn, conn.begin():
                self._scoped(conn, tenant_id, "", 5000)
                lo, hi = conn.execute(text("SELECT min(order_date), max(order_date) FROM sales.orders")).one()
            from datetime import timedelta
            self._bounds[tenant_id] = (lo or date(2024, 1, 1), (hi or date.today()) + timedelta(days=1))
        return self._bounds[tenant_id]

    def dimension_values(self, tenant_id: int, catalog) -> dict[str, list[str]]:
        """Distinct values per low-cardinality dimension, for filter matching (tenant-scoped)."""
        if tenant_id not in self._values:
            out: dict[str, list[str]] = {}
            with self.engine.connect() as conn, conn.begin():
                self._scoped(conn, tenant_id, "", 5000)
                for key, dim in catalog.dimensions.items():
                    if dim.values_from:
                        out[key] = [r[0] for r in conn.execute(text(dim.values_from + " LIMIT 500")).all() if r[0]]
            self._values[tenant_id] = out
        return self._values[tenant_id]

    def tenant_names(self) -> dict[int, str]:
        # Tenant names are needed by the cross-tenant guard; read through the owner-less
        # path is not possible for other tenants under RLS, so they come from policy demo users.
        from querypilot.safety.policy import get_policy
        return {u["tenant_id"]: u.get("tenant", "") for u in get_policy().demo_users.values() if u.get("tenant")}

    # ---- execution -----------------------------------------------------------
    def run(self, sql: str, principal: Principal, max_rows: int, catalog_fingerprint: str = "",
            use_cache: bool = True) -> ExecResult:
        s = get_settings()
        regions = self.region_ids(principal)
        key = hashlib.sha256(f"{catalog_fingerprint}|{principal.tenant_id}|{regions}|{max_rows}|{sql}".encode()).hexdigest()
        if use_cache:
            hit = self.cache.get(key)
            if hit is not None:
                return ExecResult(**{**hit.__dict__, "cache_hit": True})
        t0 = time.perf_counter()
        try:
            with self.engine.connect() as conn, conn.begin():
                self._scoped(conn, principal.tenant_id, regions, s.statement_timeout_ms)
                cur = conn.connection.cursor()  # raw DBAPI cursor: no param interpolation of '%' or ':'
                cur.execute("EXPLAIN (FORMAT JSON) " + sql)
                plan = cur.fetchone()[0]
                cost = float(plan[0]["Plan"]["Total Cost"])
                if cost > s.max_plan_cost:
                    raise ExecutionError(f"estimated query cost {cost:,.0f} exceeds the limit of {s.max_plan_cost:,.0f}; "
                                         "add filters or aggregate further", "cost_limit", True)
                cur.execute(sql)
                cols = [d.name for d in cur.description]
                fetched = cur.fetchmany(max_rows + 1)
                cur.close()
                conn.rollback()
        except ExecutionError:
            raise
        except (DBAPIError, pgerr.Error) as e:
            orig = getattr(e, 'orig', e)
            msg = str(orig).splitlines()[0] if orig else str(e)
            if isinstance(orig, pgerr.QueryCanceled):
                raise ExecutionError(f"query exceeded the {s.statement_timeout_ms} ms timeout", "timeout", True) from e
            if isinstance(orig, pgerr.InsufficientPrivilege):
                raise ExecutionError(f"database denied access: {msg}", "permission", False) from e
            if isinstance(orig, pgerr.ReadOnlySqlTransaction):
                raise ExecutionError("write attempted in a read-only transaction", "read_only", False) from e
            kind = type(orig).__name__ if orig else "db_error"
            raise ExecutionError(msg, kind, True) from e
        db_ms = (time.perf_counter() - t0) * 1000
        truncated = len(fetched) > max_rows
        rows = [[_py(v) for v in r] for r in fetched[:max_rows]]
        result = ExecResult(columns=cols, rows=rows, truncated=truncated, db_ms=round(db_ms, 2), plan_cost=cost)
        if use_cache:
            self.cache.put(key, result)
        return result
