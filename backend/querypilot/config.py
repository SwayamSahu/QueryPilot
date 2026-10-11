"""Runtime configuration (12-factor: everything comes from QP_* environment variables)."""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QP_", env_file=".env", extra="ignore")

    # --- databases -------------------------------------------------------------
    # Owner: used only for migrations, seeding and computing benchmark gold answers.
    db_admin_url: str = "postgresql+psycopg://querypilot:querypilot_dev@localhost:5432/querypilot"
    # Read-only, RLS-enforced role. ALL generated SQL runs through this connection.
    db_reader_url: str = "postgresql+psycopg://qp_reader:qp_reader_dev@localhost:5432/querypilot"
    # Append-only writer for audit log / traces / conversations.
    db_app_url: str = "postgresql+psycopg://qp_app:qp_app_dev@localhost:5432/querypilot"

    # --- query guardrails ----------------------------------------------------------
    max_rows: int = 1000
    statement_timeout_ms: int = 5000
    max_plan_cost: float = 5_000_000.0  # EXPLAIN total cost ceiling
    max_repairs: int = 2
    cache_ttl_s: int = 300
    cache_max_entries: int = 512

    # --- planner / LLM -------------------------------------------------------------
    # rules  : deterministic semantic parser only (no model needed)
    # llm    : LLM structured planner (+ raw SQL route) for everything
    # hybrid : rules first; LLM when rule coverage is low (default)
    planner: str = "hybrid"
    llm_provider: str = "none"  # none | ollama | openai_compatible
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5-coder:7b"
    llm_timeout_s: float = 60.0
    llm_temperature: float = 0.0
    llm_narrative: bool = False  # let the LLM rephrase the (number-checked) narrative
    # Local models cost $0; set these to estimate cost if you point at a priced endpoint.
    llm_usd_per_1k_input: float = 0.0
    llm_usd_per_1k_output: float = 0.0
    min_confidence: float = 0.55

    # --- retrieval -----------------------------------------------------------------
    retriever: str = "tfidf"  # tfidf | embedding (sentence-transformers + pgvector)
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    retrieval_k: int = 8

    # --- misc ------------------------------------------------------------------------
    catalog_path: Path = BACKEND_DIR / "catalog" / "semantic_layer.yaml"
    policies_path: Path = BACKEND_DIR / "catalog" / "policies.yaml"
    reference_date: date | None = None  # "today" for relative dates; default = real today
    jwt_secret: str = "change-me-in-production-please-32b"
    jwt_ttl_minutes: int = 12 * 60
    cors_origins: str = "http://localhost:3000"
    benchmark_report_path: Path = BACKEND_DIR / "benchmark" / "results" / "latest.json"

    def today(self) -> date:
        return self.reference_date or date.today()


@lru_cache
def get_settings() -> Settings:
    return Settings()
