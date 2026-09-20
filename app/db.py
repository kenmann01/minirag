from contextlib import AbstractContextManager
from typing import Protocol


class DatabaseAdapter(Protocol):
    """Connection must accept SQL with Python bind params. No driver imports here."""

    def connect(self) -> AbstractContextManager:
        """Yield a connection and close it on exit."""
        ...
