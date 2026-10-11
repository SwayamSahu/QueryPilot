from datetime import date

import sqlglot

from querypilot.core.models import Filter, QueryPlan
from querypilot.semantic import timeparse as tp
from querypilot.semantic.catalog import get_catalog
from querypilot.semantic.compiler import PlanError, compile_plan
from querypilot.semantic.plan_builder import assemble
from querypilot.semantic.rules_planner import RulePlanner

CAT = get_catalog()
RP = RulePlanner(CAT)
TODAY = date(2026, 10, 10)
BOUNDS = (date(2024, 1, 1), date(2026, 10, 1))
VALUES = {"region": ["North", "South", "East", "West", "APAC", "EMEA"],
          "category": ["Electronics", "Clothing", "Home & Garden", "Food & Beverage", "Sports"],
          "channel": ["Online", "Retail", "Partner"], "segment": ["Enterprise", "SMB", "Consumer"]}


def plan(q, prev=None):
    r = RP.parse(q, TODAY, VALUES, prev)
    assert r.route == "semantic", (q, r.route)
    return assemble(r.draft, CAT, TODAY, BOUNDS)


def test_cfo_question():
    p = plan("Which regions had declining gross margins last quarter, and what changed compared with the previous quarter?")
    assert p.metrics == ["gross_margin"] and p.dimensions == ["region"]
    assert p.time.label == "Q3 2026" and p.compare_to.label == "Q2 2026"
    assert p.change_filter == "declining" and p.drivers


def test_metric_expansion_is_governed():
    sql = CAT.expand("gross_margin")
    assert "unit_cost" in sql and "discount" in sql and "NULLIF" in sql


def test_filters_values_and_exclusion():
    p = plan("Revenue by channel excluding Partner for the West region in 2025")
    f = {x.dimension: x for x in p.filters}
    assert f["channel"].op == "not_in" and f["channel"].values == ["Partner"]
    assert f["region"].values == ["West"]


def test_top_n_and_superlative():
    p = plan("Top 3 product categories by gross profit in 2025")
    assert p.limit == 3 and p.order_by == "gross_profit" and p.order_dir == "desc"
    p = plan("Which channel had the lowest revenue in 2024?")
    assert p.limit == 1 and p.order_dir == "asc"


def test_followups_merge():
    p1 = plan("Revenue by region last quarter")
    p2 = plan("what about gross margin?", p1)
    assert p2.metrics == ["gross_margin"] and p2.dimensions == ["region"] and p2.time.label == "Q3 2026"
    p3 = plan("only for APAC and EMEA", p2)
    assert p3.filters[0].values == ["APAC", "EMEA"]
    p4 = plan("all regions", p3)
    assert p4.filters == []


def test_compiled_sql_parses_for_all_shapes():
    t = tp.quarter(2026, 3)
    shapes = [
        QueryPlan(metrics=["revenue"], time=t),
        QueryPlan(metrics=["revenue", "gross_margin"], dimensions=["region", "category"], time=t,
                  filters=[Filter(dimension="channel", values=["Online"])]),
        QueryPlan(metrics=["return_rate"], dimensions=["month"], time=t),
        QueryPlan(metrics=["gross_margin"], dimensions=["region"], time=t, comparison="previous_period",
                  compare_to=tp.quarter(2026, 2), change_filter="declining"),
    ]
    for p in shapes:
        sql = compile_plan(p, CAT).sql
        assert sqlglot.parse_one(sql, read="postgres") is not None


def test_unknown_metric_rejected():
    try:
        compile_plan(QueryPlan(metrics=["ebitda"], time=tp.quarter(2026, 3)), CAT)
    except PlanError as e:
        assert "ebitda" in str(e)
    else:
        raise AssertionError("expected PlanError")


def test_low_coverage_lowers_confidence():
    r = RP.parse("revenue by flux capacitor wobble index", TODAY, VALUES)
    assert r.confidence < 0.8 and "flux" in r.unknown_terms
