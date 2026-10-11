"""Static SQL validation: every statement is parsed and checked before it may execute.

Defence in depth - even if this layer had a bug, the database role is read-only,
column-restricted and row-level-security scoped. But this layer gives precise, explainable
reasons (shown to the user and fed back to the repair loop) instead of raw DB errors.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from querypilot.core.models import Check
from querypilot.safety.policy import Policy, RolePolicy
from querypilot.safety.sqlformat import format_sql

MAX_SQL_CHARS = 20_000
FORBIDDEN_NODES = tuple(getattr(exp, n) for n in [
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Merge", "Command", "Copy", "TruncateTable", "Grant",
    "Revoke", "Set", "Transaction", "Commit", "Rollback", "Into", "Lock", "Use", "Pragma", "Analyze", "Describe",
    "Kill", "LoadData", "Cache", "Uncache", "Refresh",
] if hasattr(exp, n))
FORBIDDEN_SCHEMAS = {"pg_catalog", "information_schema", "qp_ops", "public", "pg_toast"}
FORBIDDEN_PREFIXES = ("pg_", "lo_", "dblink", "xpath", "query_to_", "table_to_", "database_to_", "schema_to_")


class SQLRejected(Exception):
    def __init__(self, message: str, category: str, checks: list[Check]):
        super().__init__(message)
        self.category = category
        self.checks = checks


@dataclass
class ValidatedSQL:
    sql: str
    tables: set[str]
    checks: list[Check] = field(default_factory=list)
    limit_applied: int | None = None


def _fail(checks: list[Check], name: str, detail: str, category: str):
    checks.append(Check(name=name, passed=False, detail=detail, severity="error"))
    raise SQLRejected(detail, category, checks)


def validate_sql(sql: str, role: RolePolicy, policy: Policy, max_rows: int) -> ValidatedSQL:
    checks: list[Check] = []
    if len(sql) > MAX_SQL_CHARS:
        _fail(checks, "size", f"SQL longer than {MAX_SQL_CHARS} characters", "size")
    try:
        stmts = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as e:
        _fail(checks, "parse", f"SQL does not parse: {str(e).splitlines()[0][:300]}", "syntax")
    if len(stmts) != 1:
        _fail(checks, "single_statement", f"exactly one statement allowed, got {len(stmts)}", "multi_statement")
    checks.append(Check(name="single_statement", passed=True, detail="one statement"))
    root = stmts[0]
    while isinstance(root, exp.Subquery) and isinstance(root.this, (exp.Select, exp.SetOperation)):
        root = root.this
    if not isinstance(root, (exp.Select, exp.SetOperation)):
        _fail(checks, "read_only", f"only SELECT queries are allowed (got {type(root).__name__.upper()})", "not_select")
    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            _fail(checks, "read_only", f"{type(node).__name__.upper()} is not allowed in a read-only query", "not_select")
        if isinstance(node, exp.Select) and node.args.get("locks"):
            _fail(checks, "read_only", "row locking clauses (FOR UPDATE/SHARE) are not allowed", "not_select")
    checks.append(Check(name="read_only", passed=True, detail="SELECT only; no DML/DDL/locking"))

    for fn in root.find_all(exp.Func):
        name = (fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()).lower()
        if name in policy.function_denylist or name.startswith(FORBIDDEN_PREFIXES):
            _fail(checks, "functions", f"function {name}() is not allowed", "forbidden_function")
    checks.append(Check(name="functions", passed=True, detail="no denylisted functions"))

    # ---- tables
    cte_names = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    tables: set[str] = set()
    alias_to_table: dict[str, str] = {}
    for t in root.find_all(exp.Table):
        name = t.name.lower()
        schema = (t.db or "").lower()
        if not schema and name in cte_names:
            continue
        if not name:  # table-valued function etc.
            _fail(checks, "tables", "table functions are not allowed", "forbidden_table")
        if schema in FORBIDDEN_SCHEMAS or (t.catalog and t.catalog.lower() not in ("", "querypilot")):
            _fail(checks, "tables", f"schema '{schema or t.catalog}' is not accessible", "forbidden_table")
        if not schema:
            if f"sales.{name}" in role.tables or f"sales.{name}" in policy.restricted_columns:
                t.set("db", exp.to_identifier("sales"))
                schema = "sales"
            else:
                _fail(checks, "tables", f"unknown table '{name}' (use schema-qualified sales.* tables)", "unknown_table")
        full = f"{schema}.{name}"
        if full not in role.tables:
            _fail(checks, "tables", f"table {full} is not available to the '{role.name}' role", "unauthorized_table")
        tables.add(full)
        alias_to_table[(t.alias or name).lower()] = full
    if not tables:
        _fail(checks, "tables", "query must read from at least one sales table", "no_table")
    checks.append(Check(name="tables", passed=True, detail="allowed for role: " + ", ".join(sorted(tables))))

    # ---- columns: restricted columns, star expansion over restricted tables, tenant overrides
    restricted_by_table = {t: cols for t, cols in policy.restricted_columns.items() if t in tables}
    all_restricted = {c for cols in restricted_by_table.values() for c in cols}
    for col in root.find_all(exp.Column):
        cname = col.name.lower()
        if isinstance(col.this, exp.Star):
            owner = alias_to_table.get(col.table.lower()) if col.table else None
            if (owner and owner in restricted_by_table) or (not owner and restricted_by_table):
                _fail(checks, "columns", "SELECT * over a table with restricted columns; list columns explicitly", "pii")
            continue
        owner = alias_to_table.get(col.table.lower()) if col.table else None
        if (owner and cname in policy.restricted_columns.get(owner, set())) or (not owner and cname in all_restricted):
            _fail(checks, "columns", f"column {cname} is restricted (PII/confidential)", "pii")
    for star in root.find_all(exp.Star):
        if isinstance(star.parent, exp.Select) and restricted_by_table:
            _fail(checks, "columns", "SELECT * over a table with restricted columns; list columns explicitly", "pii")
    for pred in root.find_all(exp.EQ, exp.NEQ, exp.In, exp.GT, exp.LT, exp.GTE, exp.LTE):
        cols = [c for c in pred.find_all(exp.Column) if c.name.lower() == "tenant_id"]
        if cols and any(isinstance(x, exp.Literal) for x in pred.find_all(exp.Literal)):
            _fail(checks, "tenant_scope", "filtering on tenant_id is not allowed; tenant scope is enforced by the platform",
                  "cross_tenant")
    checks.append(Check(name="columns", passed=True, detail="no restricted columns; tenant scope untouched"))

    # ---- row limit
    limit_applied = None
    cap = min(max_rows, role.max_rows)
    if isinstance(root, exp.Select):
        lim = root.args.get("limit")
        if lim is None:
            root.set("limit", exp.Limit(expression=exp.Literal.number(cap + 1)))
            limit_applied = cap
        else:
            val = lim.expression
            if not (isinstance(val, exp.Literal) and val.is_int):
                _fail(checks, "row_limit", "LIMIT must be a literal integer", "limit")
            if int(val.this) > cap:
                root.set("limit", exp.Limit(expression=exp.Literal.number(cap + 1)))
                limit_applied = cap
        final = format_sql(root.sql(dialect="postgres"))
    else:
        final = format_sql(f"SELECT * FROM ({root.sql(dialect='postgres')}) AS capped LIMIT {cap + 1}")
        limit_applied = cap
    checks.append(Check(name="row_limit", passed=True, detail=f"at most {cap} rows returned"))
    return ValidatedSQL(sql=final, tables=tables, checks=checks, limit_applied=limit_applied)
