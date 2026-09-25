"""Process-local embedding cache at the embed_texts seam."""

import app.embeddings as embeddings
from app.embeddings import embed_texts


class _Vector:
    def __init__(self, values: list[float]):
        self._values = values

    def tolist(self) -> list[float]:
        return list(self._values)


class _RecordingTransformer:
    instances: list["_RecordingTransformer"] = []

    def __init__(self, name: str):
        self.name = name
        self.batches: list[list[str]] = []
        _RecordingTransformer.instances.append(self)

    def encode(self, texts, normalize_embeddings=True):
        assert normalize_embeddings is True
        self.batches.append(list(texts))
        return [
            _Vector([float(len(text)), float(len(self.name))]) for text in texts
        ]


def _isolate(monkeypatch):
    _RecordingTransformer.instances = []
    monkeypatch.setattr(embeddings, "_model", None)
    monkeypatch.setattr(embeddings, "_loaded_name", None)
    monkeypatch.setattr(embeddings, "_vectors", {})
    monkeypatch.setattr(
        "sentence_transformers.SentenceTransformer",
        _RecordingTransformer,
    )


def test_a_repeated_text_is_not_encoded_again(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("EMBEDDING_MODEL", "first-embedder")

    first = embed_texts(["meals", "meals"])
    second = embed_texts(["meals"])

    assert first == [[5.0, 14.0], [5.0, 14.0]]
    assert second == [[5.0, 14.0]]
    assert len(_RecordingTransformer.instances) == 1
    assert _RecordingTransformer.instances[0].batches == [["meals"]]


def test_a_mixed_call_encodes_only_the_new_text_and_keeps_order(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("EMBEDDING_MODEL", "first-embedder")

    embed_texts(["meals"])
    mixed = embed_texts(["airfare", "meals"])

    assert mixed == [[7.0, 14.0], [5.0, 14.0]]
    model = _RecordingTransformer.instances[0]
    assert model.batches == [["meals"], ["airfare"]]


def test_a_different_model_name_is_a_cache_miss(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("EMBEDDING_MODEL", "first-embedder")
    embed_texts(["meals"])

    monkeypatch.setenv("EMBEDDING_MODEL", "other-embedder")
    again = embed_texts(["meals"])

    assert again == [[5.0, 14.0]]
    assert [model.name for model in _RecordingTransformer.instances] == [
        "first-embedder",
        "other-embedder",
    ]
    assert _RecordingTransformer.instances[1].batches == [["meals"]]
