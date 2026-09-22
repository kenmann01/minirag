# Internal and Confidential — Not for External Distribution.
"""Provide pgvector-enabled PostgreSQL connections for application services."""

from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from app.config import get_settings


class PgAdapter:
    """Provide managed PostgreSQL connections with pgvector type support."""

    def connect(self):
        """Create a context manager for a configured PostgreSQL connection.

        Returns:
            A context manager that yields a pgvector-enabled connection.
        """
        return self._connection()

    @contextmanager
    def _connection(self):
        """Yield a connection, committing success and always closing it."""

        conn = psycopg.connect(get_settings().database_url)
        register_vector(conn)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
