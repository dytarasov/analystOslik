import { API_URL } from "@/lib/env";
import type {
  ApiError,
  DataSource,
  DataSourceCreate,
  DataSourceUpdate,
  TestConnectionResult,
} from "@/lib/types";

export class HttpError extends Error {
  constructor(public status: number, public payload: ApiError) {
    super(payload.message);
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (res.status === 204) {
    return undefined as T;
  }
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const payload: ApiError =
      body && typeof body === "object" && "message" in body
        ? body
        : { code: "HTTP", message: res.statusText };
    throw new HttpError(res.status, payload);
  }
  return body as T;
}

export type SemTableRow = {
  id: string;
  database: string;
  table_name: string;
  title: string | null;
  description: string | null;
  domain: string | null;
  tags: string[];
  confirmation_status: "draft" | "confirmed" | "needs_review";
  confirmed_at: string | null;
  updated_at: string;
};

export type SemColumn = {
  id: string;
  name: string;
  position: number;
  data_type: string;
  description: string | null;
  semantic_role: string | null;
  user_notes: string | null;
  null_ratio: number | null;
  distinct_count: number | null;
  total_count: number | null;
  examples: unknown;
  confirmation_status: string;
};

export type SemTable = SemTableRow & {
  source_id: string;
  user_notes: string | null;
  columns: SemColumn[];
};

export type ProfilingRun = {
  id: string;
  source_id: string;
  status: string;
  requested_by: string | null;
  params: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  stats: Record<string, unknown>;
  created_at: string;
};

export type ProfilingRunTable = {
  database: string;
  table_name: string;
  status: string;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type ProfilingQuestion = {
  column?: string | null;
  text: string;
  choices?: string[] | null;
};

export type ProfilingQuestionGroup = {
  task_id: string;
  database: string;
  table: string;
  questions: ProfilingQuestion[];
};

export type ProfilingTask = {
  id: string;
  kind: string;
  target: string;
  database: string | null;
  table_name: string | null;
  columns: string[];
  status: string;
  attempts: number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type ProfilingProgress = {
  status: string | null;
  agent_run_id: string | null;
  counts: Record<string, number>;
  coverage: {
    expected: number;
    covered: number;
    missing: { database: string; table: string; column: string }[];
    complete: boolean;
  };
  questions: ProfilingQuestionGroup[];
  tasks: ProfilingTask[];
};

export const api = {
  auth: {
    login: (login: string, password: string) =>
      request<{ login: string; expires_at: number }>(
        "/api/admin/auth/login",
        {
          method: "POST",
          body: JSON.stringify({ login, password }),
        },
      ),
    logout: () =>
      request<void>("/api/admin/auth/logout", { method: "POST" }),
    me: () => request<{ login: string }>("/api/admin/auth/me"),
  },
  sources: {
    list: () => request<DataSource[]>("/api/admin/sources"),
    get: (id: string) => request<DataSource>(`/api/admin/sources/${id}`),
    create: (payload: DataSourceCreate) =>
      request<DataSource>("/api/admin/sources", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    update: (id: string, payload: DataSourceUpdate) =>
      request<DataSource>(`/api/admin/sources/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    remove: (id: string) =>
      request<void>(`/api/admin/sources/${id}`, { method: "DELETE" }),
    testConnection: (id: string) =>
      request<TestConnectionResult>(
        `/api/admin/sources/${id}/test-connection`,
        { method: "POST" },
      ),
    testCredentials: (payload: DataSourceCreate) =>
      request<TestConnectionResult>(
        "/api/admin/sources/test-credentials",
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
      ),
  },
  selection: {
    discover: (source_id: string) =>
      request<
        Array<{
          database: string;
          table: string;
          engine: string | null;
          total_rows: number | null;
          total_bytes: number | null;
          selected: boolean;
        }>
      >(`/api/admin/sources/${source_id}/discover`),
    get: (source_id: string) =>
      request<Array<{ database: string; table_name: string; note: string | null; created_at: string }>>(
        `/api/admin/sources/${source_id}/selection`,
      ),
    save: (
      source_id: string,
      items: Array<{ database: string; table: string; note?: string }>,
    ) =>
      request<{ saved: number }>(`/api/admin/sources/${source_id}/selection`, {
        method: "PUT",
        body: JSON.stringify({ items }),
      }),
  },
  profiling: {
    start: (source_id: string, params?: { include?: string[]; exclude?: string[] }) =>
      request<{ run_id: string; agent_run_id: string; reused: boolean }>(
        "/api/admin/profiling/runs",
        {
          method: "POST",
          body: JSON.stringify({ source_id, ...params }),
        },
      ),
    active: (source_id: string) =>
      request<{
        run_id: string;
        agent_run_id: string | null;
        status: string;
        started_at: string | null;
        attached: boolean;
      } | null>(`/api/admin/profiling/runs/active?source_id=${source_id}`),
    respond: (
      agent_run_id: string,
      payload: { answer?: string; answers?: Record<string, unknown> },
    ) =>
      request<{ accepted: boolean }>(
        `/api/admin/profiling/agent-runs/${agent_run_id}/respond`,
        { method: "POST", body: JSON.stringify(payload) },
      ),
    getRun: (run_id: string) =>
      request<{ run: ProfilingRun; tables: ProfilingRunTable[] }>(
        `/api/admin/profiling/runs/${run_id}`,
      ),
    listRuns: (source_id: string) =>
      request<ProfilingRun[]>(`/api/admin/profiling/runs?source_id=${source_id}`),
    eventsUrl: (agent_run_id: string) =>
      `${API_URL}/api/admin/profiling/agent-runs/${agent_run_id}/events`,
    cancel: (agent_run_id: string) =>
      request<{ cancelled: boolean }>(
        `/api/admin/profiling/agent-runs/${agent_run_id}/cancel`,
        { method: "POST" },
      ),
    progress: (run_id: string) =>
      request<ProfilingProgress>(`/api/admin/profiling/runs/${run_id}/progress`),
    answerTask: (
      task_id: string,
      answers: { column?: string | null; text?: string | null; answer: string }[],
    ) =>
      request<{ ok: boolean; run_id: string }>(
        `/api/admin/profiling/tasks/${task_id}/answer`,
        { method: "POST", body: JSON.stringify({ answers }) },
      ),
  },
  tables: {
    listForSource: (source_id: string) =>
      request<SemTableRow[]>(`/api/admin/sources/${source_id}/tables`),
    get: (table_id: string) => request<SemTable>(`/api/admin/tables/${table_id}`),
    update: (
      table_id: string,
      payload: {
        title?: string | null;
        description?: string | null;
        domain?: string | null;
        tags?: string[] | null;
        user_notes?: string | null;
        reason?: string | null;
      },
    ) =>
      request<SemTable>(`/api/admin/tables/${table_id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    confirm: (table_id: string) =>
      request<SemTable>(`/api/admin/tables/${table_id}/confirm`, {
        method: "POST",
      }),
    regenerate: (table_id: string, guidance?: string | null) =>
      request<{ agent_run_id: string }>(`/api/admin/tables/${table_id}/regenerate`, {
        method: "POST",
        body: JSON.stringify({ guidance }),
      }),
  },
  adminEdit: {
    submit: (source_id: string, prompt: string) =>
      request<{ agent_run_id: string }>(`/api/admin/edit`, {
        method: "POST",
        body: JSON.stringify({ source_id, prompt }),
      }),
    eventsUrl: (agent_run_id: string) =>
      `${API_URL}/api/admin/edit/agent-runs/${agent_run_id}/events`,
  },
  columns: {
    get: (column_id: string) => request<SemColumn & { table_id: string }>(
      `/api/admin/columns/${column_id}`,
    ),
    update: (
      column_id: string,
      payload: {
        description?: string | null;
        semantic_role?: string | null;
        user_notes?: string | null;
        reason?: string | null;
      },
    ) =>
      request<SemColumn>(`/api/admin/columns/${column_id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    confirm: (column_id: string) =>
      request<SemColumn>(`/api/admin/columns/${column_id}/confirm`, {
        method: "POST",
      }),
    regenerate: (column_id: string, guidance?: string | null) =>
      request<{ agent_run_id: string }>(
        `/api/admin/columns/${column_id}/regenerate`,
        {
          method: "POST",
          body: JSON.stringify({ guidance }),
        },
      ),
  },
  tableChat: {
    history: (table_id: string) =>
      request<{
        session_id: string | null;
        messages: Array<{
          id: string;
          role: string;
          content: string;
          metadata: Record<string, unknown>;
          created_at: string;
        }>;
      }>(`/api/admin/tables/${table_id}/chat`),
    ask: (table_id: string, prompt: string) =>
      request<{ agent_run_id: string }>(`/api/admin/tables/${table_id}/ask`, {
        method: "POST",
        body: JSON.stringify({ prompt }),
      }),
    eventsUrl: (agent_run_id: string) =>
      `${API_URL}/api/admin/tables/agent-runs/${agent_run_id}/events`,
  },
  tableRevisions: (table_id: string) =>
    request<
      Array<{
        id: string;
        revision: number;
        payload: Record<string, unknown>;
        actor: string | null;
        reason: string | null;
        created_at: string;
      }>
    >(`/api/admin/tables/${table_id}/revisions`),
  client: {
    listPublicSources: () =>
      request<Array<{ id: string; name: string; database: string; readonly_verified: boolean }>>(
        "/api/sources/public",
      ),
    listSessions: () =>
      request<{ items: Array<{ id: string; title: string | null; last_activity_at: string; source_id: string | null; created_at: string }> }>(
        "/api/sessions",
      ),
    createSession: (payload: { source_id?: string; title?: string }) =>
      request<{ id: string; title: string | null; last_activity_at: string; source_id: string | null; created_at: string }>(
        "/api/sessions",
        { method: "POST", body: JSON.stringify(payload) },
      ),
    listMessages: (session_id: string) =>
      request<Array<{ id: string; role: string; content: string; metadata: Record<string, unknown>; created_at: string }>>(
        `/api/sessions/${session_id}/messages`,
      ),
    deleteSession: (session_id: string) =>
      request<void>(`/api/sessions/${session_id}`, { method: "DELETE" }),
    activeTask: (session_id: string) =>
      request<{
        task_id: string;
        agent_run_id: string;
        prompt: string;
        status: string;
        live: boolean;
      } | null>(`/api/sessions/${session_id}/active-task`),
    startTask: (session_id: string, source_id: string, prompt: string) =>
      request<{ task_id: string; agent_run_id: string }>("/api/tasks", {
        method: "POST",
        body: JSON.stringify({ session_id, source_id, prompt }),
      }),
    getTask: (task_id: string) => request<unknown>(`/api/tasks/${task_id}`),
    tasksEventsUrl: (agent_run_id: string) =>
      `${API_URL}/api/tasks/agent-runs/${agent_run_id}/events`,
    respondTask: (agent_run_id: string, answer: string) =>
      request<{ accepted: boolean }>(
        `/api/tasks/agent-runs/${agent_run_id}/respond`,
        { method: "POST", body: JSON.stringify({ answer }) },
      ),
    cancelTask: (agent_run_id: string) =>
      request<{ cancelled: boolean }>(
        `/api/tasks/agent-runs/${agent_run_id}/cancel`,
        { method: "POST" },
      ),
    rerunSql: (task_id: string, sql: string) =>
      request<
        | {
            ok: true;
            sql: string;
            preview: { columns: string[]; rows: unknown[][] };
            rowcount: number;
            export_url: string | null;
          }
        | { ok: false; error: string; kind: "guard" | "parse" | "execute" }
      >(`/api/tasks/${task_id}/rerun-sql`, {
        method: "POST",
        body: JSON.stringify({ sql }),
      }),
    exportUrl: (task_id: string) => `${API_URL}/api/tasks/${task_id}/export.xlsx`,
  },
  audit: {
    list: (params?: { entity_kind?: string; entity_id?: string; limit?: number }) => {
      const qs = new URLSearchParams();
      if (params?.entity_kind) qs.set("entity_kind", params.entity_kind);
      if (params?.entity_id) qs.set("entity_id", params.entity_id);
      if (params?.limit) qs.set("limit", String(params.limit));
      return request<unknown[]>(`/api/admin/audit?${qs.toString()}`);
    },
  },
  health: () => request<{ status: string }>("/healthz"),
};
