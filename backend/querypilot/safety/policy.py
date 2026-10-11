"""Role-based access policy and the natural-language intent guard."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from querypilot.config import get_settings
from querypilot.core.models import Principal, QueryPlan


@dataclass
class RolePolicy:
    name: str
    tables: set[str]
    region_scope: str
    max_rows: int


@dataclass
class Policy:
    version: str
    restricted_columns: dict[str, set[str]]
    restricted_terms: dict[str, str]
    function_denylist: set[str]
    roles: dict[str, RolePolicy]
    demo_users: dict[str, dict] = field(default_factory=dict)

    def role(self, name: str) -> RolePolicy:
        if name not in self.roles:
            raise PermissionError(f"unknown role {name}")
        return self.roles[name]

    def principal(self, user_id: str) -> Principal:
        u = self.demo_users[user_id]
        return Principal(user_id=user_id, name=u["name"], title=u.get("title", ""), tenant_id=u["tenant_id"],
                         tenant=u.get("tenant", ""), role=u["role"], regions=u.get("regions", []))


def load_policy(path: Path) -> Policy:
    raw = yaml.safe_load(path.read_text())
    return Policy(
        version=str(raw["policy_version"]),
        restricted_columns={t: set(c) for t, c in raw["restricted_columns"].items()},
        restricted_terms=raw["restricted_terms"],
        function_denylist={f.lower() for f in raw["allowed_functions_denylist"]},
        roles={k: RolePolicy(name=k, tables=set(v["tables"]), region_scope=v["region_scope"], max_rows=int(v["max_rows"]))
               for k, v in raw["roles"].items()},
        demo_users=raw.get("demo_users", {}),
    )


@lru_cache
def get_policy() -> Policy:
    return load_policy(get_settings().policies_path)


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    category: str = ""  # pii | destructive | injection | cross_tenant | unauthorized_region | unauthorized_table


DESTRUCTIVE = re.compile(
    r"\b(?:delete|truncate|wipe|erase|overwrite|purge)\b.{0,40}\b(?:table|tables|rows?|records?|data|database|schema|"
    r"orders|customers|products|regions|returns|sales|everything|all)\b|"
    r"\bdrop\s+(?:the\s+)?(?:\w+\s+)?(?:table|database|schema|index|view|role|user)\b|"
    r"\bupdate\s+(?:the\s+)?(?:\w+\s+)?(?:table|rows?|records?|set)\b|"
    r"\binsert\s+(?:into|a|new|rows?|records?)\b|\b(?:alter|grant|revoke)\s+(?:table|role|user|all|select|access)\b|"
    r"\b(?:create|add)\s+(?:a\s+)?(?:new\s+)?(?:table|user|role|index|view|rows?|records?)\b|"
    r"\b(?:set|change|modify|edit)\s+(?:the\s+)?(?:price|discount|status|cost)s?\s+(?:to|of)\b|"
    r"\bmark\b.{0,30}\bas\s+(?:cancelled|completed|pending)\b", re.I)
INJECTION = re.compile(
    r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier|system|safety|security)?\s*"
    r"(?:instructions?|rules?|prompts?|guardrails?|policies|restrictions?)\b|\byou are now\b|\bsystem prompt\b|\bdeveloper mode\b|"
    r"\bjailbreak\b|\bdisable\s+(?:the\s+)?(?:security|safety|rls|row[- ]level security|guard\w*|validator|filters?)\b|"
    r"\bbypass\s+(?:the\s+)?\w+|\bact as (?:a |an )?(?:dba|admin|superuser|root)\b|\bsudo\b|\bas (?:a )?superuser\b|"
    r"\bpg_\w+|\binformation_schema\b|\bpg_catalog\b|\bqp_ops\b|\baudit log\b", re.I)
CROSS_TENANT = re.compile(
    r"\b(?:other|another|all|every|different)\s+(?:tenants?|companies|company's|organi[sz]ations?|customers' companies)\b|"
    r"\btenant[_\s]*(?:id)?\s*(?:=|is|of|:)?\s*\d+\b|\bacross (?:all )?tenants\b|\bcompetitor'?s?\b", re.I)


def guard_question(question: str, principal: Principal, tenant_names: dict[int, str]) -> GuardResult:
    pol = get_policy()
    q = question.lower()
    for term, what in sorted(pol.restricted_terms.items(), key=lambda kv: -len(kv[0])):
        if re.search(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", q):
            return GuardResult(False, f"{what[0].upper() + what[1:]} are restricted by data policy {pol.version} and can't be "
                                      "queried through QueryPilot.", "pii")
    if INJECTION.search(question):
        return GuardResult(False, "This request tries to change how QueryPilot's safeguards work or reach system tables. "
                                  "That isn't allowed; ask a business question instead.", "injection")
    if DESTRUCTIVE.search(question):
        return GuardResult(False, "QueryPilot is read-only: it can analyse data but never modify, delete or create it.",
                           "destructive")
    if CROSS_TENANT.search(question):
        return GuardResult(False, "Each workspace can only see its own organisation's data. Cross-tenant questions are "
                                  "blocked.", "cross_tenant")
    for tid, name in tenant_names.items():
        if tid != principal.tenant_id and re.search(r"\b" + re.escape(name.lower().split()[0]) + r"\b", q):
            return GuardResult(False, "Each workspace can only see its own organisation's data. Cross-tenant questions are "
                                      "blocked.", "cross_tenant")
    return GuardResult(True)


def authorize_plan(plan: QueryPlan, principal: Principal) -> GuardResult:
    """Plan-level authorisation (region scope). RLS enforces the same rule in the database."""
    if principal.regions:
        allowed = {r.lower() for r in principal.regions}
        for f in plan.filters:
            if f.dimension == "region" and f.op == "in":
                bad = [v for v in f.values if v.lower() not in allowed]
                if bad:
                    return GuardResult(False, f"You're authorised for {', '.join(principal.regions)} only; "
                                              f"{', '.join(bad)} is outside your access scope.", "unauthorized_region")
    return GuardResult(True)
