-- Human-authored glossary per source: house rules, field semantics, gold SQL
-- and metrics. Injected verbatim into the client agent's system prompt (Phase 1)
-- and structurally ingested into the semantic layer (notes/metrics/relations).
ALTER TABLE data_sources
    ADD COLUMN IF NOT EXISTS glossary_md          text,
    ADD COLUMN IF NOT EXISTS glossary_ingested_at timestamptz;
