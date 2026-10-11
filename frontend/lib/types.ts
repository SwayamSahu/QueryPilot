export type Fmt = "text" | "currency" | "percent" | "number" | "pp";

export interface ColumnMeta { key: string; label: string; kind: string; format: Fmt }
export interface TableResult { columns: ColumnMeta[]; rows: (string | number | null)[][]; row_count: number; truncated: boolean }
export interface ChartSpec { type: "kpi" | "bar" | "grouped_bar" | "line" | "table"; x?: string | null; title: string;
  series: { key: string; label: string; format: Fmt; pivot?: string }[] }
export interface Check { name: string; passed: boolean; detail: string; severity: "info" | "warn" | "error" }
export interface Span { name: string; start_ms: number; duration_ms: number; status: string; attributes: Record<string, unknown> }
export interface MetricRef { name: string; label: string; version: number; definition: string; sql: string }
export interface Kpi { label: string; value: number | null; format: Fmt; display: string; previous?: number | null;
  change?: number | null; change_display?: string; previous_label?: string }
export interface Driver { member: string; metric: string; change: number; main_driver: string; statement: string;
  components?: Record<string, number>; discount_rate?: [number, number]; cogs_ratio?: [number, number]; top_breakdown?: string | null }

export interface AskResponse {
  status: "answered" | "clarify" | "blocked" | "error";
  question: string; answer: string; conversation_id: string; trace_id: string; route: "semantic" | "sql" | "none";
  assumptions: string[]; kpis: Kpi[]; table: TableResult | null; chart: ChartSpec | null; drivers: Driver[];
  sql: string | null; plan: Record<string, unknown> | null; metrics_used: MetricRef[]; checks: Check[];
  confidence: number; suggestions: string[]; blocked_reason: string | null; cache_hit: boolean; catalog_version: string;
  timings: Record<string, number>; usage: Record<string, unknown>; spans: Span[];
}

export interface DemoUser { user_id: string; name: string; title: string; tenant_id: number; tenant: string; role: string; regions?: string[] }
