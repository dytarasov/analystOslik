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
  created_at: string;
  updated_at: string;
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
