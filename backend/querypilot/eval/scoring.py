"""Result-set comparison for text-to-SQL evaluation.

execution_match (strict EX): predicted and gold result sets are identical as multisets of
    rows, allowing columns in any order but no extra or missing columns (Spider-style EX).
result_match (lenient RA): every gold column is matched by some predicted column with the
    same values row-for-row; extra predicted columns (e.g. "previous", "change %") are allowed.
    This answers "did the user get the right numbers?" even when the SQL shape differs.
Ordering is enforced only when the question implies it (top-N, trends, rankings).
Numbers compare at 8 significant digits; text compares exactly.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any


def canon(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        return 0.0 if abs(f) < 1e-12 else float(f"{f:.8g}")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    s = str(v).strip()
    try:
        return float(f"{float(s):.8g}")
    except ValueError:
        return s


def _cols(rows: list[list]) -> list[tuple]:
    if not rows:
        return []
    return [tuple(canon(r[j]) for r in rows) for j in range(len(rows[0]))]


def _rows_equal(a: list[tuple], b: list[tuple], ordered: bool) -> bool:
    return a == b if ordered else Counter(a) == Counter(b)


def _match(gold: list[list], pred: list[list], ordered: bool, strict: bool) -> bool:
    if len(gold) != len(pred):
        return False
    if not gold:
        return True
    gcols, pcols = _cols(gold), _cols(pred)
    if strict and len(gcols) != len(pcols):
        return False
    # candidate predicted columns per gold column: same multiset of values
    cands = []
    for gc in gcols:
        gcnt = Counter(gc)
        cs = [k for k, pc in enumerate(pcols) if Counter(pc) == gcnt]
        if not cs:
            return False
        cands.append(cs)
    # search an injective assignment whose projected rows equal the gold rows
    def search(i: int, used: list[int]) -> bool:
        if i == len(cands):
            proj = [tuple(canon(r[k]) for k in used) for r in pred]
            grows = [tuple(canon(x) for x in r) for r in gold]
            return _rows_equal(grows, proj, ordered)
        for k in cands[i]:
            if k not in used and search(i + 1, used + [k]):
                return True
        return False

    return search(0, [])


def execution_match(gold, pred, ordered=False) -> bool:
    return _match(gold, pred, ordered, strict=True)


def result_match(gold, pred, ordered=False) -> bool:
    return _match(gold, pred, ordered, strict=False)

