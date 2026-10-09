# Internal and Confidential - Not for External Distribution.
"""Ingest corpus sections and their embeddings into the vector store."""

from pathlib import Path

from app.config import get_settings
from app.corpus.chunking import split
from app.corpus.embeddings import embed_texts
from app.storage.db import DatabaseAdapter

DEFAULT_CORPUS_DIR = Path(__file__).resolve().parents[2] / "corpora" / "banking"


class EmptyCorpusError(RuntimeError):
    """The corpus produced no chunks, so the stored index was left unchanged."""


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
    embedding_model TEXT NOT NULL DEFAULT '',
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
)
"""

_UPSERT = """
INSERT INTO policy_chunks (
    chunk_id, source_doc, section, section_title, effective_date,
    superseded_by, parent_text, text, embedding, embedding_model
)
VALUES (
    %(chunk_id)s, %(source_doc)s, %(section)s, %(section_title)s,
    %(effective_date)s, %(superseded_by)s, %(parent_text)s, %(text)s,
    %(embedding)s, %(embedding_model)s
)
ON CONFLICT (chunk_id) DO UPDATE SET
    source_doc = EXCLUDED.source_doc,
    section = EXCLUDED.section,
    section_title = EXCLUDED.section_title,
    effective_date = EXCLUDED.effective_date,
    superseded_by = EXCLUDED.superseded_by,
    parent_text = EXCLUDED.parent_text,
    text = EXCLUDED.text,
    embedding = EXCLUDED.embedding,
    embedding_model = EXCLUDED.embedding_model
"""


_CREATE_TSV_INDEX = """
CREATE INDEX IF NOT EXISTS policy_chunks_tsv_gin ON policy_chunks USING GIN (tsv)
"""

_CREATE_SECTIONS = """
CREATE TABLE IF NOT EXISTS sections (
    source_doc TEXT NOT NULL,
    section TEXT NOT NULL,
    section_title TEXT NOT NULL,
    parent_text TEXT NOT NULL,
    effective_date TEXT,
    superseded_by TEXT,
    PRIMARY KEY (source_doc, section)
)
"""

_UPSERT_SECTION = """
INSERT INTO sections (
    source_doc, section, section_title, parent_text, effective_date, superseded_by
)
VALUES (
    %(source_doc)s, %(section)s, %(section_title)s, %(parent_text)s,
    %(effective_date)s, %(superseded_by)s
)
ON CONFLICT (source_doc, section) DO UPDATE SET
    section_title = EXCLUDED.section_title,
    parent_text = EXCLUDED.parent_text,
    effective_date = EXCLUDED.effective_date,
    superseded_by = EXCLUDED.superseded_by
"""

_DELETE_STALE_SECTIONS = """
DELETE FROM sections
WHERE NOT EXISTS (
    SELECT 1
    FROM unnest(%(docs)s::text[], %(sections)s::text[]) AS kept(doc, sec)
    WHERE source_doc = kept.doc AND section = kept.sec
)
"""

_SECTION_COLUMNS = {"source_doc", "section", "section_title", "parent_text"}


def _ensure_schema(conn) -> None:
    """Create the vector extension, table, and keyword index when missing.

    A table from an older schema (missing ``parent_text``, ``tsv``, or
    ``embedding_model``) is dropped and rebuilt; the ingest that follows
    repopulates it. Rebuilding on a missing ``embedding_model`` is what
    forces stale vectors from another embedder out of the store.
    """
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    columns = conn.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'policy_chunks'
          AND column_name IN ('parent_text', 'tsv', 'embedding_model')
        """).fetchall()
    if len(columns) < 3:
        conn.execute("DROP TABLE IF EXISTS policy_chunks")
        conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_TSV_INDEX)


def _ensure_sections_schema(conn) -> None:
    """Create the sections table when missing and rebuild an older shape.

    A sections table missing any required column is dropped; the ingest that
    follows repopulates it from the chunk lineage.
    """
    columns = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'sections'"
    ).fetchall()
    if columns and not {row[0] for row in columns} >= _SECTION_COLUMNS:
        conn.execute("DROP TABLE IF EXISTS sections")
        columns = []
    if not columns:
        conn.execute(_CREATE_SECTIONS)


def corpus_path(corpus_dir: Path | None = None) -> Path:
    """Resolve the markdown folder. An explicit path wins, then ``CORPUS_DIR``.

    Relative ``CORPUS_DIR`` values are anchored at the repository root so the
    same setting works from any working directory.
    """
    if corpus_dir is not None:
        return corpus_dir
    configured = get_settings().corpus_dir.strip()
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            candidate = DEFAULT_CORPUS_DIR.parents[1] / candidate
        return candidate
    return DEFAULT_CORPUS_DIR


def _chunks(directory: Path) -> list[dict]:
    chunks: list[dict] = []
    if not directory.is_dir():
        return chunks
    for path in sorted(directory.rglob("*.md")):
        chunks.extend(split(path.read_text(encoding="utf-8"), path.name))
    return chunks


def run(adapter: DatabaseAdapter, corpus_dir: Path | None = None) -> int:
    """Replace stored chunks with every embedded corpus document.

    Args:
        adapter: Provider of a managed vector-capable SQL connection.
        corpus_dir: Markdown folder to ingest, subfolders included.
            Defaults to ``CORPUS_DIR`` or ``corpora/banking``.

    Returns:
        The number of chunks ingested.

    Raises:
        EmptyCorpusError: The folder yielded no chunks. Nothing is deleted.

    Side Effects:
        Creates the vector extension and tables when needed, upserts current
        chunks plus one sections row per parent section, stamps the embedding
        model on every chunk, and deletes stale rows from both tables.
    """
    directory = corpus_path(corpus_dir)
    chunks = _chunks(directory)
    if not chunks:
        raise EmptyCorpusError(f"no chunks in {directory}")
    vectors = embed_texts([chunk["text"] for chunk in chunks])
    embedding_model = get_settings().embedding_model
    sections = {}
    for chunk in chunks:
        sections[(chunk["source_doc"], chunk["section"])] = chunk
    with adapter.connect() as conn:
        _ensure_schema(conn)
        _ensure_sections_schema(conn)
        ids = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            conn.execute(
                _UPSERT, {**chunk, "embedding": vector, "embedding_model": embedding_model}
            )
            ids.append(chunk["chunk_id"])
        conn.execute(
            "DELETE FROM policy_chunks WHERE chunk_id <> ALL(%s)",
            (ids,),
        )
        for chunk in sections.values():
            conn.execute(_UPSERT_SECTION, chunk)
        conn.execute(
            _DELETE_STALE_SECTIONS,
            {
                "docs": [key[0] for key in sections],
                "sections": [key[1] for key in sections],
            },
        )
    return len(chunks)
