from app.config import get_settings

_model = None


def embed_texts(texts: list[str]) -> list[list[float]]:
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(get_settings().embedding_model)
    vectors = _model.encode(texts, normalize_embeddings=True)
    return [vector.tolist() for vector in vectors]
