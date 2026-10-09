"""Storage self-healing: older table shapes are rebuilt, never silently used."""

import pytest

from app.config import Settings
from app.corpus.ingest import DEFAULT_CORPUS_DIR, _chunks, _ensure_schema, corpus_path
from app.corpus.ingest import run as run_ingest
from app.retrieval.retrieve import _guard_embedding_model
from app.storage.pgadapter import PgAdapter


@pytest.fixture(scope="module", autouse=True)
def _restore_corpus():
    """Leave the corpus ingested after the table surgery below."""
    yield
    run_ingest(PgAdapter())


def test_an_older_chunks_table_is_rebuilt_and_the_guard_waits():
    adapter = PgAdapter()
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS policy_chunks")
        conn.execute("CREATE TABLE policy_chunks (chunk_id TEXT PRIMARY KEY)")
    with adapter.connect() as conn:
        # No embedding_model column yet: the guard must return without raising.
        _guard_embedding_model(conn)
        _ensure_schema(conn)
        columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'policy_chunks'"
            ).fetchall()
        }
        assert {"parent_text", "tsv", "embedding_model"} <= columns
    with adapter.connect() as conn:
        # The rebuilt table is empty: no stored model to disagree with.
        _guard_embedding_model(conn)
        assert conn.execute("SELECT count(*) FROM policy_chunks").fetchone()[0] == 0


def test_a_missing_corpus_directory_yields_no_chunks(tmp_path):
    assert _chunks(tmp_path / "nowhere") == []


def test_an_unset_corpus_dir_falls_back_to_the_default(monkeypatch):
    monkeypatch.setattr(
        "app.corpus.ingest.get_settings",
        lambda: Settings(database_url="postgresql://minirag:minirag@127.0.0.1:5432/minirag", corpus_dir=""),
    )
    assert corpus_path(None) == DEFAULT_CORPUS_DIR


def test_a_foreign_embedder_is_rejected_until_reingest():
    from app.retrieval.retrieve import EmbeddingModelMismatch, _guard_embedding_model

    adapter = PgAdapter()
    vector = "[" + ",".join(["0.0"] * 768) + "]"
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS policy_chunks")
        conn.execute(
            """
            CREATE TABLE policy_chunks (
                chunk_id TEXT PRIMARY KEY,
                source_doc TEXT NOT NULL,
                section TEXT NOT NULL,
                section_title TEXT NOT NULL,
                parent_text TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding vector(768) NOT NULL,
                embedding_model TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT INTO policy_chunks (
                chunk_id, source_doc, section, section_title, parent_text, text,
                embedding, embedding_model
            )
            VALUES ('guard-probe', 'd.md', '1', 'T', 'p', 't', %s::vector, 'another/embedder')
            """,
            (vector,),
        )
    with adapter.connect() as conn:
        with pytest.raises(EmbeddingModelMismatch, match="re-run"):
            _guard_embedding_model(conn)
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS policy_chunks")


def test_a_top_k_below_one_is_rejected_before_any_work():
    from app.retrieval.retrieve import search

    class NoDatabase:
        def connect(self):
            raise AssertionError("search must validate top_k before touching the database")

    with pytest.raises(ValueError, match="top_k must be at least 1"):
        search("any question", NoDatabase(), top_k=0)
