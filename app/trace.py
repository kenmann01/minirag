# Internal and Confidential - Not for External Distribution.
"""Run one ask and record what each retrieval stage held."""

from app.cache import lookup, store
from app.db import DatabaseAdapter
from app.embeddings import embed_texts
from app.generate import REFUSAL, LanguageModel, generate
from app.reranker import Reranker
from app.retrieve import (
    _KEYWORD_SQL,
    _KEYWORD_SQL_ALL,
    _VECTOR_SQL,
    _VECTOR_SQL_ALL,
    _candidate,
    fuse,
)

_INGEST_SQL = """
SELECT chunk_id, source_doc, section, section_title, effective_date, superseded_by
FROM policy_chunks
ORDER BY source_doc, section, chunk_id
"""


def trace_ask(
    question: str,
    *,
    database_adapter: DatabaseAdapter,
    language_model: LanguageModel,
    reranker: Reranker,
    include_superseded: bool = False,
) -> dict:
    """Answer a question and return the answer JSON plus each stage of that run."""
    if not question.strip():
        return {"error": "Enter a question."}
    try:
        return _trace(
            question,
            database_adapter=database_adapter,
            language_model=language_model,
            reranker=reranker,
            include_superseded=include_superseded,
        )
    except Exception as exc:
        return {"error": str(exc)}


def _trace(
    question: str,
    *,
    database_adapter: DatabaseAdapter,
    language_model: LanguageModel,
    reranker: Reranker,
    include_superseded: bool,
) -> dict:
    if not include_superseded:
        cached = lookup(question, database_adapter)
        if cached is not None:
            return {
                "response": cached.model_dump(),
                "trace": [
                    {"name": "cache", "status": "hit", "response": cached.model_dump()},
                ],
            }
    stored_chunks = _stored_chunks(database_adapter)
    vector_rows, keyword_rows, keyword_query = _lanes(
        question, database_adapter, include_superseded=include_superseded
    )
    by_chunk_id = {row["chunk_id"]: row for row in [*vector_rows, *keyword_rows]}
    fused = [
        {**by_chunk_id[chunk_id], "rrf_score": float(score)}
        for chunk_id, score in fuse(vector_rows, keyword_rows)[:20]
    ]
    ranked = reranker.rank(question, fused)
    response = generate(question, ranked, language_model)
    stored = response.answer != REFUSAL and not include_superseded
    if stored:
        store(question, response, database_adapter)
    trace = [{"name": "ingest", "chunks": stored_chunks}]
    if not include_superseded:
        trace.append({"name": "cache", "status": "miss"})
    trace.extend(
        [
            {"name": "question_embedding", "chunks": vector_rows},
            {"name": "keyword_search", "query": keyword_query, "chunks": keyword_rows},
            {"name": "rrf", "chunks": fused},
            {"name": "cross_encoder", "chunks": ranked},
            {
                "name": "passed_for_generation",
                "chunks": ranked,
                "answer": response.answer,
                "citation": response.citation.model_dump() if response.citation else None,
            },
            {"name": "store", "stored": stored},
        ]
    )
    return {"response": response.model_dump(), "trace": trace}


def _stored_chunks(adapter: DatabaseAdapter) -> list[dict]:
    with adapter.connect() as conn:
        rows = conn.execute(_INGEST_SQL).fetchall()
    return [
        {
            "chunk_id": row[0],
            "source_doc": row[1],
            "section": row[2],
            "section_title": row[3],
            "effective_date": row[4],
            "superseded": row[5] is not None,
        }
        for row in rows
    ]


def _lanes(
    question: str, adapter: DatabaseAdapter, *, include_superseded: bool
) -> tuple[list[dict], list[dict], str]:
    query_vector = embed_texts([question])[0]
    vector_sql = _VECTOR_SQL_ALL if include_superseded else _VECTOR_SQL
    keyword_sql = _KEYWORD_SQL_ALL if include_superseded else _KEYWORD_SQL
    with adapter.connect() as conn:
        vector_rows = [
            _candidate(row) for row in conn.execute(vector_sql, (query_vector,)).fetchall()
        ]
        keyword_query = conn.execute(
            "SELECT websearch_to_tsquery('english', %s)::text",
            (question,),
        ).fetchone()[0]
        keyword_rows = [
            _candidate(row)
            for row in conn.execute(keyword_sql, (query_vector, question, question)).fetchall()
        ]
    return vector_rows, keyword_rows, keyword_query or ""
