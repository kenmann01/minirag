# Internal and Confidential - Not for External Distribution.
"""Expose the RAG pipeline as an eval-harness candidate (candidate-v1).

The eval harness owns specs, scoring, and pricing. This module is only the
seam: one pure run_candidate maps a v1 request onto the retrieval and
generation code that stays in this repository. Importing the module is
cheap because the database, embedding, and model modules load on first use.

The harness contract is vendored at contract/candidate-v1.schema.json.
docs/CANDIDATE.md explains the request/response mapping and the parity
fixture.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

from app.generation.generate import section_label

CONTRACT_VERSION = "candidate-v1"
DEFAULT_TOP_K = 20


class CandidateRequestError(ValueError):
    """A request does not match the candidate-v1 request contract."""


class CandidateDeps(Protocol):
    """The seams the candidate borrows from the RAG repository.

    retrieve and generate are the runtime path. judge is exposed so a caller
    can assemble the whole eval pipeline from one dependency object; the
    candidate never scores itself.
    """

    model_name: str | None

    def retrieve(self, query: str, *, top_k: int = DEFAULT_TOP_K) -> dict | list[dict]:
        """Return retrieved chunks for one question."""
        ...

    def generate(
        self,
        question: str,
        chunks: list[dict],
        *,
        prompt_text: str | None = None,
        seed: int | None = None,
    ) -> Any:
        """Generate one grounded answer from the supplied chunks."""
        ...

    def judge(self, prompt: str, answer: str, references: list[dict]) -> dict:
        """Grade one answer against its reference chunks."""
        ...


class DefaultCandidateDeps:
    """Wire the candidate to this repository's retrieval, generation, and judge."""

    def __init__(
        self, *, adapter: Any = None, model: Any = None, top_k: int = DEFAULT_TOP_K
    ) -> None:
        self._adapter = adapter
        self._model = model
        self._top_k = top_k

    @property
    def model_name(self) -> str | None:
        """The answering model's name, when the adapter exposes one."""
        if self._model is None:
            return None
        return getattr(self._model, "model_name", None) or getattr(self._model, "model", None)

    def _database(self) -> Any:
        if self._adapter is None:
            from app.storage.pgadapter import PgAdapter

            self._adapter = PgAdapter()
        return self._adapter

    def _language_model(self) -> Any:
        if self._model is None:
            from app.generation.ollama import OllamaAdapter

            self._model = OllamaAdapter()
        return self._model

    def retrieve(self, query: str, *, top_k: int | None = None) -> dict:
        """Call the hybrid retriever without copying any of its logic."""
        from app.retrieval.bridge import retrieve

        return retrieve(query, self._database(), top_k=top_k or self._top_k)

    def generate(
        self,
        question: str,
        chunks: list[dict],
        *,
        prompt_text: str | None = None,
        seed: int | None = None,
    ) -> Any:
        """Call the repository's generator with the candidate's knobs."""
        from app.generation.generate import generate

        return generate(
            question,
            chunks,
            self._language_model(),
            prompt_text=prompt_text,
            seed=seed,
        )

    def judge(self, prompt: str, answer: str, references: list[dict]) -> dict:
        """Call the repository's tier-2 citation judge."""
        from app.generation.judge import judge_rule

        return judge_rule(prompt, answer, references)


def validate_request(request: Any) -> dict:
    """Validate one candidate-v1 request and return its normalised fields.

    Raises:
        CandidateRequestError: The body is not a v1 request.
    """
    if not isinstance(request, Mapping):
        raise CandidateRequestError("request must be a JSON object")
    case_id = request.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise CandidateRequestError("request.case_id must be a non-empty string")
    prompt = request.get("prompt")
    if not isinstance(prompt, str):
        raise CandidateRequestError("request.prompt must be a string")
    context = request.get("context")
    if not isinstance(context, list):
        raise CandidateRequestError("request.context must be a list of chunks")
    chunks = [_validate_context_chunk(index, chunk) for index, chunk in enumerate(context)]
    prompt_override = request.get("prompt_override")
    if prompt_override is not None and not isinstance(prompt_override, str):
        raise CandidateRequestError("request.prompt_override must be a string or null")
    seed = request.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise CandidateRequestError("request.seed must be an integer")
    return {
        "case_id": case_id,
        "prompt": prompt,
        "context": chunks,
        "prompt_override": prompt_override,
        "seed": seed,
    }


def _validate_context_chunk(index: int, chunk: Any) -> dict:
    """Validate one injected chunk while preserving append-compatible fields."""
    if not isinstance(chunk, Mapping):
        raise CandidateRequestError(f"request.context[{index}] must be an object")
    chunk_id = chunk.get("chunk_id")
    if not isinstance(chunk_id, str) or not chunk_id:
        raise CandidateRequestError(f"request.context[{index}].chunk_id must be a non-empty string")
    text = chunk.get("text")
    if not isinstance(text, str):
        raise CandidateRequestError(f"request.context[{index}].text must be a string")
    return {**chunk, "chunk_id": chunk_id, "text": text}


def _normalise_chunk(chunk: Any) -> dict:
    """Coerce an injected or retrieved chunk into the generator's record shape."""
    if not isinstance(chunk, Mapping):
        raise CandidateRequestError("a chunk must be a JSON object")
    chunk_id = chunk.get("chunk_id")
    if not isinstance(chunk_id, str) or not chunk_id:
        raise CandidateRequestError("a chunk is missing its chunk_id")
    prefix, _, suffix = chunk_id.partition(":")
    text = chunk.get("text")
    return {
        "chunk_id": chunk_id,
        "source_doc": str(chunk.get("source_doc") or prefix or chunk_id),
        "section": str(chunk.get("section") or suffix or chunk_id),
        "section_title": str(chunk.get("section_title") or ""),
        "effective_date": chunk.get("effective_date"),
        "text": text if isinstance(text, str) else "",
        "distance": float(chunk.get("distance") or 0.0),
    }


def _retrieved_chunks(payload: Any) -> list[dict]:
    """Extract the chunk list from a bridge payload or a bare list."""
    if isinstance(payload, Mapping):
        raw = payload.get("chunks")
    elif isinstance(payload, list):
        raw = payload
    else:
        raise CandidateRequestError(
            "retrieve must return a mapping with 'chunks' or a list of chunks"
        )
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise CandidateRequestError("retrieve 'chunks' must be a list")
    return [_normalise_chunk(chunk) for chunk in raw]


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read one field from a pydantic model or a plain mapping."""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _count(value: Any) -> int:
    """Coerce a provider token count to a non-negative integer."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _citations(response: Any, chunks: list[dict]) -> list[str]:
    """Map the generator's section citation back onto chunk ids."""
    explicit = _field(response, "citations")
    if isinstance(explicit, (list, tuple)) and explicit:
        return [str(item) for item in explicit]
    citation = _field(response, "citation")
    if citation is None:
        return []
    if isinstance(citation, Mapping):
        section = citation.get("section")
    else:
        section = getattr(citation, "section", None)
    if not isinstance(section, str) or not section:
        return []
    for chunk in chunks:
        if section_label(chunk) == section:
            return [str(chunk["chunk_id"])]
    return []


def run_candidate(request: dict, *, deps: CandidateDeps | None = None) -> dict:
    """Run one candidate-v1 request through the repository's RAG pipeline.

    Args:
        request: A v1 request with case_id, prompt, context, prompt_override,
            and seed.
        deps: Optional dependency bundle. Defaults to the real retriever,
            generator, and judge. Injecting fakes keeps this testable without
            Postgres or Ollama.

    Returns:
        A v1 response body with answer, citations, usage, tool_calls, and
        artifacts.

    Raises:
        CandidateRequestError: The request is not v1-conformant.
    """
    payload = validate_request(request)
    deps = deps or DefaultCandidateDeps()
    case_id = payload["case_id"]
    question = payload["prompt"]
    prompt_override = payload["prompt_override"]
    seed = payload["seed"]
    if payload["context"]:
        chunks = [_normalise_chunk(chunk) for chunk in payload["context"]]
        retrieval_source = "request"
        tool_calls = 0
    else:
        chunks = _retrieved_chunks(deps.retrieve(question))
        retrieval_source = "retrieval"
        # The retriever is the candidate's only tool; injected context means
        # the harness supplied the evidence and no retrieval call happened.
        tool_calls = 1
    response = deps.generate(
        question,
        chunks,
        prompt_text=prompt_override,
        seed=seed,
    )
    artifacts: dict[str, Any] = {
        "case_id": case_id,
        "retrieval": retrieval_source,
        "chunk_ids": [str(chunk["chunk_id"]) for chunk in chunks],
        "seed": seed,
        "prompt_source": "override" if prompt_override is not None else "default",
    }
    model_name = getattr(deps, "model_name", None)
    if isinstance(model_name, str) and model_name:
        artifacts["model"] = model_name
    return {
        "answer": str(_field(response, "answer", "")),
        "citations": _citations(response, chunks),
        "usage": {
            "prompt_tokens": _count(_field(response, "prompt_tokens", 0)),
            "completion_tokens": _count(_field(response, "completion_tokens", 0)),
        },
        "tool_calls": tool_calls,
        "artifacts": artifacts,
    }


def run_request_file(
    request_path: Path,
    response_path: Path,
    *,
    deps: CandidateDeps | None = None,
) -> dict:
    """Run one request from disk and write its response to disk."""
    payload = json.loads(Path(request_path).read_text(encoding="utf-8"))
    response = run_candidate(payload, deps=deps)
    target = Path(response_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(response, indent=2) + "\n", encoding="utf-8")
    return response


class _CandidateHandler(BaseHTTPRequestHandler):
    """A small stdlib server for the candidate-v1 contract."""

    candidate_deps: CandidateDeps | None = None

    def do_POST(self) -> None:
        """Run one v1 request and answer with a v1 response or a JSON error."""
        if self.path != "/run":
            self._send(404, {"error": f"unknown path {self.path}"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send(400, {"error": "Content-Length is not an integer"})
            return
        if length < 0:
            self._send(400, {"error": "Content-Length must be non-negative"})
            return
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            self._send(400, {"error": "request body is not JSON"})
            return
        if not isinstance(payload, dict):
            self._send(400, {"error": "request body must be a JSON object"})
            return
        try:
            response = run_candidate(payload, deps=self.candidate_deps)
        except CandidateRequestError as exc:
            self._send(400, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 - a failed cell is a server error
            self._send(500, {"error": f"candidate failed: {type(exc).__name__}: {exc}"})
            return
        self._send(200, response)

    def do_GET(self) -> None:
        """Answer a liveness probe; every other GET is a 404."""
        if self.path in {"/health", "/healthz"}:
            self._send(200, {"status": "ok", "contract": CONTRACT_VERSION})
            return
        self._send(404, {"error": f"unknown path {self.path}"})

    def _send(self, status: int, body: Any) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the stdlib access log off the score stream."""
        return None


def make_server(
    host: str = "127.0.0.1",
    port: int = 8768,
    *,
    deps: CandidateDeps | None = None,
) -> ThreadingHTTPServer:
    """Build the HTTP server without starting it; tests pass port 0."""

    class Handler(_CandidateHandler):
        candidate_deps = deps

    return ThreadingHTTPServer((host, port), Handler)


def serve_candidate(
    host: str = "127.0.0.1",
    port: int = 8768,
    *,
    deps: CandidateDeps | None = None,
) -> int:
    """Serve the v1 contract until interrupted."""
    server = make_server(host, port, deps=deps)
    print(
        f"candidate {CONTRACT_VERSION} listening on "
        f"http://{host}:{server.server_address[1]}/run",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


__all__ = [
    "CONTRACT_VERSION",
    "CandidateDeps",
    "CandidateRequestError",
    "DefaultCandidateDeps",
    "make_server",
    "run_candidate",
    "run_request_file",
    "serve_candidate",
    "validate_request",
]
