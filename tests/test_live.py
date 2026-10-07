"""The live page plays a fixed trace and can stream a real score."""

import json
from http.client import HTTPConnection
from threading import Thread

from app.live import STATIONS, demo_events, iter_live, serve_live
from app.score import Prices
from tests.test_score import AlwaysPass
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
        "app.score.judge_rule",
        lambda prompt, answer, allowed: {
            "pass": True,
            "citation": allowed[0],
            "prompt_tokens": 1,
            "completion_tokens": 1,
        },
    )
    seen = []

    def task_fn(prompt: str, context: str):
        from app.agent import RunOutput

        answer = "unknown" if context == "bare" else prompt
        return RunOutput(answer=answer, tool_calls=0, prompt_tokens=1, completion_tokens=1)

    from app.score import run_score

    rows = run_score(
        graph_path=graph,
        repo=tmp_path,
        output_dir=tmp_path / "runs",
        model=Phraser(),
        retrieve=retrieve_factory("bank"),
        task_fn=task_fn,
        tier2_judge=AlwaysPass(),
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

    monkeypatch.setattr("app.score.run_score", fake_run_score)
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
