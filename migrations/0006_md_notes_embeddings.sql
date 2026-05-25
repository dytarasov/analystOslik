CREATE TABLE md_notes (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           uuid REFERENCES data_sources(id) ON DELETE CASCADE,
    scope               text NOT NULL CHECK (scope IN ('table','column','relation','domain','free')),
    target_id           uuid,
    title               text,
    body_md             text NOT NULL,
    tags                text[] NOT NULL DEFAULT '{}',
    embedding           vector(1536),
    confirmation_status text NOT NULL DEFAULT 'draft',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX md_notes_source_idx ON md_notes(source_id);
CREATE INDEX md_notes_scope_target_idx ON md_notes(scope, target_id);
CREATE INDEX md_notes_tags_idx ON md_notes USING gin(tags);
CREATE INDEX md_notes_embedding_idx ON md_notes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
