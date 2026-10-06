import hashlib

from app.config import get_settings
from app.db import DatabaseAdapter
from app.generate import PROMPT_PATH
from app.schemas import AskResponse

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS question_cache (
    cache_key TEXT PRIMARY KEY,
    response TEXT NOT NULL
)
"""


def normalize_question(question: str) -> str:
    return " ".join(question.split()).lower()


def cache_key(question: str) -> str:
    settings = get_settings()
    material = "\0".join(
        [
            normalize_question(question),
            settings.embedding_model,
            PROMPT_PATH.stem,
            settings.ollama_model,
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()


def lookup(question: str, adapter: DatabaseAdapter) -> AskResponse | None:
    with adapter.connect() as conn:
        exists = conn.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'question_cache'
            """
        ).fetchone()
        if exists is None:
            return None
        row = conn.execute(
            "SELECT response FROM question_cache WHERE cache_key = %s",
            (cache_key(question),),
        ).fetchone()
    if row is None:
        return None
    return AskResponse.model_validate_json(row[0])


def store(question: str, response: AskResponse, adapter: DatabaseAdapter) -> None:
    with adapter.connect() as conn:
        conn.execute(_CREATE_TABLE)
        conn.execute(
            """
            INSERT INTO question_cache (cache_key, response)
            VALUES (%s, %s)
            ON CONFLICT (cache_key) DO UPDATE SET response = EXCLUDED.response
            """,
            (cache_key(question), response.model_dump_json()),
        )
