from typing import Protocol

from app.embeddings import _quiet_model_loading

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_model = None


class Reranker(Protocol):
    def rank(self, question: str, chunks: list[dict]) -> list[dict]: ...


class CrossEncoderReranker:
    def rank(self, question: str, chunks: list[dict], top_n: int = 5) -> list[dict]:
        if not chunks:
            return []
        scores = self._predict([(question, chunk["text"]) for chunk in chunks])
        order = sorted(range(len(chunks)), key=lambda index: (-scores[index], index))
        selected: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for index in order:
            chunk = chunks[index]
            key = (chunk["source_doc"], chunk["section"])
            if key in seen:
                continue
            seen.add(key)
            selected.append(chunk)
            if len(selected) == top_n:
                break
        return selected

    def _predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        global _model
        if _model is None:
            from sentence_transformers import CrossEncoder

            with _quiet_model_loading():
                _model = CrossEncoder(MODEL_NAME)
        return [float(score) for score in _model.predict(pairs)]
