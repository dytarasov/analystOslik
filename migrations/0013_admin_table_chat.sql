-- Admin per-table chat: a dedicated kind plus a target_id so we can pin a
-- session to one sem_tables row.

ALTER TABLE chat_sessions
    DROP CONSTRAINT IF EXISTS chat_sessions_kind_check;

ALTER TABLE chat_sessions
    ADD CONSTRAINT chat_sessions_kind_check
        CHECK (kind IN ('admin', 'client', 'admin_table'));

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS target_id uuid;

CREATE INDEX IF NOT EXISTS chat_sessions_target_idx
    ON chat_sessions(target_id)
    WHERE target_id IS NOT NULL;
