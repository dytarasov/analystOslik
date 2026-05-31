export type ProfilingStatus =
  | "never_profiled"
  | "in_progress"
  | "profiled"
  | "failed"
  | "stale";

export type DataSource = {
  id: string;
  name: string;
  kind: "clickhouse";
  host: string;
  port: number;
  database: string;
  username: string;
  secure: boolean;
  extra_settings: Record<string, unknown>;
  readonly_verified: boolean;
  last_test_at: string | null;
  last_test_status: string | null;
  last_test_error: string | null;
  last_profiling_run_id: string | null;
  last_profiled_at: string | null;
  profiling_status: ProfilingStatus;
  glossary_md: string | null;
  glossary_ingested_at: string | null;
  sql_notes_md: string | null;
  sql_notes_ingested_at: string | null;
  created_at: string;
  updated_at: string;
};

export type GlossaryIngestResult = {
  ok: boolean;
  notes: number;
  metrics: number;
  terms: number;
  columns: number;
  relations: number;
  warnings: string[];
};

export type SqlNotesIngestResult = {
  ok: boolean;
  recipes: number;
  warnings: string[];
};

export type DataSourceCreate = {
  name: string;
  kind?: "clickhouse";
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  secure?: boolean;
  extra_settings?: Record<string, unknown>;
};

export type DataSourceUpdate = {
  name?: string;
  host?: string;
  port?: number;
  database?: string;
  username?: string;
  // Empty/omitted password keeps the existing encrypted secret.
  password?: string;
  secure?: boolean;
  extra_settings?: Record<string, unknown>;
  // Provided (even "") sets the glossary; omitted leaves it unchanged.
  glossary_md?: string;
  // Same semantics as glossary_md, for the separate SQL-notes area.
  sql_notes_md?: string;
};

export type TestConnectionResult = {
  ok: boolean;
  version?: string | null;
  readonly?: boolean;
  error?: string | null;
};

export type ApiError = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
};
