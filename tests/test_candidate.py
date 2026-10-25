"""The candidate adapter: v1 shape, HTTP transport, and fixture parity."""

import ast
import json
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.candidate import (
    CandidateRequestError,
    DefaultCandidateDeps,
    make_server,
    run_candidate,
    run_request_file,
    serve_candidate,
    validate_request,
)
from app.generation.schemas import AskResponse
from app.generation.validate import REFUSAL
from tests.parity_fixture import (
    ANSWERS,
    TASK_BY_ID,
    TASKS,
    FixtureDeps,
)

ROOT = Path(__file__).resolve().parents[1]


def _request(task_id: str = "t2-01", *, context=None, prompt_override=None, seed=77) -> dict:
    task = TASK_BY_ID[task_id]
    return {
        "case_id": f"case-{task_id}",
        "prompt": task.prompt,
        "context": context or [],
        "prompt_override": prompt_override,
        "seed": seed,
    }


def _post(port: int, body, *, path: str = "/run"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(port: int, path: str):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture
def server():
    srv = make_server("127.0.0.1", 0, deps=FixtureDeps())
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


# The contract shape.


def test_run_candidate_returns_the_v1_shape():
    task = TASK_BY_ID["t2-01"]
    request = _request(
        "t2-01",
        context=[{"chunk_id": "bank-1", "text": task.origin["chunks"][0]["text"]}],
    )
    response = run_candidate(request, deps=FixtureDeps())

    assert set(response) == {"answer", "citations", "usage", "tool_calls", "artifacts"}
    assert response["answer"] == ANSWERS["answers"]["t2-01"]
    assert response["citations"] == ["bank-1"]
    assert response["usage"] == {"prompt_tokens": 140, "completion_tokens": 18}
    assert response["tool_calls"] == 0
    assert response["artifacts"] == {
        "case_id": "case-t2-01",
        "retrieval": "request",
        "chunk_ids": ["bank-1"],
        "seed": 77,
        "prompt_source": "default",
        "model": ANSWERS["model"],
    }


def test_run_candidate_retrieves_only_when_context_is_empty():
    deps = FixtureDeps()
    response = run_candidate(_request("t2-01"), deps=deps)

    assert deps.retrieved == [TASK_BY_ID["t2-01"].prompt]
    assert response["tool_calls"] == 1
    assert response["artifacts"]["retrieval"] == "retrieval"
    assert response["citations"] == ["bank-1"]


def test_run_candidate_never_retrieves_when_context_is_injected():
    class NoRetrieve(FixtureDeps):
        def retrieve(self, query: str, *, top_k: int = 20) -> dict:
            raise AssertionError("injected context must bypass retrieval")

    response = run_candidate(
        _request("t1-01", context=[{"chunk_id": "map-t1-01", "text": "flowchart LR"}]),
        deps=NoRetrieve(),
    )
    assert response["tool_calls"] == 0
    assert response["citations"] == ["map-t1-01"]


def test_run_candidate_threads_the_override_and_seed():
    class Recording:
        model_name = "fixture-model"

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def retrieve(self, query: str, *, top_k: int = 20) -> dict:
            return {"chunks": []}

        def generate(self, question, chunks, *, prompt_text=None, seed=None):
            self.calls.append({"question": question, "prompt_text": prompt_text, "seed": seed})
            return {
                "answer": "ok",
                "citations": ["c1"],
                "prompt_tokens": 3,
                "completion_tokens": 4,
            }

        def judge(self, prompt, answer, references):
            return {}

    deps = Recording()
    response = run_candidate(
        _request(
            "t1-01",
            context=[{"chunk_id": "c1", "text": "x"}],
            prompt_override="CUSTOM {question}",
            seed=7,
        ),
        deps=deps,
    )
    assert deps.calls == [
        {
            "question": TASK_BY_ID["t1-01"].prompt,
            "prompt_text": "CUSTOM {question}",
            "seed": 7,
        }
    ]
    assert response["usage"] == {"prompt_tokens": 3, "completion_tokens": 4}
    assert response["citations"] == ["c1"]
    assert response["artifacts"]["prompt_source"] == "override"


def test_run_candidate_defaults_an_unreported_response_to_zero_usage():
    class Sparse:
        model_name = None

        def retrieve(self, query: str, *, top_k: int = 20) -> dict:
            return {"chunks": []}

        def generate(self, question, chunks, *, prompt_text=None, seed=None):
            return {"answer": "ok"}

        def judge(self, prompt, answer, references):
            return {}

    response = run_candidate(_request("t1-01"), deps=Sparse())
    assert response["answer"] == "ok"
    assert response["usage"] == {"prompt_tokens": 0, "completion_tokens": 0}
    assert response["citations"] == []
    assert response["tool_calls"] == 1
    assert "model" not in response["artifacts"]


def test_run_candidate_clamps_bad_usage_counts():
    class BadUsage:
        model_name = None

        def retrieve(self, query: str, *, top_k: int = 20) -> dict:
            return {"chunks": []}

        def generate(self, question, chunks, *, prompt_text=None, seed=None):
            return {"answer": "ok", "prompt_tokens": "nope", "completion_tokens": -5}

        def judge(self, prompt, answer, references):
            return {}

    response = run_candidate(_request("t1-01"), deps=BadUsage())
    assert response["usage"] == {"prompt_tokens": 0, "completion_tokens": 0}


def test_run_candidate_ignores_a_citation_that_does_not_match_a_chunk():
    class Unmatched:
        model_name = None

        def retrieve(self, query: str, *, top_k: int = 20) -> dict:
            return {"chunks": []}

        def generate(self, question, chunks, *, prompt_text=None, seed=None):
            return {"answer": "ok", "citation": {"section": "not-a-section"}}

        def judge(self, prompt, answer, references):
            return {}

    response = run_candidate(
        _request("t1-01", context=[{"chunk_id": "c1", "text": "x"}]),
        deps=Unmatched(),
    )
    assert response["citations"] == []


def test_run_candidate_treats_an_empty_citation_section_as_no_citation():
    class EmptySection:
        model_name = None

        def retrieve(self, query: str, *, top_k: int = 20) -> dict:
            return {"chunks": []}

        def generate(self, question, chunks, *, prompt_text=None, seed=None):
            return {"answer": "ok", "citation": {"section": ""}}

        def judge(self, prompt, answer, references):
            return {}

    response = run_candidate(
        _request("t1-01", context=[{"chunk_id": "c1", "text": "x"}]),
        deps=EmptySection(),
    )
    assert response["citations"] == []


def test_run_candidate_accepts_a_bare_chunk_list_and_normalises_it():
    seen = {}

    class BareList:
        model_name = None

        def retrieve(self, query: str, *, top_k: int = 20) -> list[dict]:
            return [{"chunk_id": "reg-z-1026:1", "text": "rule text"}]

        def generate(self, question, chunks, *, prompt_text=None, seed=None):
            seen["chunks"] = chunks
            return {"answer": "ok", "citations": ["reg-z-1026:1"]}

        def judge(self, prompt, answer, references):
            return {}

    response = run_candidate(_request("t2-01"), deps=BareList())
    assert seen["chunks"] == [
        {
            "chunk_id": "reg-z-1026:1",
            "source_doc": "reg-z-1026",
            "section": "1",
            "section_title": "",
            "effective_date": None,
            "text": "rule text",
            "distance": 0.0,
        }
    ]
    assert response["citations"] == ["reg-z-1026:1"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (object(), "mapping with 'chunks'"),
        ({"chunks": "not-a-list"}, "'chunks' must be a list"),
        ({"chunks": ["not-an-object"]}, "a chunk must be a JSON object"),
        ({"chunks": [{"text": "no id"}]}, "missing its chunk_id"),
    ],
)
def test_run_candidate_rejects_bad_retrieval_payloads(payload, message):
    class BadRetrieve:
        model_name = None

        def retrieve(self, query: str, *, top_k: int = 20):
            return payload

        def generate(self, question, chunks, *, prompt_text=None, seed=None):
            raise AssertionError("generation must not run")

        def judge(self, prompt, answer, references):
            return {}

    with pytest.raises(CandidateRequestError, match=message):
        run_candidate(_request("t1-01"), deps=BadRetrieve())


def test_run_candidate_treats_missing_chunks_as_empty():
    class EmptyRetrieve:
        model_name = None

        def retrieve(self, query: str, *, top_k: int = 20) -> dict:
            return {"chunks": None}

        def generate(self, question, chunks, *, prompt_text=None, seed=None):
            return {"answer": "ok"}

        def judge(self, prompt, answer, references):
            return {}

    response = run_candidate(_request("t1-01"), deps=EmptyRetrieve())
    assert response["answer"] == "ok"
    assert response["tool_calls"] == 1


def test_run_candidate_builds_default_deps_when_none_are_given(monkeypatch):
    used = {}

    class FakeDeps(FixtureDeps):
        def __init__(self) -> None:
            super().__init__()
            used["built"] = True

    monkeypatch.setattr("app.candidate.DefaultCandidateDeps", FakeDeps)
    response = run_candidate(_request("t2-01"))
    assert used["built"] is True
    assert response["citations"] == ["bank-1"]


# Request validation.


def test_validate_request_preserves_append_compatible_fields():
    request = {
        "case_id": "c1",
        "prompt": "q",
        "context": [{"chunk_id": "x", "text": "t", "source_doc": "doc", "extra": 1}],
        "prompt_override": "p",
        "seed": 7,
    }
    normalised = validate_request(request)
    assert normalised["context"][0] == {
        "chunk_id": "x",
        "text": "t",
        "source_doc": "doc",
        "extra": 1,
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r.__setitem__("case_id", ""), "case_id"),
        (lambda r: r.__setitem__("case_id", 1), "case_id"),
        (lambda r: r.__setitem__("prompt", 1), "prompt"),
        (lambda r: r.__setitem__("context", "x"), "context must be a list"),
        (lambda r: r.__setitem__("context", ["x"]), "context\\[0\\] must be an object"),
        (
            lambda r: r.__setitem__("context", [{"chunk_id": "", "text": "t"}]),
            "chunk_id must be a non-empty string",
        ),
        (
            lambda r: r.__setitem__("context", [{"chunk_id": "x", "text": 1}]),
            "text must be a string",
        ),
        (lambda r: r.__setitem__("prompt_override", 1), "prompt_override"),
        (lambda r: r.__setitem__("seed", True), "seed must be an integer"),
        (lambda r: r.__setitem__("seed", "7"), "seed must be an integer"),
    ],
)
def test_validate_request_rejects_bad_fields(mutate, message):
    request = _request("t1-01")
    mutate(request)
    with pytest.raises(CandidateRequestError, match=message):
        validate_request(request)


def test_validate_request_rejects_a_non_object():
    with pytest.raises(CandidateRequestError, match="JSON object"):
        validate_request([])


# Transport.


def test_the_http_endpoint_speaks_v1(server):
    request = _request(
        "t2-01",
        context=[{"chunk_id": "bank-1", "text": "Rule bank-1 states the fixture obligation."}],
    )
    status, body = _post(server.server_address[1], request)
    assert status == 200
    assert body["answer"] == ANSWERS["answers"]["t2-01"]
    assert body["citations"] == ["bank-1"]
    assert body["usage"] == {"prompt_tokens": 140, "completion_tokens": 18}


def test_the_http_endpoint_reports_health(server):
    port = server.server_address[1]
    assert _get(port, "/health") == (200, {"status": "ok", "contract": "candidate-v1"})
    assert _get(port, "/healthz") == (200, {"status": "ok", "contract": "candidate-v1"})


def test_the_http_endpoint_rejects_unknown_paths(server):
    port = server.server_address[1]
    status, body = _post(port, {}, path="/nope")
    assert status == 404
    assert "unknown path" in body["error"]
    status, body = _get(port, "/nope")
    assert status == 404
    assert "unknown path" in body["error"]


def test_the_http_endpoint_rejects_malformed_bodies(server):
    port = server.server_address[1]
    status, body = _post(port, b"not json")
    assert status == 400
    assert "not JSON" in body["error"]
    status, body = _post(port, b"[]")
    assert status == 400
    assert "JSON object" in body["error"]
    status, body = _post(port, {})
    assert status == 400
    assert "case_id" in body["error"]


def test_the_http_endpoint_reports_a_candidate_failure():
    class Boom(FixtureDeps):
        def generate(self, question, chunks, *, prompt_text=None, seed=None):
            raise RuntimeError("the model is down")

    srv = make_server("127.0.0.1", 0, deps=Boom())
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _post(srv.server_address[1], _request("t1-01"))
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
    assert status == 500
    assert "candidate failed: RuntimeError" in body["error"]
    assert "the model is down" in body["error"]


def test_the_http_endpoint_rejects_a_bad_content_length(server):
    port = server.server_address[1]
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(
            b"POST /run HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Length: nope\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        pieces = []
        while True:
            piece = sock.recv(4096)
            if not piece:
                break
            pieces.append(piece)
        raw = b"".join(pieces)
    assert b"400" in raw
    assert b"Content-Length is not an integer" in raw


def test_the_http_endpoint_rejects_a_negative_content_length(server):
    port = server.server_address[1]
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(
            b"POST /run HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Length: -1\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        pieces = []
        while True:
            piece = sock.recv(4096)
            if not piece:
                break
            pieces.append(piece)
        raw = b"".join(pieces)
    assert b"400" in raw
    assert b"Content-Length must be non-negative" in raw


def test_run_request_file_round_trips(tmp_path):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_request("t1-01")), encoding="utf-8")
    response_path = tmp_path / "nested" / "response.json"

    response = run_request_file(request_path, response_path, deps=FixtureDeps())

    assert json.loads(response_path.read_text(encoding="utf-8")) == response
    assert response["answer"] == REFUSAL


def test_serve_candidate_closes_on_interrupt(monkeypatch, capsys):
    closed = {}

    class FakeServer:
        server_address = ("127.0.0.1", 8768)

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            closed["closed"] = True

    monkeypatch.setattr(
        "app.candidate.make_server",
        lambda host, port, deps=None: FakeServer(),
    )
    assert serve_candidate("127.0.0.1", 8768, deps=FixtureDeps()) == 0
    assert closed["closed"] is True
    assert "candidate-v1 listening" in capsys.readouterr().err


# The real dependency wiring, skipped when the host lacks the RAG stack.


def test_default_deps_wires_the_rag_modules(monkeypatch):
    pytest.importorskip("psycopg")
    pytest.importorskip("pgvector.psycopg")
    import app.generation.generate as generate_module
    import app.generation.judge as judge_module
    import app.generation.ollama as ollama
    import app.retrieval.bridge as bridge
    import app.storage.pgadapter as pgadapter

    calls = {}

    def fake_retrieve(query, adapter, top_k=20):
        calls["retrieve"] = (query, adapter, top_k)
        return {"chunks": []}

    def fake_generate(question, chunks, model, *, prompt_text=None, seed=None):
        calls["generate"] = (question, chunks, model, prompt_text, seed)
        return AskResponse(answer="ok", citation=None, retrieved_chunks=[])

    def fake_judge(prompt, answer, references):
        calls["judge"] = (prompt, answer, references)
        return {"passed": True, "citation": "c"}

    monkeypatch.setattr(bridge, "retrieve", fake_retrieve)
    monkeypatch.setattr(generate_module, "generate", fake_generate)
    monkeypatch.setattr(judge_module, "judge_rule", fake_judge)

    class FakeAdapter:
        pass

    class FakeModel:
        model_name = "fake-model"

    deps = DefaultCandidateDeps(adapter=FakeAdapter(), model=FakeModel(), top_k=3)
    assert deps.model_name == "fake-model"
    assert deps.retrieve("q") == {"chunks": []}
    assert calls["retrieve"][0] == "q"
    assert calls["retrieve"][2] == 3
    assert deps.generate("q", [], seed=7).answer == "ok"
    assert calls["generate"][4] == 7
    assert deps.judge("p", "a", []) == {"passed": True, "citation": "c"}
    assert calls["judge"] == ("p", "a", [])

    made = {}

    class FakePgAdapter:
        def __init__(self) -> None:
            made["adapter"] = True

    class FakeOllama:
        model_name = "real-default"

        def __init__(self) -> None:
            made["model"] = True

    monkeypatch.setattr(pgadapter, "PgAdapter", FakePgAdapter)
    monkeypatch.setattr(ollama, "OllamaAdapter", FakeOllama)
    bare = DefaultCandidateDeps()
    assert bare.model_name is None
    bare.retrieve("q2")
    bare.generate("q2", [])
    assert made == {"adapter": True, "model": True}
    assert bare.model_name == "real-default"


def test_the_runtime_module_does_not_import_the_harness():
    source = (ROOT / "app" / "candidate.py").read_text(encoding="utf-8")
    modules = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    assert not any(name == "app.harness" or name.startswith("app.harness.") for name in modules)


def test_importing_the_candidate_keeps_the_heavy_modules_lazy():
    code = (
        "import sys; import app.candidate; "
        "print(int('psycopg' in sys.modules), int('sentence_transformers' in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0 0"


# Fixture parity.


def _map_chunk(task) -> dict:
    """Stand in for the map excerpt; only its presence drives the recorded answer."""
    edge = task.origin.get("edge") or task.expected
    return {
        "chunk_id": f"map-{task.id}",
        "text": (
            f"source: {edge.get('source')}\n"
            f"relation: {edge.get('relation')}\n"
            f"file: {edge.get('source_file') or edge.get('target_file') or ''}"
        ),
    }


def _chunks_for(task, context: str) -> list[dict]:
    chunks: list[dict] = []
    if context in {"map", "map_rules"}:
        chunks.append(_map_chunk(task))
    if context == "map_rules":
        chunks.extend(
            {"chunk_id": chunk["chunk_id"], "text": chunk["text"]}
            for chunk in task.origin.get("chunks") or []
        )
    return chunks


def test_the_fixture_requests_are_v1_conformant():
    for task in TASKS:
        for context in ("bare", "map", "map_rules"):
            validate_request(
                {
                    "case_id": f"{context}/{task.id}",
                    "prompt": task.prompt,
                    "context": _chunks_for(task, context),
                    "prompt_override": None,
                    "seed": ANSWERS["seed"],
                }
            )


def test_the_fixture_contract_responses_match_the_committed_answers():
    deps = FixtureDeps()
    for task in TASKS:
        for context in ("bare", "map", "map_rules"):
            request = {
                "case_id": f"{context}/{task.id}",
                "prompt": task.prompt,
                "context": _chunks_for(task, context),
                "prompt_override": None,
                "seed": ANSWERS["seed"],
            }
            response = run_candidate(request, deps=deps)
            calls_model = not (context == "bare" and task.tier == 1)
            if context == "bare":
                answering = task.tier == 2
            elif context == "map":
                answering = task.tier == 1
            else:
                answering = True
            expected = ANSWERS["answers"][task.id] if answering else REFUSAL
            assert response["answer"] == expected, (task.id, context)
            assert response["tool_calls"] == (1 if context == "bare" else 0)
            if calls_model:
                assert response["usage"] == ANSWERS["usage"][task.id]
            else:
                assert response["usage"] == {"prompt_tokens": 0, "completion_tokens": 0}
            if context == "map_rules" and task.tier == 2:
                assert response["citations"] == [task.expected["chunk_ids"][0]]
    assert all(seed == ANSWERS["seed"] for seed in deps.model.seeds)

