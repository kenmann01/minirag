from app.db import DatabaseAdapter
from app.embeddings import embed_texts


def search(question: str, adapter: DatabaseAdapter) -> list[dict]:
    query_vector = embed_texts([question])[0]
    with adapter.connect() as conn:
        rows = conn.execute(
            """
            SELECT section, section_title, embedding <=> %s::vector AS distance
            FROM policy_chunks
            ORDER BY embedding <=> %s::vector ASC
            LIMIT 1
            """,
            (query_vector, query_vector),
        ).fetchall()
    return [
        {"section": row[0], "section_title": row[1], "distance": float(row[2])}
        for row in rows
    ]
