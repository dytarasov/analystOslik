-- Durable task state for the redesigned profiling pipeline.
--
-- The new pipeline is two-pass (dry structural harvest → grouped LLM
-- profiling) and runs many units of work concurrently. Each unit is a row
-- here so that:
--   * nothing is lost — every column is accounted for by a task;
--   * the whole run is resumable after a restart (re-enqueue non-terminal);
--   * questions to the admin are first-class, persisted, non-blocking tasks.

CREATE TABLE profiling_tasks (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id             uuid NOT NULL REFERENCES profiling_runs(id) ON DELETE CASCADE,
    source_id          uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    kind               text NOT NULL CHECK (kind IN (
                          'harvest_table',    -- pass 1: dry structural+stats collection
                          'describe_group',   -- pass 2: LLM profiling of 1-3 related columns
                          'question',         -- a clarification awaiting the admin
                          'relations',        -- cross-table relation inference
                          'synthesize'        -- source-level glossary/metrics/overview
                       )),
    -- Stable unit key, e.g. 'cdm.events' or 'cdm.events#grp:lesson_*'.
    target             text NOT NULL,
    -- Coverage accounting: which columns this task is responsible for.
    database           text,
    table_name         text,
    columns            text[] NOT NULL DEFAULT '{}',
    status             text NOT NULL DEFAULT 'pending' CHECK (status IN (
                          'pending', 'running', 'awaiting_input',
                          'blocked', 'done', 'failed', 'skipped'
                       )),
    attempts           int NOT NULL DEFAULT 0,
    -- Hash of the task inputs — lets a re-run skip already-completed identical work.
    input_fingerprint  text,
    depends_on         uuid[] NOT NULL DEFAULT '{}',
    payload            jsonb,   -- inputs / question schema / group spec
    result             jsonb,   -- produced output
    error              text,
    started_at         timestamptz,
    finished_at        timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX profiling_tasks_run_idx ON profiling_tasks(run_id);
CREATE INDEX profiling_tasks_run_status_idx ON profiling_tasks(run_id, status);
CREATE INDEX profiling_tasks_kind_idx ON profiling_tasks(run_id, kind);

-- One task per (run, kind, target) — idempotent enqueue / no duplicate work.
CREATE UNIQUE INDEX uniq_profiling_task_unit
    ON profiling_tasks(run_id, kind, target);
