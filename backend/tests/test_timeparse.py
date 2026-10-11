from datetime import date

import pytest

from querypilot.semantic import timeparse as tp

TODAY = date(2026, 10, 10)


@pytest.mark.parametrize("text,start,end", [
    ("last quarter", "2026-07-01", "2026-10-01"),
    ("in Q2 2026", "2026-04-01", "2026-07-01"),
    ("2025 Q4", "2025-10-01", "2026-01-01"),
    ("the second quarter of 2025", "2025-04-01", "2025-07-01"),
    ("last year", "2025-01-01", "2026-01-01"),
    ("in 2024", "2024-01-01", "2025-01-01"),
    ("March 2026", "2026-03-01", "2026-04-01"),
    ("last month", "2026-09-01", "2026-10-01"),
    ("the last 6 months", "2026-04-01", "2026-10-01"),
    ("H1 2026", "2026-01-01", "2026-07-01"),
    ("the first half of 2025", "2025-01-01", "2025-07-01"),
    ("between Jan and Mar 2026", "2026-01-01", "2026-04-01"),
])
def test_resolve(text, start, end):
    m = tp.resolve(text, TODAY)
    assert m is not None, text
    assert (m.spec.start.isoformat(), m.spec.end.isoformat()) == (start, end)


def test_bare_quarter_picks_most_recent_complete():
    m = tp.resolve("in Q3", TODAY)
    assert m.spec.label == "Q3 2026" and m.assumption
    m = tp.resolve("in Q4", TODAY)
    assert m.spec.label == "Q4 2025"


def test_previous_and_yoy():
    q3 = tp.quarter(2026, 3)
    assert tp.previous_period(q3).label == "Q2 2026"
    assert tp.previous_period(tp.quarter(2026, 1)).label == "Q4 2025"
    assert tp.same_period_last_year(q3).label == "Q3 2025"
    assert tp.previous_period(tp.half(2026, 1)).label == "H2 2025"
