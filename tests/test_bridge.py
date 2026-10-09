"""Retrieve-only bridge and the top-k cutoff on search."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

from app.cli import main
from app.retrieval.bridge import retrieve
from app.retrieval.retrieve import search


class FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return []


class FakeAdapter:
    def __init__(self):
        self.conn = FakeConn()

    @contextmanager
    def connect(self):
        yield self.conn


def test_search_limits_each_lane_to_top_k(monkeypatch):
    monkeypatch.setattr("app.retrieval.retrieve.embed_texts", lambda texts: [[0.0, 0.1, 0.2]])
    adapter = FakeAdapter()
    assert search("provisioning", adapter, top_k=3) == []
    # Call 0 is the embedding-model guard; the lanes follow it.
    assert adapter.conn.calls[1][1][-1] == 3
    assert adapter.conn.calls[2][1][-1] == 3
    assert "LIMIT %s" in adapter.conn.calls[1][0]
    assert "LIMIT %s" in adapter.conn.calls[2][0]


def _chunks():
    return [
        {
            "chunk_id": "lending-rules:s12:c01",
            "source_doc": "lending-rules.md",
            "section": "12",
            "section_title": "Provisioning limits",
            "effective_date": "2026-04-01",
            "text": "A lender shall keep a limit.",
            "distance": 0.31,
            "rrf_score": 0.0317,
        },
        {
            "chunk_id": "lending-rules:s12:c02",
            "source_doc": "lending-rules.md",
            "section": "12",
            "section_title": "Provisioning limits",
            "effective_date": "2026-04-01",
            "text": "The same section, second window.",
            "distance": 0.33,
            "rrf_score": 0.0301,
        },
    ]


def test_retrieve_logs_every_chunk_and_does_not_drop_a_section(capsys, monkeypatch):
    seen = {}

    def fake_search(query, adapter, **kwargs):
        seen["query"] = query
        seen["top_k"] = kwargs["top_k"]
        return _chunks()

    monkeypatch.setattr("app.retrieval.bridge.search", fake_search)
    monkeypatch.setattr(
        "app.retrieval.bridge.get_settings",
        lambda: SimpleNamespace(embedding_model="Alibaba-NLP/gte-modernbert-base"),
    )
    payload = retrieve("provisioning limits", adapter=None, top_k=5)
    logged = capsys.readouterr().err
    assert "lending-rules:s12:c01" in logged
    assert "lending-rules:s12:c02" in logged
    assert [chunk["chunk_id"] for chunk in payload["chunks"]] == [
        "lending-rules:s12:c01",
        "lending-rules:s12:c02",
    ]
    assert payload["model"] == "Alibaba-NLP/gte-modernbert-base"
    assert payload["query"] == "provisioning limits"
    assert seen["top_k"] == 5


def test_retrieve_command_writes_json(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("app.retrieval.bridge.search", lambda *args, **kwargs: _chunks())
    monkeypatch.setattr(
        "app.retrieval.bridge.get_settings",
        lambda: SimpleNamespace(embedding_model="Alibaba-NLP/gte-modernbert-base"),
    )
    destination = tmp_path / "out.json"
    code = main(
        ["retrieve", "provisioning limits", "--top-k", "5", "--json", str(destination)],
        database_adapter=FakeAdapter(),
    )
    assert code == 0
    saved = json.loads(destination.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert saved["chunks"][0]["chunk_id"] == printed["chunks"][0]["chunk_id"]
    assert "rrf_score" in saved["chunks"][0]
