-- Resize md_notes.embedding from vector(1536) (OpenAI text-embedding-3-small)
-- to vector(1024) (BAAI/bge-m3, intfloat/multilingual-e5-large, etc.).
--
-- Safe because md_notes is rebuilt by the profiling pipeline; existing rows
-- (if any) lose their embedding and will get re-embedded on the next run.

DROP INDEX IF EXISTS md_notes_embedding_idx;

ALTER TABLE md_notes
    ALTER COLUMN embedding TYPE vector(1024) USING NULL;

CREATE INDEX md_notes_embedding_idx
    ON md_notes USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
