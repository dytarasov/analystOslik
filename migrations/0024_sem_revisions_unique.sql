-- Guard revision numbering against the race in add_revision (MAX+1 read then
-- insert): concurrent edits + background regenerate could otherwise collide on
-- the same revision number. De-dup any pre-existing collisions first.
DELETE FROM sem_revisions a
USING sem_revisions b
WHERE a.entity_kind = b.entity_kind
  AND a.entity_id = b.entity_id
  AND a.revision = b.revision
  AND a.ctid > b.ctid;

ALTER TABLE sem_revisions
    ADD CONSTRAINT sem_revisions_entity_rev_uq
    UNIQUE (entity_kind, entity_id, revision);
