import pytest

from querypilot.safety.policy import get_policy
from querypilot.safety.validator import SQLRejected, validate_sql

POL = get_policy()
ANALYST = POL.role("analyst")
CFO = POL.role("cfo")


@pytest.mark.parametrize("sql,category", [
    ("DROP TABLE sales.orders", "not_select"),
    ("DELETE FROM sales.orders", "not_select"),
    ("UPDATE sales.orders SET status = 'x'", "not_select"),
    ("INSERT INTO sales.regions VALUES (1, 1, 'a', 'b')", "not_select"),
    ("select 1 from sales.orders; drop table sales.orders", "multi_statement"),
    ("SELECT * INTO sales.x FROM sales.orders", "not_select"),
    ("SELECT * FROM sales.orders FOR UPDATE", "not_select"),
    ("SELECT email FROM sales.customers", "pii"),
    ("SELECT c.phone FROM sales.customers c", "pii"),
    ("SELECT * FROM sales.customers", "pii"),
    ("SELECT x.e FROM (SELECT email AS e FROM sales.customers) x", "pii"),
    ("SELECT title FROM sales.employees", "unauthorized_table"),
    ("SELECT * FROM pg_catalog.pg_user", "forbidden_table"),
    ("SELECT * FROM information_schema.tables", "forbidden_table"),
    ("SELECT * FROM qp_ops.audit_log", "forbidden_table"),
    ("SELECT pg_sleep(10) FROM sales.orders", "forbidden_function"),
    ("SELECT set_config('app.tenant_id', '2', false) FROM sales.orders", "forbidden_function"),
    ("SELECT count(*) FROM sales.orders WHERE tenant_id = 2", "cross_tenant"),
    ("SELECT count(*) FROM orders_archive", "unknown_table"),
    ("SELEC count(*) FROM sales.orders", "syntax"),
])
def test_rejects(sql, category):
    with pytest.raises(SQLRejected) as e:
        validate_sql(sql, ANALYST, POL, 1000)
    assert e.value.category == category, (sql, e.value.category, str(e.value))


def test_cfo_can_count_staff_but_not_salary():
    v = validate_sql("SELECT title, count(*) FROM sales.employees GROUP BY 1", CFO, POL, 1000)
    assert "sales.employees" in v.tables
    with pytest.raises(SQLRejected):
        validate_sql("SELECT avg(salary) FROM sales.employees", CFO, POL, 1000)


def test_limit_injected_and_capped():
    v = validate_sql("SELECT order_id FROM sales.orders", ANALYST, POL, 1000)
    assert "LIMIT 501" in v.sql  # analyst cap 500 (+1 to detect truncation)
    v = validate_sql("SELECT order_id FROM sales.orders LIMIT 10", ANALYST, POL, 1000)
    assert "LIMIT 10" in v.sql
    v = validate_sql("SELECT order_id FROM sales.orders UNION SELECT order_id FROM sales.order_items", ANALYST, POL, 1000)
    assert v.sql.rstrip().endswith("LIMIT 501")


def test_unqualified_tables_are_qualified():
    v = validate_sql("SELECT count(*) FROM orders o JOIN order_items i ON i.order_id = o.order_id", ANALYST, POL, 1000)
    assert "sales.orders" in v.sql and "sales.order_items" in v.sql


def test_benign_aggregate_with_cte_passes():
    sql = ("WITH t AS (SELECT o.channel, sum(i.quantity) q FROM sales.orders o JOIN sales.order_items i "
           "ON i.order_id = o.order_id GROUP BY 1) SELECT * FROM t ORDER BY q DESC")
    v = validate_sql(sql, ANALYST, POL, 1000)
    assert all(c.passed for c in v.checks)


def test_formatter_changes_whitespace_only():
    import sqlglot

    from querypilot.safety.sqlformat import format_sql
    tricky = ("WITH a AS (SELECT o.channel, count(*) AS n FROM sales.orders o WHERE o.channel IN ('x), (y', 'SELECT , FROM') "
              "AND o.status <> 'z' GROUP BY 1) SELECT channel, n FROM a ORDER BY n DESC LIMIT 5")
    one = sqlglot.parse_one(tricky, read="postgres").sql(dialect="postgres")
    tok = lambda s: [t.text for t in sqlglot.Dialect.get_or_raise("postgres").tokenize(s)]  # noqa: E731
    out = format_sql(one)
    assert tok(out) == tok(one) and "'x), (y'" in out and "\n" in out
