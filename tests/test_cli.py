"""Every CLI branch reports cleanly: happy paths exit zero, failures exit one."""

import argparse
import runpy
import sys
from types import SimpleNamespace

import pytest

from app.cli import main
from app.corpus.ingest import EmptyCorpusError
from app.harness.tasks import TaskGenerationError


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


def test_compare_passes_the_report_directory(monkeypatch):
    served = {}

    def fake_serve(report_dir, host, port):
        served.update(report_dir=str(report_dir), host=host, port=port)

    monkeypatch.setattr("app.web.compare.serve_compare", fake_serve)
    assert main(["compare", "reports", "--host", "0.0.0.0", "--port", "9000"]) == 0
    assert served == {"report_dir": "reports", "host": "0.0.0.0", "port": 9000}


def test_gate_keeps_its_record_when_metrics_storage_fails(tmp_path, monkeypatch, capsys):
    loans = tmp_path / "fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount"
    loans.mkdir(parents=True)
    (loans / "Loan.java").write_text(
        "public class Loan { public void approve() { disburse(); } public void disburse() {} }",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.harness.gate.render_largest",
        lambda diagrams, out_dir: ({"name": "loanaccount", "nodes": 2, "rendered": False}, []),
    )
    output = tmp_path / "gate.json"
    code = main(
        ["gate", "--repo", str(tmp_path), "--output", str(output)],
        database_adapter=FakeAdapter(),
    )
    assert code == 0
    assert "gate metrics not recorded: database is down" in capsys.readouterr().err
    assert output.is_file()


def test_ossie_materialize_reports_an_unreadable_graph(tmp_path, capsys):
    from app.storage.pgadapter import PgAdapter

    code = main(
        ["ossie", "materialize", "--graph", str(tmp_path / "missing.json")],
        database_adapter=PgAdapter(),
    )
    assert code == 1
    assert capsys.readouterr().err.strip() != ""


def test_live_serves_the_trace_page(monkeypatch):
    served = {}

    def fake_serve(host, port, *, database_adapter=None, language_model=None):
        served.update(host=host, port=port, adapter=database_adapter, model=language_model)

    monkeypatch.setattr("app.web.live.serve_live", fake_serve)
    adapter, model = FakeAdapter(), FakeModel()
    assert main(["live", "--port", "9001"], database_adapter=adapter, language_model=model) == 0
    assert served == {"host": "127.0.0.1", "port": 9001, "adapter": adapter, "model": model}


def test_score_reports_a_task_generation_failure(monkeypatch, capsys):
    def boom(**kwargs):
        raise TaskGenerationError("the task list is not grounded")

    monkeypatch.setattr("app.harness.score.run_score", boom)
    monkeypatch.setattr("app.generation.ollama.OllamaAdapter", FakeModel)
    code = main(
        [
            "score",
            "--repo",
            "repo",
            "--graph",
            "graph.json",
            "--output",
            "runs",
            "--no-metrics",
        ],
        database_adapter=FakeAdapter(),
    )
    assert code == 1
    assert "not grounded" in capsys.readouterr().err


def test_score_builds_the_default_model_and_prints_the_rows(monkeypatch, capsys):
    rows = [
        {
            "run_id": "RUN 1",
            "context": "bare",
            "tasks_passed": 3,
            "tasks_total": 8,
            "tool_calls": 6,
            "cost_usd": 0.0123,
        }
    ]
    built = {}

    class RecordingModel(FakeModel):
        def __init__(self):
            built["model"] = True

    monkeypatch.setattr("app.generation.ollama.OllamaAdapter", RecordingModel)
    monkeypatch.setattr("app.harness.score.run_score", lambda **kwargs: rows)
    code = main(
        ["score", "--repo", "repo", "--graph", "graph.json", "--output", "runs", "--no-metrics"],
        database_adapter=FakeAdapter(),
    )
    assert code == 0
    assert built.get("model") is True
    out = capsys.readouterr().out
    assert "RUN 1  bare  3 of 8  tools=6  cost=$0.0123" in out


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
