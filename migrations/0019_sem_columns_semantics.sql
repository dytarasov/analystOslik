-- LLM-derived business semantics per column (pass 2 of the redesigned
-- profiling). Kept as one jsonb blob so the analyst-facing fields can evolve
-- without a migration each time:
--   {unit, pii, value_meanings: {<value>: <meaning>}, safe_to_group_by,
--    safe_to_filter_by, caveats, suggested_aggregation, confidence}
ALTER TABLE sem_columns
    ADD COLUMN IF NOT EXISTS semantics jsonb;
