# Internal and Confidential - Not for External Distribution.
"""Serve the ask-trace page on localhost."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.db import DatabaseAdapter
from app.generate import LanguageModel
from app.pgadapter import PgAdapter
from app.reranker import CrossEncoderReranker, Reranker
from app.trace import trace_ask

_PAGE = Path(__file__).resolve().parent / "static" / "index.html"


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    database_adapter: DatabaseAdapter | None = None,
    language_model: LanguageModel | None = None,
    reranker: Reranker | None = None,
) -> None:
    """Serve the trace page until the process is stopped."""
    adapter = database_adapter or PgAdapter()
    if language_model is None:
        from app.ollama import OllamaAdapter

        language_model = OllamaAdapter()
    ranker = reranker or CrossEncoderReranker()
    handler = _handler(adapter, language_model, ranker)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"ask trace at http://{host}:{port}", flush=True)
    server.serve_forever()


def _handler(
    adapter: DatabaseAdapter,
    language_model: LanguageModel,
    reranker: Reranker,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] != "/":
                self.send_error(404)
                return
            body = _PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/ask":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = trace_ask(
                str(payload.get("question", "")),
                database_adapter=adapter,
                language_model=language_model,
                reranker=reranker,
                include_superseded=bool(payload.get("include_superseded", False)),
            )
            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

    return Handler
