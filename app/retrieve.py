from app.db import DatabaseAdapter
from app.embeddings import embed_texts


_VECTOR_SQL = """
SELECT chunk_id, source_doc, section, section_title, effective_date,
       parent_text, embedding <=> %s::vector AS distance
FROM policy_chunks
WHERE superseded_by IS NULL
ORDER BY distance
LIMIT 20
"""

_KEYWORD_SQL = """
SELECT chunk_id, source_doc, section, section_title, effective_date,
       parent_text, embedding <=> %s::vector AS distance
FROM policy_chunks
WHERE superseded_by IS NULL AND tsv @@ websearch_to_tsquery('english', %s)
ORDER BY ts_rank(tsv, websearch_to_tsquery('english', %s)) DESC
LIMIT 20
"""


def _candidate(row) -> dict:
    return {
        "chunk_id": row[0],
        "source_doc": row[1],
        "section": row[2],
        "section_title": row[3],
        "effective_date": row[4],
        "text": row[5],
        "distance": float(row[6]),
    }


def fuse(vector_rows: list[dict], keyword_rows: list[dict], k: int = 60) -> list[tuple]:
    ranks: dict[str, dict] = {}
    for lane, rows in (("vector_rank", vector_rows), ("keyword_rank", keyword_rows)):
        for rank, row in enumerate(rows, start=1):
            ranks.setdefault(row["chunk_id"], {})[lane] = rank
    scored = [
        (
            chunk_id,
            1.0 / (k + lane.get("vector_rank", float("inf")))
            + 1.0 / (k + lane.get("keyword_rank", float("inf"))),
            lane.get("vector_rank", float("inf")),
            lane.get("keyword_rank", float("inf")),
        )
        for chunk_id, lane in ranks.items()
    ]
    scored.sort(key=lambda entry: (-entry[1], entry[2], entry[3]))
    return [(chunk_id, score) for chunk_id, score, _, _ in scored]


def search(question: str, adapter: DatabaseAdapter) -> list[dict]:
    query_vector = embed_texts([question])[0]
    with adapter.connect() as conn:
        vector_rows = [
            _candidate(row) for row in conn.execute(_VECTOR_SQL, (query_vector,)).fetchall()
        ]
        keyword_rows = [
            _candidate(row)
            for row in conn.execute(_KEYWORD_SQL, (query_vector, question, question)).fetchall()
        ]
    by_chunk_id = {row["chunk_id"]: row for row in [*vector_rows, *keyword_rows]}
    fused = fuse(vector_rows, keyword_rows)[:20]
    return [
        {**by_chunk_id[chunk_id], "rrf_score": float(score)}
        for chunk_id, score in fused
    ]
