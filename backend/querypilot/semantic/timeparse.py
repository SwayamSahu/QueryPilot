"""Deterministic resolution of relative / absolute time expressions.

LLMs are bad at calendar arithmetic, so neither planner computes dates itself: both emit
a time *expression* and this module turns it into a concrete [start, end) range relative
to a reference date. Every resolution carries a human-readable label that is shown to the
user as part of the answer's stated assumptions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from querypilot.core.models import TimeSpec

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "last": 4}
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "eighteen": 18, "twenty-four": 24}
NUM = r"(\d{1,3}|" + "|".join(WORDNUM) + r")"


def _n(tok: str) -> int:
    return int(tok) if tok.isdigit() else WORDNUM[tok]


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    return date(y, m % 12 + 1, 1)


def quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def q_label(start: date) -> str:
    return f"Q{(start.month - 1) // 3 + 1} {start.year}"


def m_label(start: date) -> str:
    return start.strftime("%b %Y")


def quarter(y: int, q: int) -> TimeSpec:
    s = date(y, 3 * (q - 1) + 1, 1)
    return TimeSpec(start=s, end=add_months(s, 3), label=q_label(s), grain="quarter")


def month(y: int, m: int) -> TimeSpec:
    s = date(y, m, 1)
    return TimeSpec(start=s, end=add_months(s, 1), label=m_label(s), grain="month")


def year(y: int) -> TimeSpec:
    return TimeSpec(start=date(y, 1, 1), end=date(y + 1, 1, 1), label=str(y), grain="year")


def half(y: int, h: int) -> TimeSpec:
    s = date(y, 1 if h == 1 else 7, 1)
    return TimeSpec(start=s, end=add_months(s, 6), label=f"H{h} {y}", grain="half")


def months_range(s: date, e_excl: date) -> TimeSpec:
    last = add_months(e_excl, -1)
    if s.year == last.year:
        label = f"{s.strftime('%b')}–{last.strftime('%b %Y')}" if s != last else m_label(s)
    else:
        label = f"{m_label(s)}–{m_label(last)}"
    return TimeSpec(start=s, end=e_excl, label=label, grain="month" if add_months(s, 1) == e_excl else "custom")


def previous_period(t: TimeSpec) -> TimeSpec:
    if t.grain == "quarter":
        s = add_months(t.start, -3)
        return TimeSpec(start=s, end=t.start, label=q_label(s), grain="quarter")
    if t.grain == "month":
        s = add_months(t.start, -1)
        return TimeSpec(start=s, end=t.start, label=m_label(s), grain="month")
    if t.grain == "year":
        return year(t.start.year - 1)
    if t.grain == "half":
        return half(t.start.year if t.start.month == 7 else t.start.year - 1, 1 if t.start.month == 7 else 2)
    # month-aligned custom ranges shift by whole months; others by day count
    if t.start.day == 1 and t.end.day == 1:
        n = (t.end.year - t.start.year) * 12 + t.end.month - t.start.month
        return months_range(add_months(t.start, -n), t.start)
    days = (t.end - t.start).days
    s = t.start - timedelta(days=days)
    return TimeSpec(start=s, end=t.start, label=f"{s.isoformat()} to {(t.start - timedelta(days=1)).isoformat()}", grain="custom")


def _shift_year(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + n, day=28)


def same_period_last_year(t: TimeSpec) -> TimeSpec:
    s, e = _shift_year(t.start, -1), _shift_year(t.end, -1)
    label = {
        "quarter": q_label(s), "month": m_label(s), "year": str(s.year),
        "half": f"H{1 if s.month == 1 else 2} {s.year}",
    }.get(t.grain, f"{s.isoformat()} to {(e - timedelta(days=1)).isoformat()}")
    return TimeSpec(start=s, end=e, label=label, grain=t.grain)


@dataclass
class TimeMatch:
    spec: TimeSpec
    span: tuple[int, int]
    assumption: str | None = None


def _most_recent_complete(month_or_q: str, n: int, today: date) -> tuple[int, str | None]:
    """For 'Q2' or 'March' without a year: pick the most recent complete occurrence."""
    if month_or_q == "q":
        y = today.year
        if quarter(y, n).end > today:
            y -= 1
        return y, f"'Q{n}' without a year read as Q{n} {y} (most recent complete)"
    y = today.year
    if month(y, n).end > today:
        y -= 1
    name = date(2000, n, 1).strftime("%B")
    return y, f"'{name}' without a year read as {name} {y} (most recent complete)"


def resolve(text: str, today: date) -> TimeMatch | None:
    """Find the first (left-most, longest) time expression in `text` and resolve it."""
    t = text.lower()
    cands: list[TimeMatch] = []

    def add(m: re.Match, spec: TimeSpec, assumption: str | None = None):
        cands.append(TimeMatch(spec, m.span(), assumption))

    cur_q = quarter_start(today)
    cur_m = date(today.year, today.month, 1)

    for m in re.finditer(r"\b(?:last|previous|prior|most recent|latest|past|recent)\s+(?:full\s+|complete\s+|completed\s+|fiscal\s+)?quarter\b", t):
        s = add_months(cur_q, -3)
        add(m, TimeSpec(start=s, end=cur_q, label=q_label(s), grain="quarter"))
    for m in re.finditer(r"\b(?:this|current)\s+quarter\b|\bquarter[- ]to[- ]date\b|\bqtd\b", t):
        add(m, TimeSpec(start=cur_q, end=today + timedelta(days=1), label=f"{q_label(cur_q)} to date", grain="custom"))
    for m in re.finditer(r"\b(?:last|previous|prior|most recent|latest|past)\s+(?:full\s+|complete\s+)?month\b", t):
        s = add_months(cur_m, -1)
        add(m, month(s.year, s.month))
    for m in re.finditer(r"\b(?:this|current)\s+month\b|\bmonth[- ]to[- ]date\b|\bmtd\b", t):
        add(m, TimeSpec(start=cur_m, end=today + timedelta(days=1), label=f"{m_label(cur_m)} to date", grain="custom"))
    for m in re.finditer(r"\b(?:last|previous|prior)\s+(?:full\s+|calendar\s+|fiscal\s+)?year\b", t):
        add(m, year(today.year - 1))
    for m in re.finditer(r"\b(?:this|current)\s+year\b|\byear[- ]to[- ]date\b|\bytd\b", t):
        add(m, TimeSpec(start=date(today.year, 1, 1), end=today + timedelta(days=1), label=f"{today.year} year to date", grain="custom"))
    for m in re.finditer(r"\b(?:last|past|previous|trailing|prior)\s+" + NUM + r"\s+(months?|quarters?|years?|weeks?|days?)\b", t):
        n, unit = _n(m.group(1)), m.group(2)
        if unit.startswith("month"):
            add(m, months_range(add_months(cur_m, -n), cur_m))
        elif unit.startswith("quarter"):
            add(m, months_range(add_months(cur_q, -3 * n), cur_q))
        elif unit.startswith("year"):
            add(m, months_range(add_months(cur_m, -12 * n), cur_m))
        else:
            days = n * (7 if unit.startswith("week") else 1)
            s = today - timedelta(days=days)
            add(m, TimeSpec(start=s, end=today, label=f"last {n} {unit}", grain="custom"))
    for m in re.finditer(r"\b(?:ltm|ttm|trailing twelve months)\b", t):
        add(m, months_range(add_months(cur_m, -12), cur_m))
    for m in re.finditer(r"\ball[- ]time\b|\ball available data\b|\bever\b|\bto date\b(?<!quarter to date)", t):
        add(m, TimeSpec(start=date(1900, 1, 1), end=today + timedelta(days=1), label="all available data", grain="all"))

    # Quarters: "Q3 2025", "2025 Q3", "Q3 of 2025", "Q3'25", "third quarter of 2025", bare "Q3"
    for m in re.finditer(r"\bq([1-4])\s*(?:of\s+|,\s*|')?(20\d\d|\d\d)?\b", t):
        q = int(m.group(1))
        if m.group(2):
            y = int(m.group(2)) if len(m.group(2)) == 4 else 2000 + int(m.group(2))
            add(m, quarter(y, q))
        else:
            y, why = _most_recent_complete("q", q, today)
            add(m, quarter(y, q), why)
    for m in re.finditer(r"\b(20\d\d)\s*[- ]?\s*q([1-4])\b", t):
        add(m, quarter(int(m.group(1)), int(m.group(2))))
    for m in re.finditer(r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\s+(?:of\s+)?(20\d\d)\b", t):
        add(m, quarter(int(m.group(2)), ORD[m.group(1)]))
    # Halves
    for m in re.finditer(r"\bh([12])\s*(?:of\s+)?(20\d\d)\b|\b(20\d\d)\s*h([12])\b", t):
        h = int(m.group(1) or m.group(4))
        y = int(m.group(2) or m.group(3))
        add(m, half(y, h))
    for m in re.finditer(r"\b(first|second)\s+half\s+(?:of\s+)?(20\d\d)\b", t):
        add(m, half(int(m.group(2)), 1 if m.group(1) == "first" else 2))
    # Month ranges: "between Jan and Mar 2026", "from January 2025 to June 2025"
    for m in re.finditer(r"\b(?:between|from)\s+(" + MONTH_RE + r")\.?\s*(20\d\d)?\s+(?:and|to|through|-)\s+(" + MONTH_RE + r")\.?\s*(20\d\d)\b", t):
        y2 = int(m.group(4))
        y1 = int(m.group(2)) if m.group(2) else y2
        s = date(y1, MONTHS[m.group(1)], 1)
        e = add_months(date(y2, MONTHS[m.group(3)], 1), 1)
        if s < e:
            add(m, months_range(s, e))
    # "March 2026", "Mar '26"
    for m in re.finditer(r"\b(" + MONTH_RE + r")\.?\s*(?:of\s+)?(20\d\d)\b", t):
        add(m, month(int(m.group(2)), MONTHS[m.group(1)]))
    # bare month "in March" (avoid 'may' the verb: require a preposition)
    for m in re.finditer(r"\b(?:in|during|for|of)\s+(" + MONTH_RE + r")\b(?!\s*\d)", t):
        name = m.group(1)
        if name == "may" and not re.search(r"\b(?:in|during|for)\s+may\b", t):
            continue
        y, why = _most_recent_complete("m", MONTHS[name], today)
        add(m, month(y, MONTHS[name]), why)
    # explicit dates
    for m in re.finditer(r"\b(20\d\d-\d\d-\d\d)\s*(?:to|through|until|and|-|–)\s*(20\d\d-\d\d-\d\d)\b", t):
        s, e = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
        add(m, TimeSpec(start=s, end=e + timedelta(days=1), label=f"{s.isoformat()} to {e.isoformat()}", grain="custom"))
    for m in re.finditer(r"\bsince\s+(" + MONTH_RE + r")\.?\s*(20\d\d)\b", t):
        s = date(int(m.group(2)), MONTHS[m.group(1)], 1)
        add(m, TimeSpec(start=s, end=today + timedelta(days=1), label=f"since {m_label(s)}", grain="custom"))
    for m in re.finditer(r"\bsince\s+(20\d\d)\b", t):
        s = date(int(m.group(1)), 1, 1)
        add(m, TimeSpec(start=s, end=today + timedelta(days=1), label=f"since {s.year}", grain="custom"))
    # Years: "2025", "FY2025", "in 2025" (only when not part of a richer match)
    for m in re.finditer(r"\b(?:fy\s?)?(20[1-3]\d)\b", t):
        add(m, year(int(m.group(1))))

    if not cands:
        return None
    # left-most wins, then longest
    cands.sort(key=lambda c: (c.span[0], -(c.span[1] - c.span[0])))
    best = cands[0]
    # a longer candidate that fully contains the best one wins (e.g. "Q3 of 2025" vs "2025")
    for c in cands:
        if c.span[0] <= best.span[0] and c.span[1] > best.span[1]:
            best = c
    return best


def resolve_all(text: str, today: date) -> list[TimeMatch]:
    """All non-overlapping time expressions, left to right."""
    out: list[TimeMatch] = []
    rest = text
    offset = 0
    while True:
        m = resolve(rest, today)
        if not m:
            return out
        out.append(TimeMatch(m.spec, (m.span[0] + offset, m.span[1] + offset), m.assumption))
        offset += m.span[1]
        rest = rest[m.span[1]:]


def clip(spec: TimeSpec, lo: date, hi_excl: date) -> TimeSpec:
    if spec.grain == "all":
        return TimeSpec(start=lo, end=hi_excl, label=f"all available data ({lo.isoformat()} to {(hi_excl - timedelta(days=1)).isoformat()})", grain="all")
    return spec
