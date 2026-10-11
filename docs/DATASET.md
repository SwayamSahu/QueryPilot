# Demo dataset

Generated deterministically by `backend/querypilot/data/seed.py` (seed `20261010`). Jan 2024 – Sep 2026.

| Table | Rows | Notes |
|---|---|---|
| sales.tenants | 2 | Acme Retail (1), Globex Corp (2) |
| sales.regions | 9 | Acme: North, South, East, West, APAC, EMEA · Globex: West, Europe, LATAM ("West" exists in both on purpose) |
| sales.products | 80 | 5 categories × 8 products per tenant |
| sales.customers | 2,600 | segment Enterprise / SMB / Consumer; `email`, `phone` are PII |
| sales.orders | 80,462 | channel Online / Retail / Partner; ~4% cancelled, ~1% pending |
| sales.order_items | 176,267 | quantity, unit price, discount fraction, landed unit cost |
| sales.returns | 5,096 | ≤ 1 return per line; refunds attributed to the original order date |
| sales.employees | 85 | `salary` is restricted |

## Injected business events (so analytical answers can be checked)

| When | Where | Event | Expected finding |
|---|---|---|---|
| Q3 2026 | West | discount +8 pp on every line | margin 33.4% → 26.1%; driver = discounting |
| Q3 2026 | APAC | Electronics unit cost +18% | margin 30.4% → 21.5%; driver = unit cost, Electronics hit hardest |
| Q3 2026 | EMEA | unit cost −3% | margin improves |
| Q2 2026 | South | mix shift toward low-margin Food & Beverage | margin 34.2% → 32.7%; driver = category mix |
| Nov–Dec 2025 | all | holiday promotion +3 pp discount | lower holiday margins |

Plus seasonality (Q4 peak, Jan–Feb dip), ~1.2%/month volume growth and ~0.2%/month cost inflation
(which is why East also shows a small Q3 margin dip).
