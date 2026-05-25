-- Нужно для переподписки SSE-стрима после рефреша страницы. Без этой колонки
-- мы не знаем agent_run_id незавершённого таска, и фронт не может найти его
-- в in-memory RunRegistry для повторного подключения к стриму.

ALTER TABLE task_runs
    ADD COLUMN IF NOT EXISTS agent_run_id text;

CREATE INDEX IF NOT EXISTS task_runs_session_active_idx
    ON task_runs(session_id)
    WHERE status IN ('running', 'awaiting_input');
