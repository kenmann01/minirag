# Internal and Confidential - Not for External Distribution.
"""Retrieve-only bridge. It returns chunk JSON and never generates."""

import sys

from app.config import get_settings
from app.db import DatabaseAdapter
from app.retrieve import search

_FIELDS = (
    "chunk_id",
    "source_doc",
    "section",
    "section_title",
    "effective_date",
    "text",
    "distance",
    "rrf_score",
)


def retrieve(query: str, adapter: DatabaseAdapter, *, top_k: int = 20) -> dict:
    """Return fused chunks for a query without reranking or generation.

    Each returned chunk id is written to stderr so a miss stays visible.
    The reranker is intentionally not called: it deduplicates by section
    and would drop chunks the log is supposed to keep.

    Args:
        query: Text embedded and matched against the corpus.
        adapter: Provider of a managed vector-capable SQL connection.
        top_k: Maximum chunks to return. Defaults to the same 20 as ask and eval.

    Returns:
        A JSON-ready object with the query, the configured embedding model,
        and the chunk records.
    """
    found = search(query, adapter, top_k=top_k)
    chunks = [{field: chunk[field] for field in _FIELDS} for chunk in found]
    for chunk in chunks:
        print(chunk["chunk_id"], file=sys.stderr)
    return {
        "query": query,
        "model": get_settings().embedding_model,
        "chunks": chunks,
    }
