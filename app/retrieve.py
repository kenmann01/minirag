from app.db import DatabaseAdapter
from app.embeddings import embed_texts


def search(question: str, adapter: DatabaseAdapter) -> list[dict]:
    query_vector = embed_texts([question])[0]
    with adapter.connect() as conn:
        rows = conn.execute(
            """
            SELECT document, version, section, section_title, text,
                   embedding <=> %s::vector AS distance
            FROM policy_chunks
            ORDER BY embedding <=> %s::vector ASC
            LIMIT 3
            """,
            (query_vector, query_vector),
        ).fetchall()
    return [
        {
            "document": row[0],
            "version": row[1],
            "section": row[2],
            "section_title": row[3],
            "text": row[4],
            "distance": float(row[5]),
        }
        for row in rows
    ]
