from app.db import DatabaseAdapter
from app.embeddings import embed_texts


def search(question: str, adapter: DatabaseAdapter) -> list[dict]:
    query_vector = embed_texts([question])[0]
    with adapter.connect() as conn:
        rows = conn.execute(
            """
            SELECT source_doc, section, section_title, effective_date,
                   parent_text, distance
            FROM (
                SELECT DISTINCT ON (source_doc, section)
                    source_doc, section, section_title, effective_date,
                    parent_text, embedding <=> %s::vector AS distance
                FROM policy_chunks
                WHERE superseded_by IS NULL
                ORDER BY source_doc, section, embedding <=> %s::vector
            ) AS sections
            ORDER BY distance
            LIMIT 3
            """,
            (query_vector, query_vector),
        ).fetchall()
    return [
        {
            "source_doc": row[0],
            "section": row[1],
            "section_title": row[2],
            "effective_date": row[3],
            "text": row[4],
            "distance": float(row[5]),
        }
        for row in rows
    ]
