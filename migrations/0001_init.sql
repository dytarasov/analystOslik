CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version int PRIMARY KEY,
    name    text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
