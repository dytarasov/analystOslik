CREATE TABLE audit_log (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor         text NOT NULL,
    action        text NOT NULL,
    entity_kind   text,
    entity_id     uuid,
    before        jsonb,
    after         jsonb,
    reason        text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_entity_idx ON audit_log(entity_kind, entity_id);
CREATE INDEX audit_log_actor_idx ON audit_log(actor, created_at DESC);
