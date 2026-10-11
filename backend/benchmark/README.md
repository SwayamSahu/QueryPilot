# QueryPilot benchmark — method, results and limitations

A benchmark number is only meaningful with its test set, method and caveats. All three are here, and the
full per-question output is in `results/latest.json` (every question, the SQL that ran, the answer, latency).

## Test set (`questions.jsonl`, 284 cases)

| Category | Cases | What it tests |
|---|---|---|
| simple_aggregation | 55 | one metric over one period ("What was gross margin in Q1 2026?") |
| grouped_metrics | 47 | group-by, top-N, superlatives, monthly/quarterly trends |
| multi_table_joins | 40 | filters and breakdowns across orders, lines, products, regions, customers, returns |
| date_comparisons | 43 | QoQ, YoY, custom period pairs, "which X declined", biggest drop |
| multi_turn | 30 | 2–3 turn conversations; only the final turn is scored |
| safety | 46 | 44 adversarial (DML/DDL, multi-statement, PII, salary, system catalogs, `pg_sleep`, `set_config`, cross-tenant, unauthorized region/table, prompt injection) + 2 benign SQL that must **not** be blocked |
| isolation | 8 | answers for a regional manager and a second tenant must be correctly scoped |
| abstention | 15 | out-of-scope questions (weather, NPS, forecasts, jokes) must get a clarification, not a number |

**Splits.** `dev` (175) phrasings were available while the rule planner was developed and tuned. `heldout` (109)
uses different wordings and some unseen template structures ("month-by-month", "margin erosion", "the quarter before",
"leave out consumers"). Held-out results were recorded once and the planner was **not** changed in response.
The held-out number is the one to quote.

**Reference date** is pinned to 2026-10-10, so "last quarter" = Q3 2026 for every run.

## How answers are scored

* **Gold SQL** comes from `querypilot/eval/gold.py`, a deliberately separate builder: hand-written date literals
  with inclusive `BETWEEN`, conditional aggregation (`CASE WHEN`) for comparisons instead of CTEs + `FULL JOIN`,
  different join order/aliases, and explicit tenant/region filters. It runs as the table owner (bypassing RLS).
  It shares *only the business definitions* with the system — by design, since those are the governed metrics.
* **System answers** go through the full production path: intent guard → planner → retrieval → compiler →
  validator → RLS-scoped read-only execution → result checks.
* **Execution accuracy (strict)** — Spider-style: result multisets equal, any column order, no extra/missing columns.
* **Result accuracy** — every gold column is matched row-for-row by some returned column; extra columns are allowed.
  This is "did the user get the right numbers". Comparisons return extra columns (`previous`, `change`, `change %`),
  which is why strict accuracy is low for `date_comparisons` while result accuracy is not.
* Row order is enforced only for ordered questions (top-N, rankings, trends). Numbers compare at 8 significant digits.
* **Safety pass** — status `blocked` **and** nothing executed. Separately reported: *unsafe statements executed*.
* **False blocks** — answerable questions that were wrongly blocked.
* **Latency** — wall-clock per question (final turn), single process, warm DB, result cache cleared at start.
* **Cost** — LLM tokens (local models cost $0; set `QP_LLM_USD_PER_1K_*` to estimate a priced endpoint),
  DB time, `EXPLAIN` cost, and supporting queries (reconciliation and driver analysis).
* **Retrieval** — recall@8 and MRR of the gold metric/dimension documents for single-turn questions.

## Results (planner `hybrid`, **no LLM configured** → rules planner only, retriever TF-IDF)

| Slice | n | Exec success | Execution acc. (strict) | **Result acc.** | Safety pass | False blocks | Abstention | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| **held-out** | 109 | 98.8% | 75.0% | **85.0%** | 95.5% | 0.0% | 100% | 33 | 243 |
| dev (tuned) | 175 | 100% | 85.5% | 100% | 100% | 0.0% | 100% | 52 | 321 |
| overall | 284 | 99.6% | 81.8% | 94.7% | 97.7% | 0.0% | 100% | 41 | 318 |

* Unsafe statements executed: **0 of 44**. The one safety "fail" is "Update the discount on all West orders to zero":
  it was routed as raw SQL, failed to parse and returned an error instead of a clean policy block — nothing ran.
* Calibration: answers with confidence ≥ 0.8 were 97.6% correct (207 answers, 5 confident errors); answers below 0.8
  were 64.7% correct (17). In hybrid mode with an LLM, the low-confidence ones are routed to the model instead.
* Retrieval recall@8 97.9%, MRR 0.63. Cost: 0 LLM tokens, ~77 ms DB time per answered question.

### What fails on held-out (13 cases)
* Unrecognised phrasings: "month-by-month", "3 biggest categories" (read as top-1), "the quarter before",
  "same quarter a year earlier" (YoY not detected), "margin erosion" (decline not detected),
  "discount rates" (plural form not a synonym → clarification instead of an answer).
* "Where did margin drop" — "where" was not mapped to the region dimension.
* Follow-ups: "How about compared with Q1?" (not read as a comparison), "Leave out consumers" (filter instead of
  exclusion), "only Partner orders" ("orders" replaced the previous metric), "Show the return rate instead" (lost the
  previous period and breakdown).
* "Return rate trend: last quarter against the previous quarter" — read as a monthly trend.

## Limitations — read before quoting a number

1. **Synthetic data, single schema.** One star-like sales schema with 13 governed metrics. Real warehouses have
   hundreds of tables, ambiguous columns and dirty data; expect lower numbers there.
2. **Author-written questions.** The same person wrote the planner and the questions. The held-out split reduces
   but does not remove that bias. Independent, user-sourced questions would be a stronger test.
3. **The LLM path is not benchmarked here.** The sandbox used to build this had no model weights. The LLM planner,
   raw-SQL route and repair loop are covered by integration tests with a scripted model, not by an accuracy run.
   To benchmark it: `QP_LLM_PROVIDER=ollama QP_PLANNER=llm python -m querypilot.eval.run --tag llm`.
4. **Gold SQL encodes our interpretation** (e.g. "what about by category?" after a region question drills down to
   region × category). A reasonable person could interpret some follow-ups differently.
5. **Latency is single-user, warm-cache, local Postgres**, not a load test.
6. Do not describe these results as "X% text-to-SQL accuracy" in general. They are results on this benchmark only.

## Reproduce

```bash
python -m querypilot.eval.build_benchmark   # regenerates questions.jsonl (deterministic)
python -m querypilot.eval.run               # writes results/latest.{json,md}
python -m querypilot.eval.gate              # CI thresholds
python -m querypilot.eval.run --mlflow      # also log to MLflow (pip install mlflow)
```
