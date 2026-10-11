-- QueryPilot database bootstrap.
-- Runs as the database owner. Creates:
--   * schema `sales`  : the analytical data the copilot is allowed to query
--   * schema `qp_ops` : audit log, traces, conversations (written by the app role only)
--   * role qp_reader  : read-only, row-level-security enforced, used for ALL generated SQL
--   * role qp_app     : append-only writer for qp_ops
-- Passwords below are development defaults. Override them in production
-- (see docs/SECURITY.md) - the app reads its DSNs from environment variables.

CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'qp_reader') THEN
    CREATE ROLE qp_reader LOGIN PASSWORD 'qp_reader_dev' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'qp_app') THEN
    CREATE ROLE qp_app LOGIN PASSWORD 'qp_app_dev' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END $$;

-- Reader can never write, even if the validator were bypassed.
ALTER ROLE qp_reader SET default_transaction_read_only = on;
ALTER ROLE qp_reader SET statement_timeout = '10s';
ALTER ROLE qp_reader SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE qp_reader SET work_mem = '32MB';
ALTER ROLE qp_reader CONNECTION LIMIT 40;

CREATE SCHEMA IF NOT EXISTS sales;
CREATE SCHEMA IF NOT EXISTS qp_ops;

-- ---------------------------------------------------------------- sales data
CREATE TABLE IF NOT EXISTS sales.tenants (
  tenant_id   int PRIMARY KEY,
  tenant_name text NOT NULL
);

CREATE TABLE IF NOT EXISTS sales.regions (
  region_id   int PRIMARY KEY,
  tenant_id   int NOT NULL REFERENCES sales.tenants,
  region_name text NOT NULL,
  country     text NOT NULL
);

CREATE TABLE IF NOT EXISTS sales.products (
  product_id   int PRIMARY KEY,
  tenant_id    int NOT NULL REFERENCES sales.tenants,
  product_name text NOT NULL,
  category     text NOT NULL,
  list_price   numeric(12,2) NOT NULL,
  standard_cost numeric(12,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS sales.customers (
  customer_id   int PRIMARY KEY,
  tenant_id     int NOT NULL REFERENCES sales.tenants,
  region_id     int NOT NULL REFERENCES sales.regions,
  customer_name text NOT NULL,
  segment       text NOT NULL,
  email         text NOT NULL,   -- PII (restricted)
  phone         text NOT NULL,   -- PII (restricted)
  signup_date   date NOT NULL
);

CREATE TABLE IF NOT EXISTS sales.orders (
  order_id    bigint PRIMARY KEY,
  tenant_id   int NOT NULL REFERENCES sales.tenants,
  customer_id int NOT NULL REFERENCES sales.customers,
  region_id   int NOT NULL REFERENCES sales.regions,
  order_date  date NOT NULL,
  channel     text NOT NULL,
  status      text NOT NULL CHECK (status IN ('completed','pending','cancelled'))
);

CREATE TABLE IF NOT EXISTS sales.order_items (
  order_item_id bigint PRIMARY KEY,
  tenant_id     int NOT NULL REFERENCES sales.tenants,
  order_id      bigint NOT NULL REFERENCES sales.orders,
  region_id     int NOT NULL REFERENCES sales.regions,  -- denormalised for row-level security
  product_id    int NOT NULL REFERENCES sales.products,
  quantity      int NOT NULL CHECK (quantity > 0),
  unit_price    numeric(12,2) NOT NULL,
  discount      numeric(5,4) NOT NULL CHECK (discount >= 0 AND discount < 1),
  unit_cost     numeric(12,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS sales.returns (
  return_id     bigint PRIMARY KEY,
  tenant_id     int NOT NULL REFERENCES sales.tenants,
  order_item_id bigint NOT NULL UNIQUE REFERENCES sales.order_items,
  region_id     int NOT NULL REFERENCES sales.regions,
  return_date   date NOT NULL,
  refund_amount numeric(12,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS sales.employees (
  employee_id   int PRIMARY KEY,
  tenant_id     int NOT NULL REFERENCES sales.tenants,
  region_id     int NOT NULL REFERENCES sales.regions,
  full_name     text NOT NULL,
  title         text NOT NULL,
  salary        numeric(12,2) NOT NULL  -- restricted
);

CREATE INDEX IF NOT EXISTS orders_tenant_date ON sales.orders (tenant_id, order_date);
CREATE INDEX IF NOT EXISTS orders_region ON sales.orders (region_id);
CREATE INDEX IF NOT EXISTS order_items_order ON sales.order_items (order_id);
CREATE INDEX IF NOT EXISTS order_items_tenant ON sales.order_items (tenant_id, region_id);
CREATE INDEX IF NOT EXISTS returns_item ON sales.returns (order_item_id);

-- ---------------------------------------------------------------- privileges
REVOKE ALL ON SCHEMA sales FROM PUBLIC;
REVOKE ALL ON SCHEMA qp_ops FROM PUBLIC;
GRANT USAGE ON SCHEMA sales TO qp_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA sales TO qp_reader;
-- Column-level lockdown: PII and salary are not selectable by the reader role at all.
-- The app's policy layer adds role-based rules on top; the database is the last line.
REVOKE SELECT ON sales.customers FROM qp_reader;
GRANT SELECT (customer_id, tenant_id, region_id, customer_name, segment, signup_date) ON sales.customers TO qp_reader;
REVOKE SELECT ON sales.employees FROM qp_reader;
GRANT SELECT (employee_id, tenant_id, region_id, full_name, title) ON sales.employees TO qp_reader;

-- ---------------------------------------------------------------- row-level security
-- Every query runs inside a transaction that sets:
--   app.tenant_id  = the caller's tenant (mandatory; unset => zero rows, fail closed)
--   app.region_ids = comma separated allowed region ids ('' => all regions of the tenant)
CREATE OR REPLACE FUNCTION sales.qp_row_allowed(row_tenant int, row_region int)
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT row_tenant = NULLIF(current_setting('app.tenant_id', true), '')::int
     AND (COALESCE(current_setting('app.region_ids', true), '') = ''
          OR row_region = ANY (string_to_array(current_setting('app.region_ids', true), ',')::int[]));
$$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['regions','customers','orders','order_items','returns','employees'] LOOP
    EXECUTE format('ALTER TABLE sales.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS qp_isolation ON sales.%I', t);
    EXECUTE format('CREATE POLICY qp_isolation ON sales.%I FOR SELECT TO qp_reader USING (sales.qp_row_allowed(tenant_id, region_id))', t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['tenants','products'] LOOP
    EXECUTE format('ALTER TABLE sales.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS qp_isolation ON sales.%I', t);
    EXECUTE format('CREATE POLICY qp_isolation ON sales.%I FOR SELECT TO qp_reader USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::int)', t);
  END LOOP;
END $$;
GRANT EXECUTE ON FUNCTION sales.qp_row_allowed(int, int) TO qp_reader;

-- ---------------------------------------------------------------- ops tables
CREATE TABLE IF NOT EXISTS qp_ops.audit_log (
  audit_id      bigserial PRIMARY KEY,
  ts            timestamptz NOT NULL DEFAULT now(),
  trace_id      uuid NOT NULL,
  user_id       text NOT NULL,
  tenant_id     int NOT NULL,
  role          text NOT NULL,
  conversation_id uuid,
  question      text NOT NULL,
  status        text NOT NULL,          -- answered | clarify | blocked | error
  blocked_reason text,
  sql_text      text,
  row_count     int,
  latency_ms    numeric(12,2),
  cache_hit     boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS audit_ts ON qp_ops.audit_log (tenant_id, ts DESC);

CREATE TABLE IF NOT EXISTS qp_ops.traces (
  trace_id    uuid PRIMARY KEY,
  ts          timestamptz NOT NULL DEFAULT now(),
  tenant_id   int NOT NULL,
  user_id     text NOT NULL,
  status      text NOT NULL,
  intent      text,
  metrics     text[],
  confidence  numeric(5,4),
  total_ms    numeric(12,2),
  llm_tokens  int NOT NULL DEFAULT 0,
  db_ms       numeric(12,2),
  spans       jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS traces_ts ON qp_ops.traces (tenant_id, ts DESC);

CREATE TABLE IF NOT EXISTS qp_ops.conversations (
  conversation_id uuid PRIMARY KEY,
  tenant_id   int NOT NULL,
  user_id     text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  state       jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- Catalog embeddings for the optional pgvector retriever (384 dims = all-MiniLM-L6-v2).
CREATE TABLE IF NOT EXISTS qp_ops.catalog_embeddings (
  doc_id      text PRIMARY KEY,
  catalog_version text NOT NULL,
  kind        text NOT NULL,
  content     text NOT NULL,
  embedding   vector(384) NOT NULL
);

GRANT USAGE ON SCHEMA qp_ops TO qp_app;
-- The audit log is append-only for the application: no UPDATE / DELETE.
GRANT SELECT, INSERT ON qp_ops.audit_log TO qp_app;
GRANT USAGE ON SEQUENCE qp_ops.audit_log_audit_id_seq TO qp_app;
GRANT SELECT, INSERT ON qp_ops.traces TO qp_app;
GRANT SELECT, INSERT, UPDATE ON qp_ops.conversations TO qp_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON qp_ops.catalog_embeddings TO qp_app;
