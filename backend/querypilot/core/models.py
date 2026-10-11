"""Shared data contracts between pipeline stages, the API and the UI."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class Principal(BaseModel):
    """The authenticated caller. Everything downstream is scoped by this."""

    user_id: str
    name: str
    title: str = ""
    tenant_id: int
    tenant: str = ""
    role: str
    regions: list[str] = Field(default_factory=list)  # empty = all regions of the tenant


class TimeSpec(BaseModel):
    start: date  # inclusive
    end: date  # exclusive
    label: str
    grain: Literal["day", "month", "quarter", "half", "year", "custom", "all"] = "custom"


class Filter(BaseModel):
    dimension: str
    op: Literal["in", "not_in"] = "in"
    values: list[str]


class QueryPlan(BaseModel):
    """Governed, semantic-layer query. Compiled to SQL deterministically."""

    metrics: list[str]
    dimensions: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    time: TimeSpec | None = None
    comparison: Literal["none", "previous_period", "yoy", "custom"] = "none"
    compare_to: TimeSpec | None = None  # resolved comparison period (set when comparison != none)
    change_filter: Literal["none", "declining", "increasing"] = "none"
    order_by: str | None = None  # metric key, "<metric>_change", or a dimension key
    order_dir: Literal["asc", "desc"] = "desc"
    limit: int | None = None
    drivers: bool = False
    assumptions: list[str] = Field(default_factory=list)

    def signature(self) -> str:
        return self.model_dump_json(exclude={"assumptions"})


class Check(BaseModel):
    name: str
    passed: bool
    detail: str = ""
    severity: Literal["info", "warn", "error"] = "info"


class Span(BaseModel):
    name: str
    start_ms: float
    duration_ms: float
    status: Literal["ok", "error", "skipped"] = "ok"
    attributes: dict[str, Any] = Field(default_factory=dict)


class ChartSpec(BaseModel):
    type: Literal["kpi", "bar", "grouped_bar", "line", "table"]
    x: str | None = None
    series: list[dict[str, str]] = Field(default_factory=list)  # [{key, label, format}]
    title: str = ""


class ColumnMeta(BaseModel):
    key: str
    label: str
    kind: Literal["dimension", "metric", "previous", "change", "change_pct", "other"] = "other"
    format: Literal["text", "currency", "percent", "number", "pp"] = "text"


class TableResult(BaseModel):
    columns: list[ColumnMeta]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False


class MetricRef(BaseModel):
    name: str
    label: str
    version: int
    definition: str
    sql: str


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class AskResponse(BaseModel):
    status: Literal["answered", "clarify", "blocked", "error"]
    question: str
    answer: str
    conversation_id: str
    trace_id: str
    route: Literal["semantic", "sql", "none"] = "none"
    assumptions: list[str] = Field(default_factory=list)
    kpis: list[dict[str, Any]] = Field(default_factory=list)
    table: TableResult | None = None
    chart: ChartSpec | None = None
    drivers: list[dict[str, Any]] = Field(default_factory=list)
    sql: str | None = None
    plan: QueryPlan | None = None
    metrics_used: list[MetricRef] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    confidence: float = 0.0
    suggestions: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    cache_hit: bool = False
    catalog_version: str = ""
    timings: dict[str, float] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    spans: list[Span] = Field(default_factory=list)
