"""Every CLI branch reports cleanly: happy paths exit zero, failures exit one."""

import argparse
import runpy
import sys
from types import SimpleNamespace

import pytest

from app.cli import main
from app.corpus.ingest import EmptyCorpusError


class FakeAdapter:
    """A database adapter that fails on connect, for paths that must survive it."""

    def connect(self):
        raise OSError("database is down")


class FakeModel:
    def chat(self, prompt: str) -> str:
        return '{"tier2": []}'


def test_ingest_reports_the_chunk_count(monkeypatch, capsys):
    monkeypatch.setattr("app.cli.run", lambda adapter, corpus_dir=None: 3)
    assert main(["ingest"], database_adapter=FakeAdapter()) == 0
    assert "ingested 3 policy chunks" in capsys.readouterr().err


def test_ingest_reports_an_empty_corpus(monkeypatch, capsys):
    def boom(adapter, corpus_dir=None):
        raise EmptyCorpusError("no chunks in corpora/empty")

    monkeypatch.setattr("app.cli.run", boom)
    assert main(["ingest"], database_adapter=FakeAdapter()) == 1
    assert "no chunks in corpora/empty" in capsys.readouterr().err


def test_eval_builds_the_default_model_and_reranker(monkeypatch, tmp_path):
    wired = {}

    class RecordingModel(FakeModel):
        def __init__(self):
            wired["model"] = True

    class RecordingReranker:
        def __init__(self):
            wired["reranker"] = True

        def rank(self, question, chunks):
            return chunks

    monkeypatch.setattr("app.generation.ollama.OllamaAdapter", RecordingModel)
    monkeypatch.setattr("app.cli.CrossEncoderReranker", RecordingReranker)
    monkeypatch.setattr(
        "app.cli.run_exam",
        lambda goldens, **kwargs: {"summary": {"passed": 10, "total": 10}},
    )
    monkeypatch.setattr("app.cli.format_table", lambda record: "table")
    output = tmp_path / "record.json"
    code = main(["eval", "--output", str(output)], database_adapter=FakeAdapter())
    assert code == 0
    assert wired == {"model": True, "reranker": True}
    assert "passed" in output.read_text(encoding="utf-8")


def test_an_unrouted_command_fails_closed(monkeypatch):
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self, argv=None: SimpleNamespace(command="bogus"),
    )
    assert main([], database_adapter=FakeAdapter()) == 1


def test_the_module_entrypoint_runs_the_cli(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["app", "no-such-command"])
    with pytest.raises(SystemExit):
        runpy.run_module("app", run_name="__main__")


def test_candidate_serves_with_the_injected_deps(monkeypatch):
    served = {}
    deps = object()

    def fake_serve(host, port, *, deps=None):
        served.update(host=host, port=port, deps=deps)
        return 0

    monkeypatch.setattr("app.candidate.serve_candidate", fake_serve)
    code = main(
        ["candidate", "--serve", "--port", "9000"],
        database_adapter=FakeAdapter(),
        candidate_deps=deps,
    )
    assert code == 0
    assert served == {"host": "127.0.0.1", "port": 9000, "deps": deps}


def test_candidate_runs_one_request_file(monkeypatch, capsys):
    called = {}
    deps = object()

    def fake_run(request, response, *, deps=None):
        called.update(request=str(request), response=str(response), deps=deps)
        return {"answer": "ok"}

    monkeypatch.setattr("app.candidate.run_request_file", fake_run)
    code = main(
        ["candidate", "--request", "req.json", "--response", "out.json"],
        database_adapter=FakeAdapter(),
        candidate_deps=deps,
    )
    assert code == 0
    assert called == {"request": "req.json", "response": "out.json", "deps": deps}
    assert '"answer": "ok"' in capsys.readouterr().out


def test_candidate_needs_a_request_and_response(capsys):
    code = main(
        ["candidate"],
        database_adapter=FakeAdapter(),
        candidate_deps=object(),
    )
    assert code == 2
    assert "candidate needs" in capsys.readouterr().err


def test_candidate_builds_the_default_deps(monkeypatch):
    built = {}

    class FakeDeps:
        def __init__(self, *, adapter=None, model=None):
            built.update(adapter=adapter, model=model)

    monkeypatch.setattr("app.candidate.DefaultCandidateDeps", FakeDeps)
    monkeypatch.setattr(
        "app.candidate.serve_candidate",
        lambda host, port, *, deps=None: 0,
    )
    adapter, model = FakeAdapter(), FakeModel()
    code = main(
        ["candidate", "--serve"],
        database_adapter=adapter,
        language_model=model,
    )
    assert code == 0
    assert built == {"adapter": adapter, "model": model}
