CREATE TABLE chat_sessions (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind              text NOT NULL CHECK (kind IN ('admin','client')),
    cookie_id         text,
    source_id         uuid REFERENCES data_sources(id) ON DELETE SET NULL,
    title             text,
    last_activity_at  timestamptz NOT NULL DEFAULT now(),
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX chat_sessions_cookie_idx ON chat_sessions(cookie_id);
CREATE INDEX chat_sessions_kind_act_idx ON chat_sessions(kind, last_activity_at DESC);

CREATE TABLE chat_messages (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        text NOT NULL CHECK (role IN ('user','assistant','system','tool')),
    content     text NOT NULL,
    metadata    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX chat_messages_session_idx ON chat_messages(session_id, created_at);

CREATE TABLE client_sessions_meta (
    cookie_id        text PRIMARY KEY,
    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now(),
    requests_count   int NOT NULL DEFAULT 0,
    user_agent       text,
    ip_hash          text
);
