/** Mirrors the backend's Pydantic models (backend/app/schemas, app/dashboard/spec.py). */

export type SourceType = "postgres" | "clickhouse" | "csv" | "duckdb";

export interface DataSource {
  id: string;
  name: string;
  type: SourceType;
  config: Record<string, unknown>;
  has_secret: boolean;
  status: string;
  status_message: string | null;
  business_context: Record<string, unknown>;
  table_count: number;
  last_synced_at: string | null;
}

export interface SchemaColumn {
  name: string;
  data_type: string;
  normalized_type: string;
  description: string | null;
  sample_values: string[] | null;
  distinct_count: number | null;
  is_dimension: boolean;
}

export interface SchemaTable {
  id: string;
  schema_name: string;
  table_name: string;
  description: string | null;
  row_count_estimate: number | null;
  is_queryable: boolean;
  columns: SchemaColumn[];
}

export interface Relationship {
  from_table: string;
  from_column: string;
  to_table: string;
  to_column: string;
  source: string;
}

export interface Schema {
  tables: SchemaTable[];
  relationships: Relationship[];
}

export interface ConnectionTest {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
  server_version: string | null;
}

export type AnalysisStatus = "queued" | "running" | "completed" | "failed";

export interface AnalysisSummary {
  id: string;
  question: string;
  datasource_id: string | null;
  datasource_name: string | null;
  status: AnalysisStatus;
  stage: string | null;
  stop_reason: string | null;
  question_type: string | null;
  created_at: string;
  latency_ms: number | null;
  llm_calls: number;
  tokens: number;
}

export interface Step {
  seq: number;
  purpose: "primary" | "drilldown";
  step_question: string | null;
  attempt: number;
  status: "rejected" | "failed" | "succeeded";
  error: string | null;
  sql: string | null;
  original_sql: string;
  tables_used: string[];
  row_count: number | null;
  execution_ms: number | null;
  filters: Record<string, string>[] | null;
}

export type NumberFormat = "number" | "currency" | "percent" | "compact";
export type ChartType = "line" | "bar" | "area" | "pie";
export type Severity = "info" | "positive" | "warning" | "negative";

export interface Kpi {
  id: string;
  label: string;
  query_seq: number;
  value_column: string;
  row_index: number;
  format: NumberFormat;
  comparison_column: string | null;
  value: number;
  comparison_value?: number;
  delta_pct?: number | null;
}

export interface ChartSpec {
  id: string;
  type: ChartType;
  title: string;
  query_seq: number;
  x: string;
  y: string[];
  series: string | null;
  format: NumberFormat;
  description: string | null;
}

export interface TableSpec {
  id: string;
  title: string;
  query_seq: number;
  columns: string[] | null;
}

export interface Insight {
  id: string;
  title: string;
  text: string;
  severity: Severity;
  evidence_query_seq: number[];
}

export interface DashboardSpec {
  title: string;
  summary: string;
  kpis: unknown[];
  charts: ChartSpec[];
  tables: TableSpec[];
  insights: Insight[];
}

export interface Dataset {
  columns: { name: string; type: "number" | "temporal" | "text" | "empty" }[];
  rows: (string | number | null)[][];
  row_count: number;
  truncated: boolean;
  sql: string | null;
  step_question: string | null;
}

export interface Dashboard {
  spec: DashboardSpec;
  kpis: Kpi[];
  datasets: Record<string, Dataset>;
  generated_by: "llm" | "fallback";
}

export interface Finding {
  statement: string;
  evidence_query_seq: number[];
  importance: string;
}

export interface Findings {
  summary: string;
  findings: Finding[];
  confidence: "high" | "medium" | "low";
  caveats: string[];
  rounds?: number;
}

export interface ContextChunk {
  kind: string;
  title: string;
  score: number;
  reason: string;
}

export interface RetrievedContext {
  question: string;
  confidence: string;
  chunks: ContextChunk[];
  joins: string[];
}

export interface Analysis extends AnalysisSummary {
  steps: Step[];
  findings: Findings | null;
  retrieved_context: RetrievedContext | null;
  dashboard: Dashboard | null;
  stats: {
    llm_calls: number;
    input_tokens: number;
    output_tokens: number;
    latency_ms: number | null;
    drilldown_depth: number;
  };
  error: { code: string; message: string } | null;
}

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  is_admin: boolean;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface Usage {
  analyses: number;
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
}

export interface Me {
  user: User;
  usage_today: Usage;
  limits: { analyses_per_day: number; llm_calls_per_day: number; tokens_per_day: number };
  resets_at: string;
}
