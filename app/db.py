# Internal and Confidential - Not for External Distribution.
"""Define the database connection contract used by application services."""

from contextlib import AbstractContextManager
from typing import Protocol


class DatabaseAdapter(Protocol):
    """Supply managed SQL connections without exposing a database driver.

    Returned connections must accept SQL statements with Python bind
    parameters, including lists bound to ``vector(768)`` values.
    """

    def connect(self) -> AbstractContextManager:
        """Create a context manager that yields an SQL connection.

        Returns:
            A context manager that commits as appropriate and closes the
            connection when its context exits.
        """
        ...
