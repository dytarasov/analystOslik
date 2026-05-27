-- "locked" marks human-/glossary-curated rows so re-profiling never overwrites
-- their content (title/description/domain/tags/role). Structural facts (stats,
-- keys, engine, examples) still refresh on re-profile regardless.
ALTER TABLE sem_tables  ADD COLUMN IF NOT EXISTS locked boolean NOT NULL DEFAULT false;
ALTER TABLE sem_columns ADD COLUMN IF NOT EXISTS locked boolean NOT NULL DEFAULT false;

-- Backfill: anything already confirmed, or columns enriched from the glossary,
-- counts as curated and must be protected.
UPDATE sem_tables  SET locked = true WHERE confirmation_status = 'confirmed';
UPDATE sem_columns SET locked = true WHERE confirmation_status = 'confirmed';
UPDATE sem_columns SET locked = true WHERE semantics ->> 'source' = 'glossary';
