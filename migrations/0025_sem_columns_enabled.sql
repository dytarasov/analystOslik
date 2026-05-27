-- "enabled" lets an admin exclude a column from all downstream investigation:
-- it disappears from the client agent's context (structured tools, schema,
-- relations, graph, RAG) and is rejected by the SQL guard. The hard facts
-- harvested in pass-1 stay in the row, so re-enabling is cheap and the catalog
-- stays complete. Like "locked", this survives a re-profile (upsert_column
-- never resets it) — disabling a column is a durable human decision.
ALTER TABLE sem_columns ADD COLUMN IF NOT EXISTS enabled boolean NOT NULL DEFAULT true;

-- Partial index: the agent-facing queries always filter enabled = true, and the
-- disabled set is the small minority.
CREATE INDEX IF NOT EXISTS sem_columns_enabled_idx
    ON sem_columns (table_id) WHERE enabled = true;
