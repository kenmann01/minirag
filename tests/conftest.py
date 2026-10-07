# Internal and Confidential - Not for External Distribution.
"""Pin the whole suite to the CI environment before any test imports app.

pydantic-settings resolves values with this precedence: init arguments,
then real environment variables, then the ``.env`` file, then field
defaults. A module-level assignment here therefore beats the local
``.env`` line ``EMBEDDING_MODEL=all-mpnet-base-v2`` and restores the code
default (``app/config.py``) that CI runs with. The pin sits at import
time of this conftest, which pytest loads before collecting or importing
any test module, so no ``get_settings()`` call can observe the ``.env``
override. ``DATABASE_URL`` is left untouched on purpose: the suite uses
whatever the environment or ``.env`` provides.

Issue #38.
"""

import os

import pytest

PINNED_EMBEDDING_MODEL = "Alibaba-NLP/gte-modernbert-base"

os.environ["EMBEDDING_MODEL"] = PINNED_EMBEDDING_MODEL


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
