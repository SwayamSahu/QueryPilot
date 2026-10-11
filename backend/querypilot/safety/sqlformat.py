"""Whitespace-only SQL layout for display *and* execution.

Only the whitespace between tokens changes; every token (string literals, identifiers,
operators) is copied byte-for-byte from the validated SQL. The readable text the user sees
is therefore exactly the statement that runs.
"""
from __future__ import annotations

from sqlglot.dialects.dialect import Dialect
from sqlglot.tokens import TokenType as T

CLAUSES = {T.SELECT, T.FROM, T.WHERE, T.GROUP_BY, T.ORDER_BY, T.LIMIT, T.HAVING, T.UNION, T.EXCEPT, T.INTERSECT, T.OFFSET}
JOIN_PREFIX = {T.LEFT, T.RIGHT, T.FULL, T.INNER, T.CROSS, T.OUTER, T.NATURAL}


def format_sql(sql: str) -> str:
    toks = Dialect.get_or_raise("postgres").tokenize(sql)
    out: list[str] = []
    depth = 0
    paren_is_query: list[bool] = []          # per open paren: does it wrap a subquery?
    clause_at: dict[int, object] = {}        # current clause per query depth
    with_preamble = bool(toks) and toks[0].token_type == T.WITH
    for i, t in enumerate(toks):
        tt = t.token_type
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1].token_type if i + 1 < len(toks) else None
        gap = sql[prev.end + 1:t.start] if prev else ""
        ind = "  " * depth
        brk = None
        if prev is None:
            brk = ""
        elif tt in CLAUSES:
            brk = "\n" + ind
        elif tt == T.JOIN and prev.token_type not in JOIN_PREFIX:
            brk = "\n" + ind
        elif tt in JOIN_PREFIX and prev.token_type not in JOIN_PREFIX and (nxt in (T.JOIN, T.OUTER) or tt == T.CROSS):
            brk = "\n" + ind
        elif tt == T.AND and clause_at.get(depth) in (T.WHERE, T.HAVING):
            brk = "\n" + ind + "  "
        elif tt == T.R_PAREN and paren_is_query and paren_is_query[-1]:
            brk = "\n" + "  " * (depth - 1)
        elif prev.token_type == T.COMMA and clause_at.get(depth) == T.SELECT:
            brk = "\n" + ind + "  "
        elif prev.token_type == T.COMMA and depth == 0 and with_preamble:
            brk = "\n"
        out.append(gap if brk is None else brk)
        out.append(sql[t.start:t.end + 1])
        if tt in CLAUSES:
            clause_at[depth] = tt
            if depth == 0 and tt == T.SELECT:
                with_preamble = False
        if tt == T.L_PAREN:
            paren_is_query.append(nxt in (T.SELECT, T.WITH))
            depth += 1
            clause_at.pop(depth, None)
        elif tt == T.R_PAREN and paren_is_query:
            paren_is_query.pop()
            clause_at.pop(depth, None)
            depth = max(0, depth - 1)
    return "".join(out).strip()
