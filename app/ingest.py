from pathlib import Path

from app.chunking import split
from app.db import DatabaseAdapter
from app.embeddings import embed_texts

POLICY_DIR = Path(__file__).resolve().parents[1] / "Policy"

_CREATE_TABLE = """
CREATE TABLE policy_chunks (
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
)
"""

_UPSERT = """
INSERT INTO policy_chunks (
    chunk_id, source_doc, section, section_title, effective_date,
    superseded_by, parent_text, text, embedding
)
VALUES (
    %(chunk_id)s, %(source_doc)s, %(section)s, %(section_title)s,
    %(effective_date)s, %(superseded_by)s, %(parent_text)s, %(text)s,
    %(embedding)s
)
ON CONFLICT (chunk_id) DO UPDATE SET
    source_doc = EXCLUDED.source_doc,
    section = EXCLUDED.section,
    section_title = EXCLUDED.section_title,
    effective_date = EXCLUDED.effective_date,
    superseded_by = EXCLUDED.superseded_by,
    parent_text = EXCLUDED.parent_text,
    text = EXCLUDED.text,
    embedding = EXCLUDED.embedding
"""


_CREATE_TSV_INDEX = """
CREATE INDEX IF NOT EXISTS policy_chunks_tsv_gin ON policy_chunks USING GIN (tsv)
"""


def _ensure_schema(conn) -> None:
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    columns = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'policy_chunks'
          AND column_name IN ('parent_text', 'tsv')
        """
    ).fetchall()
    if len(columns) < 2:
        conn.execute("DROP TABLE IF EXISTS policy_chunks")
        conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_TSV_INDEX)


def _chunks() -> list[dict]:
    chunks: list[dict] = []
    for path in sorted(POLICY_DIR.glob("*.md")):
        chunks.extend(split(path.read_text(encoding="utf-8"), path.name))
    return chunks


def run(adapter: DatabaseAdapter) -> int:
    chunks = _chunks()
    vectors = embed_texts([chunk["text"] for chunk in chunks])
    with adapter.connect() as conn:
        _ensure_schema(conn)
        ids = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            conn.execute(_UPSERT, {**chunk, "embedding": vector})
            ids.append(chunk["chunk_id"])
        conn.execute(
            "DELETE FROM policy_chunks WHERE chunk_id <> ALL(%s)",
            (ids,),
        )
    return len(chunks)
