CREATE TABLE task_runs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    source_id         uuid REFERENCES data_sources(id) ON DELETE SET NULL,
    status            text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','awaiting_input','done','failed','cancelled')),
    prompt            text NOT NULL,
    params            jsonb NOT NULL DEFAULT '{}'::jsonb,
    result_summary    text,
    result_sql        text,
    result_preview    jsonb,
    result_rowcount   bigint,
    export_path       text,
    error             text,
    started_at        timestamptz,
    finished_at       timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX task_runs_session_idx ON task_runs(session_id, started_at DESC);
