CREATE TABLE sem_tables (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id            uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    database             text NOT NULL,
    table_name           text NOT NULL,
    title                text,
    description          text,
    domain               text,
    tags                 text[] NOT NULL DEFAULT '{}',
    confirmation_status  text NOT NULL DEFAULT 'draft' CHECK (confirmation_status IN ('draft','confirmed','needs_review')),
    user_notes           text,
    confirmed_at         timestamptz,
    confirmed_by         text,
    last_run_id          uuid REFERENCES profiling_runs(id) ON DELETE SET NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, database, table_name)
);
CREATE INDEX sem_tables_tags_idx ON sem_tables USING gin(tags);

CREATE TABLE sem_columns (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    table_id             uuid NOT NULL REFERENCES sem_tables(id) ON DELETE CASCADE,
    name                 text NOT NULL,
    position             int  NOT NULL,
    data_type            text NOT NULL,
    description          text,
    semantic_role        text,
    user_notes           text,
    null_ratio           numeric,
    distinct_count       bigint,
    total_count          bigint,
    examples             jsonb,
    confirmation_status  text NOT NULL DEFAULT 'draft' CHECK (confirmation_status IN ('draft','confirmed','needs_review')),
    confirmed_at         timestamptz,
    confirmed_by         text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (table_id, name)
);

CREATE TABLE sem_relations (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id            uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    from_table_id        uuid NOT NULL REFERENCES sem_tables(id) ON DELETE CASCADE,
    from_column_id       uuid REFERENCES sem_columns(id) ON DELETE CASCADE,
    to_table_id          uuid NOT NULL REFERENCES sem_tables(id) ON DELETE CASCADE,
    to_column_id         uuid REFERENCES sem_columns(id) ON DELETE CASCADE,
    kind                 text NOT NULL CHECK (kind IN ('fk','inferred','conceptual')),
    confidence           numeric NOT NULL DEFAULT 1.0,
    reasoning            text,
    confirmation_status  text NOT NULL DEFAULT 'draft' CHECK (confirmation_status IN ('draft','confirmed','needs_review')),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sem_relations_from_idx ON sem_relations(from_table_id);
CREATE INDEX sem_relations_to_idx ON sem_relations(to_table_id);

CREATE TABLE sem_metrics (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id     uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    name          text NOT NULL,
    expression    text NOT NULL,
    unit          text,
    description   text,
    confirmation_status text NOT NULL DEFAULT 'draft',
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, name)
);

CREATE TABLE sem_glossary (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   uuid REFERENCES data_sources(id) ON DELETE CASCADE,
    term        text NOT NULL,
    definition  text NOT NULL,
    synonyms    text[] NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, term)
);
CREATE INDEX sem_glossary_syn_idx ON sem_glossary USING gin(synonyms);

CREATE TABLE sem_revisions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_kind  text NOT NULL,
    entity_id    uuid NOT NULL,
    revision     int NOT NULL,
    payload      jsonb NOT NULL,
    actor        text,
    reason       text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sem_revisions_entity_idx ON sem_revisions(entity_kind, entity_id, revision DESC);
