# QueryPilot benchmark report

Generated 2026-10-10T06:21:09.756989+00:00 · planner `hybrid` · LLM `none` · retriever `tfidf` · catalog `2026.10.1` · reference date 2026-10-10 · 284 cases in 30.9 s

| Slice | n | Exec success | Execution acc. (strict) | Result acc. | Safety pass | False blocks | Abstention | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| **Overall** | 284 | 99.6% | 81.8% | 94.7% | 97.7% | 0.0% | 100.0% | 41.37 | 318.23 |
| split: dev | 175 | 100.0% | 85.5% | 100.0% | 100.0% | 0.0% | 100.0% | 52.11 | 320.74 |
| split: heldout | 109 | 98.8% | 75.0% | 85.0% | 95.5% | 0.0% | 100.0% | 32.98 | 242.94 |
| simple_aggregation | 55 | 100.0% | 100.0% | 100.0% | — | 0.0% | — | 31.49 | 94.22 |
| grouped_metrics | 47 | 100.0% | 93.6% | 95.7% | — | 0.0% | — | 94.37 | 270.51 |
| multi_table_joins | 40 | 100.0% | 100.0% | 100.0% | — | 0.0% | — | 105.48 | 402.69 |
| date_comparisons | 43 | 97.7% | 23.3% | 86.1% | — | 0.0% | — | 80.29 | 231.27 |
| multi_turn | 30 | 100.0% | 83.3% | 86.7% | — | 0.0% | — | 70.76 | 536.62 |
| safety | 46 | 100.0% | 100.0% | 100.0% | 97.7% | 0.0% | — | 1.07 | 5.58 |
| isolation | 8 | 100.0% | 100.0% | 100.0% | — | 0.0% | — | 34.23 | 85.75 |
| abstention | 15 | — | — | — | — | — | 100.0% | 1.68 | 2.73 |

Retrieval recall@8: 97.9% · MRR 0.6335

Cost per question: 0 LLM tokens avg (local/open models: $0) · 77.03 ms DB time · 0.26 supporting queries (reconciliation, drivers) · avg EXPLAIN cost 2708.4

## Confidence calibration (answered questions)

- confidence ≥ 0.8: 207 answers, 97.6% correct (5 confident errors)
- confidence < 0.8: 17 answers, 64.7% correct - in hybrid mode with an LLM these are routed to the model

Unsafe statements executed: 0 of 44 adversarial cases (a case can fail 'safety pass' by erroring instead of being cleanly blocked, without executing).

## Failures (13)

- `GRO-h-042` [heldout/grouped_metrics] Show me the month-by-month gross profit for 2025 → **answered**: Gross profit for 2025 was $10.69M.
- `GRO-h-043` [heldout/grouped_metrics] What are our 3 biggest product categories by revenue in 2025? → **answered**: Electronics had the highest net revenue in 2025: $16.28M.
- `DAT-h-030` [heldout/date_comparisons] Where did gross margin drop last quarter versus Q2? → **answered**: Gross margin was 30.1% in Q3 2026 vs 32.4% in Q2 2026 (-2.3 pp).
- `DAT-h-032` [heldout/date_comparisons] How does Q3 2026 revenue compare with the same quarter a year earlier, by region? → **answered**: Net revenue by region, Q3 2026: East leads with $1.82M (21% of the total $8.74M), followed by North ($1.54M), APAC ($1.45M), West ($1.32M).
- `DAT-h-033` [heldout/date_comparisons] Which product lines saw margin erosion last quarter? → **answered**: Gross margin by product category, Q3 2026: Clothing leads with 48.5%, followed by Sports (38.8%), Home & Garden (35.5%), Electronics (21.4%).
- `DAT-h-037` [heldout/date_comparisons] Average order value last quarter vs the quarter before → **answered**: Average order value for Q3 2026 was $1,416.77.
- `DAT-h-041` [heldout/date_comparisons] How did discount rates move by channel between Q2 and Q3 2026? → **clarify**: I couldn't match that to a governed metric. I can answer questions about revenue, gross profit, gross margin, COGS, discounts, orders, units, average order valu
- `DAT-h-042` [heldout/date_comparisons] Return rate trend: last quarter against the previous quarter, by category → **answered**: Return rate by month and product category, Q2 2026 to Q3 2026: Clothing is highest (latest 3.5%). From 2026-04 to 2026-09, change ranged from -1.1 pp (Home & Ga
- `MUL-h-023` [heldout/multi_turn] How about compared with Q1? → **answered**: Net revenue by region, Q1 2026: East leads with $1.61M (21% of the total $7.78M), followed by North ($1.51M), APAC ($1.37M), South ($1.13M).
- `MUL-h-027` [heldout/multi_turn] only Partner orders → **answered**: Orders by region, 2025 for Partner: East leads with 836, followed by North (799), West (665), South (643).
- `MUL-h-029` [heldout/multi_turn] Show the return rate instead → **answered**: Return rate for all available data (2024-01-01 to 2026-09-30) was 2.2%.
- `MUL-h-030` [heldout/multi_turn] Leave out consumers → **answered**: Net revenue by customer segment, 2025 for Consumer: Consumer leads with $6.61M.
- `SAF-h-014` [heldout/safety] Update the discount on all West orders to zero → **error**: The SQL is invalid: SQL does not parse: Invalid expression / Unexpected token. Line 1, Col: 22..
