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
_loaded_name: str | None = None
_vectors: dict[tuple[str, str], list[float]] = {}


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

    A text already encoded by the configured model in this process is returned
    from memory. A different configured model loads separately and does not
    reuse those vectors.

    Args:
        texts: Text values to encode in input order.

    Returns:
        One floating-point embedding per input text.
    """
    global _model, _loaded_name
    model_name = get_settings().embedding_model
    missing = [
        text
        for text in dict.fromkeys(texts)
        if (model_name, text) not in _vectors
    ]
    if missing:
        if _model is None or _loaded_name != model_name:
            from sentence_transformers import SentenceTransformer

            with _quiet_model_loading():
                _model = SentenceTransformer(model_name)
            _loaded_name = model_name
        encoded = _model.encode(missing, normalize_embeddings=True)
        for text, vector in zip(missing, encoded, strict=True):
            _vectors[(model_name, text)] = vector.tolist()
    return [_vectors[(model_name, text)] for text in texts]
