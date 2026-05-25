CREATE TABLE data_sources (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 text NOT NULL UNIQUE,
    kind                 text NOT NULL CHECK (kind IN ('clickhouse')),
    host                 text NOT NULL,
    port                 int  NOT NULL,
    database             text NOT NULL,
    username             text NOT NULL,
    password_encrypted   bytea NOT NULL,
    secure               boolean NOT NULL DEFAULT false,
    extra_settings       jsonb NOT NULL DEFAULT '{}'::jsonb,
    readonly_verified    boolean NOT NULL DEFAULT false,
    last_test_at         timestamptz,
    last_test_status     text,
    last_test_error      text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
