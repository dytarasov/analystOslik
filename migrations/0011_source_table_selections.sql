-- Whitelist of tables that the admin explicitly approved for profiling.
-- This whitelist is the single source of truth: both the admin pipeline
-- (profiling) and the client pipeline (SQL guard / agent context) restrict
-- themselves to these tables.

CREATE TABLE source_table_selections (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id    uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    database     text NOT NULL,
    table_name   text NOT NULL,
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, database, table_name)
);
CREATE INDEX source_table_selections_source_idx ON source_table_selections(source_id);
