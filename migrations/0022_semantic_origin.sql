-- Origin marker so glossary-derived rows can be cleared on re-ingest without
-- touching profiling-derived ones (both pipelines write these tables).
-- NULL = pre-existing / profiling; 'glossary' = produced by GlossaryService.
ALTER TABLE sem_metrics   ADD COLUMN IF NOT EXISTS origin text;
ALTER TABLE sem_glossary  ADD COLUMN IF NOT EXISTS origin text;
ALTER TABLE sem_relations ADD COLUMN IF NOT EXISTS origin text;
