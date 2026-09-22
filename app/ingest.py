# Internal and Confidential — Not for External Distribution.
"""Ingest policy sections and their embeddings into the vector store."""

from pathlib import Path

from app.chunking import split
from app.db import DatabaseAdapter
from app.embeddings import embed_texts

POLICY_PATH = Path(__file__).resolve().parents[1] / "policy.md"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS policy_chunks (
    chunk_id TEXT PRIMARY KEY,
    document TEXT NOT NULL,
    version TEXT NOT NULL,
    section TEXT NOT NULL,
    section_title TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding vector(384) NOT NULL
)
"""

_UPSERT = """
INSERT INTO policy_chunks (
    chunk_id, document, version, section, section_title, text, embedding
)
VALUES (
    %(chunk_id)s, %(document)s, %(version)s, %(section)s,
    %(section_title)s, %(text)s, %(embedding)s
)
ON CONFLICT (chunk_id) DO UPDATE SET
    document = EXCLUDED.document,
    version = EXCLUDED.version,
    section = EXCLUDED.section,
    section_title = EXCLUDED.section_title,
    text = EXCLUDED.text,
    embedding = EXCLUDED.embedding
"""


def run(adapter: DatabaseAdapter) -> None:
    """Replace stored policy chunks with the current embedded policy sections.

    Args:
        adapter: Provider of a managed vector-capable SQL connection.

    Side Effects:
        Reads ``policy.md``, creates the vector extension and table when
        needed, upserts current chunks, and deletes stale chunks.
    """
    chunks = split(POLICY_PATH.read_text(encoding="utf-8"))
    vectors = embed_texts([chunk["text"] for chunk in chunks])
    with adapter.connect() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(_CREATE_TABLE)
        ids = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            conn.execute(_UPSERT, {**chunk, "embedding": vector})
            ids.append(chunk["chunk_id"])
        conn.execute(
            "DELETE FROM policy_chunks WHERE chunk_id <> ALL(%s)",
            (ids,),
        )
