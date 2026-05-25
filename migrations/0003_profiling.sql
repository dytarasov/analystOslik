CREATE TABLE profiling_runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id     uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    status        text NOT NULL DEFAULT 'pending',
    requested_by  text,
    params        jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at    timestamptz,
    finished_at   timestamptz,
    error         text,
    stats         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX profiling_runs_source_started_idx ON profiling_runs(source_id, started_at DESC);

CREATE TABLE profiling_run_tables (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        uuid NOT NULL REFERENCES profiling_runs(id) ON DELETE CASCADE,
    database      text NOT NULL,
    table_name    text NOT NULL,
    status        text NOT NULL DEFAULT 'pending',
    ddl           text,
    sample        jsonb,
    column_stats  jsonb,
    usage_stats   jsonb,
    error         text,
    started_at    timestamptz,
    finished_at   timestamptz,
    UNIQUE (run_id, database, table_name)
);
CREATE INDEX profiling_run_tables_run_idx ON profiling_run_tables(run_id);
