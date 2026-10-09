"""The live page plays a fixed trace and can stream a real score."""

import json
import pathlib
from http.client import HTTPConnection
from threading import Thread

from app.harness.score import Prices, TaskGenerationError
from app.web.live import STATIONS, _jailed, demo_events, iter_live, serve_live
from tests.test_tasks import Phraser, retrieve_factory, write_graph


def test_the_demo_walks_map_then_rag_then_three_runs():
    events = demo_events()
    assert [event["station"] for event in events[:3]] == ["graph", "retrieve", "tasks"]
    assert "bare" in [event["station"] for event in events]
    assert "map" in [event["station"] for event in events]
    assert "rules" in [event["station"] for event in events]
    assert events[-2]["call"] == "scoreboard"
    assert set(STATIONS) >= {event["station"] for event in events}


def test_a_score_emits_each_call_before_it_returns(tmp_path, monkeypatch):
    graph = tmp_path / "graph.json"
    write_graph(graph)
    monkeypatch.setattr(
        "app.harness.score.judge_rule",
        lambda prompt, answer, references: {
            "passed": True,
            "citation": references[0]["chunk_id"],
            "prompt_tokens": 1,
            "completion_tokens": 1,
        },
    )
    seen = []

    def task_fn(prompt: str, context: str):
        from app.harness.agent import RunOutput

        answer = "unknown" if context == "bare" else prompt
        return RunOutput(answer=answer, tool_calls=0, prompt_tokens=1, completion_tokens=1)

    from app.harness.score import run_score

    rows = run_score(
        graph_path=graph,
        repo=tmp_path,
        output_dir=tmp_path / "runs",
        model=Phraser(),
        retrieve=retrieve_factory("bank"),
        task_fn=task_fn,
        prices=Prices(0.15, 0.60, 0.001),
        on_event=seen.append,
    )
    calls = [event["call"] for event in seen]
    assert calls[0] == "load_graph"
    assert "retrieve" in calls
    assert "phrase" in calls
    assert calls.count("agent") == 24
    assert "grade" in calls
    assert calls[-1] == "scoreboard"
    assert rows[0]["context"] == "bare"


def test_iter_live_yields_calls_until_the_worker_finishes(tmp_path, monkeypatch):
    def fake_run_score(**kwargs):
        kwargs["on_event"](
            {
                "call": "load_graph",
                "station": "graph",
                "title": "Read the code map",
                "detail": "edge",
            }
        )
        return []

    monkeypatch.setattr("app.harness.score.run_score", fake_run_score)
    events = list(
        iter_live(
            graph_path=tmp_path / "graph.json",
            repo=tmp_path,
            output_dir=tmp_path / "runs",
            model=Phraser(),
            retrieve=retrieve_factory("bank"),
        )
    )
    assert [event["call"] for event in events] == ["load_graph", "done"]


def test_the_page_serves_the_demo_trace(tmp_path):
    thread = Thread(
        target=serve_live,
        kwargs={"host": "127.0.0.1", "port": 8768, "output_dir": tmp_path},
        daemon=True,
    )
    thread.start()
    page = _get(8768, "/")
    demo = _get(8768, "/demo")
    assert b"Click a step" in page
    payload = json.loads(demo)
    assert payload[0]["call"] == "load_graph"
    missing = _post(8768, "/run", {"graph": str(tmp_path / "missing.json"), "repo": str(tmp_path)})
    assert b"Point at a graph" in missing


def _get(port: int, path: str) -> bytes:
    for _ in range(20):
        try:
            connection = HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("GET", path)
            return connection.getresponse().read()
        except OSError:
            continue
    raise AssertionError("live server did not answer")


def _post(port: int, path: str, payload: dict) -> bytes:
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    body = json.dumps(payload).encode()
    connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    return connection.getresponse().read()


def _status(method: str, path: str, port: int = 8769) -> int:
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    connection.request(method, path)
    response = connection.getresponse()
    response.read()
    return response.status


def test_iter_live_reports_a_task_generation_failure(tmp_path, monkeypatch):
    def boom(**kwargs):
        raise TaskGenerationError("the task list is not grounded")

    monkeypatch.setattr("app.harness.score.run_score", boom)
    events = list(
        iter_live(
            graph_path=tmp_path / "graph.json",
            repo=tmp_path,
            output_dir=tmp_path / "runs",
            model=Phraser(),
            retrieve=retrieve_factory("bank"),
        )
    )
    assert events[-1]["call"] == "error"
    assert events[-1]["title"] == "The task list stopped"
    assert "not grounded" in events[-1]["detail"]


def test_iter_live_reports_an_unexpected_crash(tmp_path, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("worker died")

    monkeypatch.setattr("app.harness.score.run_score", boom)
    events = list(
        iter_live(
            graph_path=tmp_path / "graph.json",
            repo=tmp_path,
            output_dir=tmp_path / "runs",
            model=Phraser(),
            retrieve=retrieve_factory("bank"),
        )
    )
    assert events[-1]["call"] == "error"
    assert events[-1]["title"] == "The run stopped"
    assert "worker died" in events[-1]["detail"]


def test_jailed_rejects_paths_outside_the_working_directory(tmp_path):
    assert _jailed(tmp_path) is None


def test_jailed_returns_none_when_resolution_fails(monkeypatch):
    real_resolve = pathlib.Path.resolve

    def selective(self, strict=False):
        if str(self).endswith("poison"):
            raise OSError("unresolvable path")
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(pathlib.Path, "resolve", selective)
    assert _jailed(pathlib.Path("poison")) is None


def test_unknown_paths_get_a_404(tmp_path):
    Thread(
        target=serve_live,
        kwargs={"host": "127.0.0.1", "port": 8769, "output_dir": tmp_path},
        daemon=True,
    ).start()
    _get(8769, "/")
    assert _status("GET", "/nope") == 404
    assert _status("POST", "/not-run") == 404


def test_a_run_builds_the_default_model_and_streams_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    graph = tmp_path / "graph.json"
    write_graph(graph)
    (tmp_path / "repo").mkdir()

    def fake_run_score(**kwargs):
        kwargs["on_event"]({"call": "load_graph", "station": "graph", "title": "Read the code map"})
        kwargs["retrieve"]("Loan1")
        return []

    monkeypatch.setattr("app.harness.score.run_score", fake_run_score)
    monkeypatch.setattr("app.generation.ollama.OllamaAdapter", Phraser)
    monkeypatch.setattr("app.retrieval.bridge.retrieve", lambda query, adapter: {"chunks": []})
    # The handler imports the retrieval bridge on its own thread; warming it
    # here keeps the first POST fast when no earlier test imported the stack.
    import app.retrieval.bridge  # noqa: F401

    Thread(
        target=serve_live,
        kwargs={
            "host": "127.0.0.1",
            "port": 8771,
            "database_adapter": object(),
            "language_model": None,
            "output_dir": tmp_path / "runs",
        },
        daemon=True,
    ).start()
    _get(8771, "/")
    body = _post(8771, "/run", {"graph": str(graph), "repo": str(tmp_path / "repo")})
    assert b"data:" in body
    assert b"load_graph" in body
    assert b"done" in body
