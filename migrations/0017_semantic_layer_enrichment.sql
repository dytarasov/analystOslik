-- Enrich the semantic layer so the client text-to-SQL agent has the physical
-- and value-level context it needs to write correct WHERE / JOIN / time filters.
--
-- Profiling previously stored only descriptions + approximate distinct/null
-- stats. The agent could not see actual categorical values, the table's keys,
-- the partition column, or the available date range — the three things it most
-- needs. These columns back packages П1 (metadata/values/ranges) and П2
-- (relation cardinality).

-- ── sem_tables: physical metadata + grain ─────────────────────────────────
ALTER TABLE sem_tables
    ADD COLUMN IF NOT EXISTS engine        text,
    ADD COLUMN IF NOT EXISTS total_rows    bigint,
    ADD COLUMN IF NOT EXISTS total_bytes   bigint,
    ADD COLUMN IF NOT EXISTS sorting_key   text,
    ADD COLUMN IF NOT EXISTS partition_key text,
    ADD COLUMN IF NOT EXISTS primary_key   text,
    -- "one row = ..." — the grain of the table, LLM-derived, helps the agent
    -- decide when GROUP BY / dedup is required.
    ADD COLUMN IF NOT EXISTS grain         text,
    -- Catch-all for extra physical/profile info (time coverage summary, etc.).
    ADD COLUMN IF NOT EXISTS profile       jsonb;

-- ── sem_columns: key membership + value catalog + ranges ──────────────────
ALTER TABLE sem_columns
    ADD COLUMN IF NOT EXISTS is_in_sorting_key   boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS is_in_partition_key boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS is_in_primary_key   boolean NOT NULL DEFAULT false,
    -- Top values with frequencies for low-cardinality columns:
    -- [{"value": "...", "count": N}, ...]. The single most useful artefact for
    -- correct WHERE clauses on categorical/flag columns.
    ADD COLUMN IF NOT EXISTS value_catalog jsonb,
    -- For temporal/numeric columns: {"min": ..., "max": ..., "avg": ...,
    -- "median": ...}. Powers relative time windows and scale awareness.
    ADD COLUMN IF NOT EXISTS value_range   jsonb;

-- ── sem_relations: join cardinality + value-overlap evidence ──────────────
ALTER TABLE sem_relations
    ADD COLUMN IF NOT EXISTS cardinality text,        -- '1:1' | 'N:1' | '1:N' | 'N:N'
    ADD COLUMN IF NOT EXISTS match_ratio numeric;     -- sampled FK→PK overlap [0..1]
