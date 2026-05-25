export type AgentEvent =
  | { kind: "step.started"; step_id: string; name: string; run_id: string; ts: number }
  | { kind: "step.progress"; step_id: string; progress: number; detail?: string | null; run_id: string; ts: number }
  | { kind: "step.completed"; step_id: string; duration_ms: number; run_id: string; ts: number }
  | { kind: "step.failed"; step_id: string; error: string; retry_possible?: boolean; run_id: string; ts: number }
  | { kind: "llm.token"; step_id: string; chunk: string; run_id: string; ts: number }
  | { kind: "tool.started"; tool: string; args_summary?: string | null; run_id: string; ts: number }
  | { kind: "tool.completed"; tool: string; result_summary?: string | null; run_id: string; ts: number }
  | { kind: "awaiting_input"; question: string; schema?: Record<string, unknown>; respond_url?: string; run_id: string; ts: number }
  | { kind: "profiling.table.started"; database: string; table: string; idx: number; total: number; run_id: string; ts: number }
  | { kind: "profiling.table.completed"; database: string; table: string; duration_ms: number; run_id: string; ts: number }
  | { kind: "result.partial"; preview_url?: string; run_id: string; ts: number }
  | { kind: "result.final"; summary: string | null; sql: string | null; preview: unknown; export_url: string | null; run_id: string; ts: number }
  | { kind: "error"; code: string; message: string; run_id: string; ts: number }
  | { kind: "done"; run_id: string; ts: number };

export type AgentEventKind = AgentEvent["kind"];
