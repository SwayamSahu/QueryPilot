"""Prometheus metrics (scraped at /metrics)."""
from prometheus_client import Counter, Histogram

LAT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60)

REQUESTS = Counter("querypilot_requests_total", "Questions processed", ["status", "route"])
BLOCKED = Counter("querypilot_blocked_total", "Questions/queries blocked by policy", ["category"])
REQUEST_LATENCY = Histogram("querypilot_request_seconds", "End-to-end question latency", buckets=LAT_BUCKETS)
STAGE_LATENCY = Histogram("querypilot_stage_seconds", "Latency per pipeline stage", ["stage"], buckets=LAT_BUCKETS)
DB_LATENCY = Histogram("querypilot_db_seconds", "Database execution time", buckets=LAT_BUCKETS)
CACHE = Counter("querypilot_cache_total", "Result cache lookups", ["result"])
LLM_TOKENS = Counter("querypilot_llm_tokens_total", "LLM tokens", ["direction"])
REPAIRS = Counter("querypilot_repairs_total", "Repair attempts", ["reason"])
CONFIDENCE = Histogram("querypilot_confidence", "Answer confidence", buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0))
