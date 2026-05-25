-- Soft-delete для чатов: мы прячем сессию, но не теряем связанные
-- chat_messages и task_runs — пригодится для audit log / restore.

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

CREATE INDEX IF NOT EXISTS chat_sessions_active_idx
    ON chat_sessions(kind, last_activity_at DESC)
    WHERE deleted_at IS NULL;
