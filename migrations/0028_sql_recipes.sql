-- Split SQL examples out of the glossary into their own "SQL notes" area.
-- The glossary is injected verbatim into the agent's prompt, so bundling gold
-- SQL there bloated the context; recipes now live separately and are retrieved
-- on demand by the find_sql_recipes tool. The natural-language "intent" is what
-- gets embedded (the SQL itself is stored verbatim and never vectorized).
ALTER TABLE data_sources
    ADD COLUMN IF NOT EXISTS sql_notes_md          text,
    ADD COLUMN IF NOT EXISTS sql_notes_ingested_at timestamptz;

CREATE TABLE IF NOT EXISTS sql_recipes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    title       text NOT NULL,
    intent      text NOT NULL DEFAULT '',
    sql         text NOT NULL,
    tables      text[] NOT NULL DEFAULT '{}',
    embedding   vector(1024),          -- on intent (NL), never on the SQL
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS sql_recipes_source_idx ON sql_recipes (source_id);
