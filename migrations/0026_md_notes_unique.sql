-- Table/column notes must be one-per-target. Concurrent write_source_notes
-- (e.g. a double-fired profiling resume) raced the SELECT-then-INSERT upsert and
-- produced duplicate notes — polluting RAG and making the per-column toggle's
-- table-note refresh pick an arbitrary copy. Dedup, then enforce uniqueness so
-- the upsert (now savepoint-guarded) can never duplicate again.
--
-- 'free'/glossary notes (scope='free', target_id = source_id or NULL) legitimately
-- have many rows per target, so the index is partial to table/column only.

DELETE FROM md_notes a USING md_notes b
WHERE a.scope IN ('table', 'column') AND a.scope = b.scope
  AND a.target_id = b.target_id
  AND (a.updated_at < b.updated_at OR (a.updated_at = b.updated_at AND a.id < b.id));

CREATE UNIQUE INDEX IF NOT EXISTS md_notes_scope_target_uq
    ON md_notes (scope, target_id)
    WHERE scope IN ('table', 'column');
