from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from app.config import get_settings


class PgAdapter:
    def connect(self):
        return self._connection()

    @contextmanager
    def _connection(self):
        conn = psycopg.connect(get_settings().database_url)
        register_vector(conn)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
