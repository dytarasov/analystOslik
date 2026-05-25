-- Enforce a single active task per chat session, mirroring the profiling
-- single-active guard (uniq_active_profiling_per_source, migration 0012).
--
-- Without this, a double-submit (or an SSE reconnect that re-POSTs) could spawn
-- two concurrent client_task pipelines for the same session — both writing
-- assistant messages and racing on the shared DB session.

-- Pre-flight: collapse any pre-existing active task rows so the partial unique
-- index can build. In-memory AgentRun workers never survive a restart anyway,
-- so flagging stale active rows failed here is consistent with the
-- restart-recovery semantics in main.py (_recover_abandoned_tasks).
UPDATE task_runs
SET status = 'failed',
    error = COALESCE(error, 'abandoned_pre_migration'),
    finished_at = now()
WHERE status IN ('running', 'awaiting_input');

CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_task_per_session
ON task_runs (session_id)
WHERE status IN ('running', 'awaiting_input');
