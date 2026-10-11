"""Deterministic synthetic enterprise sales dataset.

Two tenants (Acme Retail, Globex Corp), Jan 2024 - Sep 2026, ~65k orders / ~160k order lines.
Known, documented business events are injected so analytical questions have real answers
(see docs/DATASET.md):

  * Q3 2026  West  : discount rate up ~8pp across all channels   -> margin falls
  * Q3 2026  APAC  : Electronics unit cost up ~18% (supplier)      -> margin falls
  * Q3 2026  EMEA  : unit costs down ~3%                           -> margin improves
  * Q2 2026  South : mix shift towards low-margin Food & Beverage  -> margin falls vs Q1
  * Nov-Dec 2025   : holiday promotion, +3pp discount everywhere

Run:  python -m querypilot.data.seed            (idempotent: truncates and reloads)
"""
from __future__ import annotations

import argparse
import calendar
import io
import math
from datetime import date, timedelta

import numpy as np
import psycopg

from querypilot.config import get_settings

SEED = 20261010
START = date(2024, 1, 1)
END = date(2026, 9, 30)

CATEGORIES = {
    # category: (cost ratio of list price, price range, product names)
    "Electronics": (0.68, (80, 1200), ["Aero Laptop 14", "Aero Laptop 16", "Pulse Phone X", "Pulse Phone Mini",
                                        "Echo Earbuds", "Vista Monitor 27", "Nimbus Tablet", "Volt Power Bank"]),
    "Clothing": (0.45, (20, 150), ["Trail Jacket", "Everyday Tee", "Merino Sweater", "Denim Classic",
                                   "Rain Shell", "Linen Shirt", "Wool Socks 3pk", "City Sneakers"]),
    "Home & Garden": (0.55, (25, 400), ["Cedar Planter", "Smart Thermostat", "Chef Knife Set", "Air Purifier",
                                        "Garden Hose Pro", "Ceramic Cookware", "LED Floor Lamp", "Bamboo Sheets"]),
    "Food & Beverage": (0.72, (5, 60), ["Single Origin Coffee", "Green Tea Tin", "Protein Bars 12pk",
                                        "Olive Oil 1L", "Sparkling Water 24pk", "Dark Chocolate Box",
                                        "Trail Mix Bulk", "Cold Brew Kit"]),
    "Sports": (0.52, (15, 300), ["Yoga Mat Pro", "Carbon Bike Helmet", "Running Vest", "Kettlebell 12kg",
                                 "Tennis Racket", "Hiking Poles", "Swim Goggles", "Resistance Bands"]),
}

TENANTS = [
    {
        "tenant_id": 1, "name": "Acme Retail", "orders_per_month": 1500, "customers": 2000,
        "regions": [(1, "North", "United States"), (2, "South", "United States"), (3, "East", "United States"),
                    (4, "West", "United States"), (5, "APAC", "Singapore"), (6, "EMEA", "Germany")],
        "region_weights": [0.18, 0.16, 0.20, 0.17, 0.14, 0.15],
    },
    {
        "tenant_id": 2, "name": "Globex Corp", "orders_per_month": 450, "customers": 600,
        # Region "West" deliberately exists in both tenants so leakage would be visible.
        "regions": [(7, "West", "United States"), (8, "Europe", "France"), (9, "LATAM", "Brazil")],
        "region_weights": [0.45, 0.35, 0.20],
    },
]

SEGMENTS = ["Enterprise", "SMB", "Consumer"]
SEGMENT_P = [0.15, 0.35, 0.50]
CHANNELS = ["Online", "Retail", "Partner"]
CHANNEL_DISCOUNT = {"Online": 0.04, "Retail": 0.02, "Partner": 0.09}
SEGMENT_CHANNEL_P = {"Enterprise": [0.35, 0.10, 0.55], "SMB": [0.55, 0.20, 0.25], "Consumer": [0.65, 0.33, 0.02]}
SEGMENT_QTY = {"Enterprise": 12, "SMB": 5, "Consumer": 1.6}
FIRST = ["Alex", "Priya", "Chen", "Maria", "Omar", "Sara", "Kenji", "Lena", "Ravi", "Noah", "Ana", "Tom"]
LAST = ["Shah", "Garcia", "Kim", "Okafor", "Novak", "Rossi", "Iyer", "Berg", "Silva", "Lopez", "Wong", "Meyer"]
COMPANY_A = ["Blue", "North", "Bright", "Iron", "Silver", "Quantum", "Green", "Swift", "Prime", "Urban"]
COMPANY_B = ["Labs", "Works", "Retail", "Foods", "Systems", "Partners", "Goods", "Studio", "Supply", "Group"]


def month_iter():
    y, m = START.year, START.month
    while date(y, m, 1) <= END:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def quarter_of(d: date) -> tuple[int, int]:
    return d.year, (d.month - 1) // 3 + 1


def build(rng: np.random.Generator):
    rows = {k: [] for k in ["tenants", "regions", "products", "customers", "orders", "order_items", "returns", "employees"]}
    product_id = 100
    customer_id = 10_000
    order_id = 1_000_000
    item_id = 5_000_000
    return_id = 9_000_000
    employee_id = 500

    for t in TENANTS:
        tid = t["tenant_id"]
        rows["tenants"].append((tid, t["name"]))
        region_ids = [r[0] for r in t["regions"]]
        region_name = {r[0]: r[1] for r in t["regions"]}
        for r in t["regions"]:
            rows["regions"].append((r[0], tid, r[1], r[2]))

        # products
        prods = []  # (id, category, list_price, std_cost)
        for cat, (ratio, (lo, hi), names) in CATEGORIES.items():
            for name in names:
                lp = round(float(math.exp(rng.uniform(math.log(lo), math.log(hi)))), 2)
                sc = round(lp * ratio * float(rng.uniform(0.92, 1.08)), 2)
                product_id += 1
                prods.append((product_id, cat, lp, sc))
                rows["products"].append((product_id, tid, name, cat, lp, sc))
        cat_names = list(CATEGORIES)
        prods_by_cat = {c: [p for p in prods if p[1] == c] for c in cat_names}

        # customers
        custs = []  # (id, region_id, segment)
        for i in range(t["customers"]):
            customer_id += 1
            seg = rng.choice(SEGMENTS, p=SEGMENT_P)
            reg = int(rng.choice(region_ids, p=t["region_weights"]))
            if seg == "Consumer":
                name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            else:
                name = f"{rng.choice(COMPANY_A)} {rng.choice(COMPANY_B)} {i % 97}"
            email = f"user{customer_id}@example.com"
            phone = f"+1-555-{customer_id % 10000:04d}"
            signup = START - timedelta(days=int(rng.integers(0, 700)))
            custs.append((customer_id, reg, str(seg)))
            rows["customers"].append((customer_id, tid, reg, name, str(seg), email, phone, signup))
        custs_by_region = {r: [c for c in custs if c[1] == r] for r in region_ids}

        # employees (salary is restricted)
        for _ in range(60 if tid == 1 else 25):
            employee_id += 1
            reg = int(rng.choice(region_ids))
            title = str(rng.choice(["Account Executive", "Sales Manager", "Analyst", "Director", "Associate"]))
            salary = round(float(rng.normal(95000, 25000)), 2)
            rows["employees"].append((employee_id, tid, reg, f"{rng.choice(FIRST)} {rng.choice(LAST)}", title, max(salary, 40000)))

        # orders
        for mi, (y, m) in enumerate(month_iter()):
            season = {10: 1.10, 11: 1.30, 12: 1.40, 1: 0.90, 2: 0.88}.get(m, 1.0)
            n_orders = int(t["orders_per_month"] * (1.012 ** mi) * season * float(rng.uniform(0.95, 1.05)))
            days_in_month = calendar.monthrange(y, m)[1]
            qy = (y, (m - 1) // 3 + 1)
            for _ in range(n_orders):
                reg = int(rng.choice(region_ids, p=t["region_weights"]))
                pool = custs_by_region[reg]
                cust = pool[int(rng.integers(0, len(pool)))]
                seg = cust[2]
                odate = date(y, m, int(rng.integers(1, days_in_month + 1)))
                channel = str(rng.choice(CHANNELS, p=SEGMENT_CHANNEL_P[seg]))
                u = rng.random()
                status = "cancelled" if u < 0.04 else ("pending" if u < 0.05 else "completed")
                order_id += 1
                rows["orders"].append((order_id, tid, cust[0], reg, odate, channel, status))

                # category mix (region + event dependent)
                w = np.array([0.22, 0.22, 0.20, 0.16, 0.20])
                if region_name[reg] == "APAC" and tid == 1:
                    w = w * np.array([1.6, 0.9, 0.9, 0.9, 0.9])
                if tid == 1 and region_name[reg] == "South" and qy == (2026, 2):
                    w = w * np.array([0.8, 0.8, 0.8, 3.0, 0.8])
                w = w / w.sum()

                n_items = 1 + min(int(rng.poisson(1.2)), 4)
                for _ in range(n_items):
                    cat = cat_names[int(rng.choice(5, p=w))]
                    p = prods_by_cat[cat][int(rng.integers(0, 8))]
                    qty = max(1, int(rng.poisson(SEGMENT_QTY[seg])))
                    price = round(p[2] * float(rng.uniform(0.98, 1.02)), 2)
                    disc = CHANNEL_DISCOUNT[channel] + (0.05 if seg == "Enterprise" else 0.0) + float(rng.normal(0, 0.01))
                    if (y, m) in [(2025, 11), (2025, 12)]:
                        disc += 0.03
                    if tid == 1 and region_name[reg] == "West" and qy == (2026, 3):
                        disc += 0.08
                    disc = round(min(max(disc, 0.0), 0.40), 4)
                    cost = p[3] * (1.002 ** mi) * float(rng.normal(1.0, 0.02))
                    if tid == 1 and qy == (2026, 3):
                        if region_name[reg] == "APAC" and cat == "Electronics":
                            cost *= 1.18
                        if region_name[reg] == "EMEA":
                            cost *= 0.97
                    cost = round(cost, 2)
                    item_id += 1
                    rows["order_items"].append((item_id, tid, order_id, reg, p[0], qty, price, disc, cost))
                    if status == "completed" and rng.random() < 0.03:
                        return_id += 1
                        refund = round(qty * price * (1 - disc) * float(rng.uniform(0.5, 1.0)), 2)
                        rows["returns"].append((return_id, tid, item_id, reg, odate + timedelta(days=int(rng.integers(5, 31))), refund))
    return rows


COLUMNS = {
    "tenants": "tenant_id, tenant_name",
    "regions": "region_id, tenant_id, region_name, country",
    "products": "product_id, tenant_id, product_name, category, list_price, standard_cost",
    "customers": "customer_id, tenant_id, region_id, customer_name, segment, email, phone, signup_date",
    "employees": "employee_id, tenant_id, region_id, full_name, title, salary",
    "orders": "order_id, tenant_id, customer_id, region_id, order_date, channel, status",
    "order_items": "order_item_id, tenant_id, order_id, region_id, product_id, quantity, unit_price, discount, unit_cost",
    "returns": "return_id, tenant_id, order_item_id, region_id, return_date, refund_amount",
}
LOAD_ORDER = ["tenants", "regions", "products", "customers", "employees", "orders", "order_items", "returns"]


def plain_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def apply_schema(conn: psycopg.Connection) -> None:
    from querypilot.config import BACKEND_DIR

    sql = (BACKEND_DIR / "db" / "init" / "01_schema.sql").read_text()
    conn.execute(sql)


def load(rows, conn: psycopg.Connection) -> None:
    conn.execute("TRUNCATE " + ", ".join(f"sales.{t}" for t in reversed(LOAD_ORDER)) + " CASCADE")
    for table in LOAD_ORDER:
        buf = io.StringIO()
        for r in rows[table]:
            buf.write("\t".join(str(v) for v in r) + "\n")
        buf.seek(0)
        with conn.cursor() as cur, cur.copy(f"COPY sales.{table} ({COLUMNS[table]}) FROM STDIN") as cp:
            cp.write(buf.read())
    conn.execute("ANALYZE")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", action="store_true", help="(re)apply db/init/01_schema.sql first")
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)
    rows = build(rng)
    with psycopg.connect(plain_dsn(get_settings().db_admin_url), autocommit=True) as conn:
        if args.schema:
            apply_schema(conn)
        with conn.transaction():
            load(rows, conn)
    print({k: len(v) for k, v in rows.items()})


if __name__ == "__main__":
    main()
