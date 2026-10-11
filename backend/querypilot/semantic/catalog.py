"""Loads the versioned semantic layer (metrics, dimensions, joins, table docs)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from querypilot.config import get_settings


@dataclass
class Metric:
    key: str
    label: str
    version: int
    description: str
    sql: str
    format: str
    additive: bool
    synonyms: list[str]
    requires: list[str] = field(default_factory=list)


@dataclass
class Dimension:
    key: str
    label: str
    sql: str
    requires: list[str]
    synonyms: list[str]
    values_from: str | None = None
    time_grain: bool = False


@dataclass
class Join:
    alias: str
    table: str
    on: str
    type: str = "inner"
    required: bool = False


class Catalog:
    def __init__(self, raw: dict, source: Path | None = None):
        self.raw = raw
        self.source = source
        self.version: str = str(raw["catalog_version"])
        self.changelog = raw.get("changelog", [])
        f = raw["fact"]
        self.fact_table: str = f["table"]
        self.fact_alias: str = f["alias"]
        self.base_filters: list[str] = f.get("base_filters", [])
        self.time_column: str = f["time_column"]
        self.joins: dict[str, Join] = {
            a: Join(alias=a, table=j["table"], on=j["on"], type=j.get("type", "inner"), required=j.get("required", False))
            for a, j in f["joins"].items()
        }
        self.metrics: dict[str, Metric] = {
            k: Metric(key=k, label=m["label"], version=int(m["version"]), description=m["description"], sql=m["sql"],
                      format=m["format"], additive=bool(m.get("additive", False)), synonyms=list(m.get("synonyms", [])),
                      requires=list(m.get("requires", [])))
            for k, m in raw["metrics"].items()
        }
        self.dimensions: dict[str, Dimension] = {
            k: Dimension(key=k, label=d["label"], sql=d["sql"], requires=list(d.get("requires", [])),
                         synonyms=list(d.get("synonyms", [])), values_from=d.get("values_from"),
                         time_grain=bool(d.get("time_grain", False)))
            for k, d in raw["dimensions"].items()
        }
        self.tables: dict[str, dict] = raw.get("tables", {})
        self.fingerprint = hashlib.sha256(yaml.safe_dump(raw, sort_keys=True).encode()).hexdigest()[:12]

    # -- metric expansion ---------------------------------------------------------
    def expand(self, key: str, _depth: int = 0) -> str:
        if _depth > 8:
            raise ValueError(f"metric reference cycle at {key}")
        m = self.metrics[key]
        return re.sub(r"\{(\w+)\}", lambda g: _wrap(self.expand(g.group(1), _depth + 1)), m.sql)

    def metric_requires(self, key: str, _depth: int = 0) -> set[str]:
        m = self.metrics[key]
        req = set(m.requires)
        for ref in re.findall(r"\{(\w+)\}", m.sql):
            req |= self.metric_requires(ref, _depth + 1)
        for alias in re.findall(r"\b([a-z]{1,3})\.\w+", self.expand(key)):
            if alias in self.joins or alias == self.fact_alias:
                req.add(alias)
        return req

    def describe(self) -> dict:
        return {
            "catalog_version": self.version,
            "fingerprint": self.fingerprint,
            "changelog": self.changelog,
            "metrics": [
                {"key": m.key, "label": m.label, "version": m.version, "description": m.description,
                 "format": m.format, "sql": self.expand(m.key), "synonyms": m.synonyms}
                for m in self.metrics.values()
            ],
            "dimensions": [
                {"key": d.key, "label": d.label, "sql": d.sql, "time_grain": d.time_grain, "synonyms": d.synonyms}
                for d in self.dimensions.values()
            ],
        }


def _single_call(sql: str) -> bool:
    """True if `sql` is one function call like SUM(...) spanning the whole string."""
    m = re.match(r"^[A-Za-z_]+\(", sql)
    if not m or not sql.endswith(")"):
        return False
    depth = 0
    for i, ch in enumerate(sql):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0 and i < len(sql) - 1 and i >= m.end() - 1:
            return False
    return True


def _wrap(sql: str) -> str:
    return sql if _single_call(sql) else f"({sql})"


def load_catalog(path: Path) -> Catalog:
    with open(path) as fh:
        return Catalog(yaml.safe_load(fh), source=path)


@lru_cache
def get_catalog() -> Catalog:
    return load_catalog(get_settings().catalog_path)
