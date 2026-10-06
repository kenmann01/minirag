CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_chunks (
    chunk_id TEXT PRIMARY KEY,
    source_doc TEXT NOT NULL,
    section TEXT NOT NULL,
    section_title TEXT NOT NULL,
    effective_date TEXT,
    superseded_by TEXT,
    parent_text TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding vector(768) NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
);

CREATE INDEX IF NOT EXISTS policy_chunks_tsv_gin ON policy_chunks USING GIN (tsv);
