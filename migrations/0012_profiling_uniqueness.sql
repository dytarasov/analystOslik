-- Pre-flight: there may already be orphan 'running' rows left behind by a
-- previous backend that crashed before the recovery hook existed. Mark them
-- as failed first, otherwise the partial unique index below cannot be built.
UPDATE profiling_runs
   SET status = 'failed',
       error = COALESCE(error, 'abandoned_on_initial_migration'),
       finished_at = COALESCE(finished_at, now())
 WHERE status IN ('pending', 'running', 'awaiting_input', 'paused');

-- One active profiling run per source: the partial unique index blocks
-- duplicates at the database level, so even a race between two services
-- can't create two parallel runs for the same data source.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_profiling_per_source
    ON profiling_runs(source_id)
    WHERE status IN ('pending', 'running', 'awaiting_input', 'paused');

-- Denormalized "knowledge state" on data_sources so the source list/detail
-- pages can render status without joining profiling_runs.
ALTER TABLE data_sources
    ADD COLUMN IF NOT EXISTS last_profiling_run_id uuid
        REFERENCES profiling_runs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS last_profiled_at timestamptz,
    ADD COLUMN IF NOT EXISTS profiling_status text NOT NULL DEFAULT 'never_profiled';

ALTER TABLE data_sources
    DROP CONSTRAINT IF EXISTS data_sources_profiling_status_check;

ALTER TABLE data_sources
    ADD CONSTRAINT data_sources_profiling_status_check
        CHECK (profiling_status IN (
            'never_profiled',
            'in_progress',
            'profiled',
            'failed',
            'stale'
        ));

-- Backfill from existing profiling history (best run wins: latest 'done' >
-- latest active > latest failed).
WITH ranked AS (
    SELECT
        source_id,
        id,
        status,
        started_at,
        finished_at,
        ROW_NUMBER() OVER (
            PARTITION BY source_id
            ORDER BY
                CASE status
                    WHEN 'done' THEN 1
                    WHEN 'running' THEN 2
                    WHEN 'awaiting_input' THEN 2
                    WHEN 'paused' THEN 2
                    WHEN 'pending' THEN 2
                    WHEN 'failed' THEN 3
                    WHEN 'cancelled' THEN 4
                    ELSE 5
                END,
                COALESCE(finished_at, started_at, created_at) DESC
        ) AS rn
    FROM profiling_runs
)
UPDATE data_sources ds
SET
    last_profiling_run_id = r.id,
    last_profiled_at      = CASE WHEN r.status = 'done' THEN r.finished_at ELSE ds.last_profiled_at END,
    profiling_status      = CASE
        WHEN r.status = 'done' THEN 'profiled'
        WHEN r.status IN ('running', 'awaiting_input', 'paused', 'pending') THEN 'in_progress'
        WHEN r.status = 'failed' THEN 'failed'
        ELSE ds.profiling_status
    END
FROM ranked r
WHERE r.source_id = ds.id AND r.rn = 1;
