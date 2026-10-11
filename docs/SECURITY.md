# Security model

QueryPilot assumes the language model, the user's wording and any SQL a user pastes are **untrusted**.
Controls are layered so that no single layer has to be perfect.

| Layer | Where | What it enforces |
|---|---|---|
| 1. Intent guard | `safety/policy.py::guard_question` | Blocks requests for PII/salary, destructive intent ("delete all orders"), prompt-injection / guard-bypass attempts, system-table access and cross-tenant wording — before any model or SQL is involved. |
| 2. Plan authorisation | `safety/policy.py::authorize_plan` | Region-scoped roles cannot filter to regions outside their scope (clear message instead of silent empty result). |
| 3. Governed compilation | `semantic/compiler.py` | Semantic-route SQL is generated only from catalog metric formulas and whitelisted dimensions; filter values are escaped literals. |
| 4. SQL validator | `safety/validator.py` (sqlglot AST) | Exactly one statement; SELECT/UNION only; no DML/DDL/`SELECT INTO`/`FOR UPDATE`; denylisted functions (`pg_sleep`, `pg_read_file`, `set_config`, `current_setting`, `dblink`, `lo_*`, any `pg_*`); only role-allowed `sales.*` tables; no `pg_catalog`/`information_schema`/`qp_ops`; restricted columns and `SELECT *` over restricted tables rejected; literal `tenant_id` predicates rejected; row cap injected (role-specific). |
| 5. Cost guard | `pipeline/executor.py` | `EXPLAIN` before execution; queries above `QP_MAX_PLAN_COST` are refused (and fed to the repair loop). |
| 6. Database role | `db/init/01_schema.sql` | `qp_reader`: `default_transaction_read_only = on`, `statement_timeout`, connection limit, `SELECT` only, **column-level grants exclude email/phone/salary**. |
| 7. Row-level security | `db/init/01_schema.sql` | Every table filters on `app.tenant_id` (and `app.region_ids` for region-scoped roles), set per transaction with `set_config(..., true)`. If unset, queries return **zero rows** (fail closed). |
| 8. Audit | `qp_ops.audit_log` | Every question, outcome, block reason and executed SQL. The app role has `INSERT`/`SELECT` only — no `UPDATE`/`DELETE`. |

Verified by `tests/test_validator.py`, `tests/test_pipeline_db.py` (RLS fail-closed, tenant/region scoping,
read-only, column grants) and the 44 adversarial benchmark cases (0 executed).

## Production checklist
* Replace the demo login (`/api/auth/demo-login`) with your IdP (OIDC/SAML); map groups to roles in `catalog/policies.yaml`.
* Set `QP_JWT_SECRET`, and change the `qp_reader` / `qp_app` passwords (`ALTER ROLE ... PASSWORD`) and DSNs.
* Point `QP_DB_READER_URL` at a read replica.
* Keep the LLM self-hosted (Ollama / llama.cpp / vLLM) so prompts containing schema and values never leave your network.
* The in-process rate limiter and caches are per API worker; use a shared store (e.g. Redis) when scaling out.
