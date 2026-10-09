# Internal and Confidential - Not for External Distribution.
"""Session-wide pins for the suite: the embedder, and an isolated database.

pydantic-settings resolves values with this precedence: init arguments,
then real environment variables, then the ``.env`` file, then field
defaults. Module-level assignments here therefore beat any ``.env``
overrides and restore the code defaults (``app/config.py``) that CI runs
with. They sit at import time of this conftest, which pytest loads before
collecting or importing any test module, so no ``get_settings()`` call can
observe the ``.env`` override.

The suite also drops and rebuilds ``policy_chunks``, ``sections``,
``runs``, and the graph tables, so ``DATABASE_URL`` is redirected to a
sibling ``minirag_test`` database created on demand. The developer rig's
stored runs and ingested corpus are never touched by a test run.

Issue #38.
"""

import os
from urllib.parse import urlsplit, urlunsplit

import pytest

PINNED_EMBEDDING_MODEL = "Alibaba-NLP/gte-modernbert-base"

os.environ["EMBEDDING_MODEL"] = PINNED_EMBEDDING_MODEL

TEST_DATABASE = "minirag_test"


def _redirect_to_test_database() -> None:
    """Create ``minirag_test`` next to the configured database and point the suite at it."""
    from app.config import Settings

    url = Settings().database_url
    parts = urlsplit(url)
    if parts.path.lstrip("/") == TEST_DATABASE:
        return
    server = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))
    test_url = urlunsplit(
        (parts.scheme, parts.netloc, f"/{TEST_DATABASE}", parts.query, parts.fragment)
    )
    try:
        import psycopg

        with psycopg.connect(server, autocommit=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DATABASE,)
            ).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{TEST_DATABASE}"')
    except Exception as exc:  # noqa: BLE001 - fall back loudly, keep the old target
        print(f"conftest: could not create {TEST_DATABASE} ({exc}); using the configured database")
        return
    os.environ["DATABASE_URL"] = test_url


_redirect_to_test_database()


def pytest_configure(config):
    """Register the shared marker set for the whole suite."""
    config.addinivalue_line(
        "markers",
        "llm: requires the real Ollama generator on the host (run by the full CI tier)",
    )


@pytest.fixture(scope="session", autouse=True)
def _embedding_model_matches_the_pin():
    """Fail the session if Settings ever resolves a different embedder.

    The pin above is only effective if the real environment variable wins
    over ``.env``; this assertion turns a silent precedence regression
    into an immediate, obvious failure instead of divergent rankings.
    """
    from app.config import Settings

    resolved = Settings().embedding_model
    assert resolved == PINNED_EMBEDDING_MODEL, (
        f"tests/conftest.py pinned EMBEDDING_MODEL to {PINNED_EMBEDDING_MODEL}, "
        f"but Settings resolved {resolved!r}; the .env override leaked into the suite"
    )
