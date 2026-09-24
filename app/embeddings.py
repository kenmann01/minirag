# Internal and Confidential - Not for External Distribution.
"""Create normalized vector embeddings with the configured sentence-transformers model."""

from contextlib import contextmanager

from huggingface_hub import logging as hf_logging
from huggingface_hub.utils import (
    are_progress_bars_disabled,
    disable_progress_bars,
    enable_progress_bars,
)
from transformers.utils import logging as transformers_logging

from app.config import get_settings

_model = None


@contextmanager
def _quiet_model_loading():
    """Suppress model-loading logs and progress bars within the context."""

    hf_level = hf_logging.get_verbosity()
    hf_progress_disabled = are_progress_bars_disabled()
    transformers_level = transformers_logging.get_verbosity()
    transformers_progress_enabled = transformers_logging.is_progress_bar_enabled()
    hf_logging.set_verbosity_error()
    disable_progress_bars()
    transformers_logging.set_verbosity_error()
    transformers_logging.disable_progress_bar()
    try:
        yield
    finally:
        hf_logging.set_verbosity(hf_level)
        if not hf_progress_disabled:
            enable_progress_bars()
        transformers_logging.set_verbosity(transformers_level)
        if transformers_progress_enabled:
            transformers_logging.enable_progress_bar()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed text as normalized vectors with a lazily loaded shared model.

    Args:
        texts: Text values to encode in input order.

    Returns:
        One floating-point embedding per input text.
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        with _quiet_model_loading():
            _model = SentenceTransformer(get_settings().embedding_model)
    vectors = _model.encode(texts, normalize_embeddings=True)
    return [vector.tolist() for vector in vectors]
