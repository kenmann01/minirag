# Internal and Confidential - Not for External Distribution.
"""Serve a page that animates each score call as it happens."""

import json
import queue
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.db import DatabaseAdapter
from app.generate import LanguageModel
from app.pgadapter import PgAdapter

_PAGE = Path(__file__).resolve().parent / "static" / "live.html"

STATIONS = ("graph", "retrieve", "tasks", "bare", "map", "rules", "board")


def demo_events() -> list[dict]:
    """One code edge and one rule, played in the order the harness calls them."""
    edge = "LoanAccount calls calculateInterest in LoanAccount.java"
    code_q = "Which method does LoanAccount call, and what is the file name?"
    rule_q = (
        "If LoanAccount charges a mortgage broker fee, is that fee a finance charge "
        "when the creditor does not require a broker?"
    )
    rule = (
        "bank-7: Fees charged by a mortgage broker are finance charges even if the "
        "creditor does not require the consumer to use a mortgage broker."
    )
    return [
        {
            "call": "load_graph",
            "station": "graph",
            "title": "Read the code map",
            "detail": edge,
            "ran": "graph.json, extracted calls, loans first",
            "returned": "LoanAccount calls calculateInterest · loanaccount/LoanAccount.java",
            "passed": None,
        },
        {
            "call": "retrieve",
            "station": "retrieve",
            "title": "Retrieve the rule",
            "detail": "Query LoanAccount. The corpus returned bank-7.",
            "ran": "retrieve LoanAccount",
            "returned": rule,
            "chunk_ids": ["bank-7"],
            "passed": None,
        },
        {
            "call": "phrase",
            "station": "tasks",
            "title": "Phrase two questions from that evidence",
            "detail": f"Code: {code_q} Rule: {rule_q}",
            "ran": "Phrase a code question and a rule question. Do not invent the facts.",
            "returned": f"t1-01 {code_q}\nt2-01 {rule_q}",
            "passed": None,
        },
        {
            "call": "agent",
            "station": "bare",
            "title": "Ask with the question only",
            "detail": code_q,
            "ran": code_q,
            "returned": "It calls disburse in Loan.java.",
            "task": "t1-01",
            "has_map": False,
            "has_rules": False,
            "passed": None,
        },
        {
            "call": "tool",
            "station": "bare",
            "title": "list_dir",
            "detail": "loanaccount/",
            "ran": "list_dir loanaccount/",
            "returned": "LoanAccount.java",
            "passed": None,
        },
        {
            "call": "tool",
            "station": "bare",
            "title": "read_file",
            "detail": "loanaccount/LoanAccount.java",
            "ran": "read_file loanaccount/LoanAccount.java",
            "returned": "void calculateInterest() { ... }",
            "passed": None,
        },
        {
            "call": "grade",
            "station": "bare",
            "title": "Grade the code fact",
            "detail": "Answer never names calculateInterest.",
            "ran": "It calls disburse in Loan.java.",
            "returned": "fail",
            "task": "t1-01",
            "passed": False,
        },
        {
            "call": "agent",
            "station": "bare",
            "title": "Ask the rule question with nothing attached",
            "detail": rule_q,
            "ran": rule_q,
            "returned": "Yes, those fees are finance charges.",
            "task": "t2-01",
            "has_map": False,
            "has_rules": False,
            "passed": None,
        },
        {
            "call": "grade",
            "station": "bare",
            "title": "Grade the cited rule",
            "detail": "No chunk was in the prompt, so bank-7 cannot be cited.",
            "ran": "Yes, those fees are finance charges.",
            "returned": "fail",
            "task": "t2-01",
            "passed": False,
        },
        {
            "call": "agent",
            "station": "map",
            "title": "Ask again with the map attached",
            "detail": f"{code_q} Map: {edge}",
            "ran": f"{code_q}\n\n{edge}",
            "returned": "It calls calculateInterest in LoanAccount.java.",
            "task": "t1-01",
            "has_map": True,
            "has_rules": False,
            "passed": None,
        },
        {
            "call": "grade",
            "station": "map",
            "title": "Grade the code fact",
            "detail": "calculateInterest and LoanAccount.java are both in the answer.",
            "ran": "It calls calculateInterest in LoanAccount.java.",
            "returned": "pass",
            "task": "t1-01",
            "passed": True,
        },
        {
            "call": "agent",
            "station": "map",
            "title": "The rule question still has no regulation text",
            "detail": rule_q,
            "ran": f"{rule_q}\n\n{edge}",
            "returned": "The map does not say whether the fee is a finance charge.",
            "task": "t2-01",
            "has_map": True,
            "has_rules": False,
            "passed": None,
        },
        {
            "call": "grade",
            "station": "map",
            "title": "Grade the cited rule",
            "detail": "The diagram does not support bank-7.",
            "ran": "The map does not say whether the fee is a finance charge.",
            "returned": "fail",
            "task": "t2-01",
            "passed": False,
        },
        {
            "call": "retrieve",
            "station": "retrieve",
            "title": "Log the rule again",
            "detail": rule,
            "ran": rule_q,
            "returned": rule,
            "chunk_ids": ["bank-7"],
            "passed": None,
        },
        {
            "call": "agent",
            "station": "rules",
            "title": "Ask with the map and the chunk",
            "detail": f"{rule_q} {rule}",
            "ran": f"{rule_q}\n\n{edge}\n\n{rule}",
            "returned": "Yes. bank-7 says the fee is a finance charge even when the creditor does not require a broker.",
            "task": "t2-01",
            "has_map": True,
            "has_rules": True,
            "passed": None,
        },
        {
            "call": "grade",
            "station": "rules",
            "title": "Grade the cited rule",
            "detail": "The judge cites bank-7.",
            "ran": "Yes. bank-7 says the fee is a finance charge even when the creditor does not require a broker.",
            "returned": "pass · bank-7",
            "task": "t2-01",
            "passed": True,
        },
        {
            "call": "scoreboard",
            "station": "board",
            "title": "Scoreboard",
            "detail": "Bare fails both. The map passes the code question. The rules pass the regulation question.",
            "ran": "Same two questions, three contexts",
            "returned": "RUN 1 bare 0/2\nRUN 2 map 1/2\nRUN 3 rules 2/2",
            "rows": [
                {"run_id": "RUN 1", "context": "bare", "tasks_passed": 0, "tasks_total": 2},
                {"run_id": "RUN 2", "context": "map", "tasks_passed": 1, "tasks_total": 2},
                {"run_id": "RUN 3", "context": "rules", "tasks_passed": 2, "tasks_total": 2},
            ],
            "passed": None,
        },
        {
            "call": "done",
            "station": "board",
            "title": "Trace finished",
            "detail": "Replay or run a live score.",
            "passed": None,
        },
    ]


def iter_live(
    *,
    graph_path: Path,
    repo: Path,
    output_dir: Path,
    model: LanguageModel,
    retrieve: Callable[[str], dict],
) -> Iterator[dict]:
    """Run one score in a worker thread and yield each call as it is emitted."""
    from app.score import TaskGenerationError, run_score

    events: queue.Queue[dict | None] = queue.Queue()

    def emit(event: dict) -> None:
        events.put(event)

    def work() -> None:
        try:
            run_score(
                graph_path=graph_path,
                repo=repo,
                output_dir=output_dir,
                model=model,
                retrieve=retrieve,
                on_event=emit,
            )
            events.put(
                {
                    "call": "done",
                    "station": "board",
                    "title": "Trace finished",
                    "detail": str(output_dir),
                }
            )
        except TaskGenerationError as exc:
            events.put(
                {
                    "call": "error",
                    "station": "board",
                    "title": "The task list stopped",
                    "detail": str(exc),
                }
            )
        except Exception as exc:
            events.put(
                {
                    "call": "error",
                    "station": "board",
                    "title": "The run stopped",
                    "detail": str(exc),
                }
            )
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True).start()
    while True:
        event = events.get()
        if event is None:
            break
        yield event


def serve_live(
    host: str = "127.0.0.1",
    port: int = 8767,
    *,
    database_adapter: DatabaseAdapter | None = None,
    language_model: LanguageModel | None = None,
    output_dir: Path | None = None,
) -> None:
    """Serve the live trace page until the process is stopped."""
    adapter = database_adapter or PgAdapter()
    handler = _handler(adapter, language_model, output_dir or Path("eval/live-runs"))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"live trace at http://{host}:{port}", flush=True)
    server.serve_forever()


def _handler(
    adapter: DatabaseAdapter,
    language_model: LanguageModel | None,
    output_dir: Path,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                body = _PAGE.read_bytes()
                self._bytes(200, "text/html; charset=utf-8", body)
                return
            if path == "/demo":
                body = json.dumps(demo_events()).encode()
                self._bytes(200, "application/json", body)
                return
            self.send_error(404)

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/run":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            graph = Path(str(payload.get("graph") or ""))
            repo = Path(str(payload.get("repo") or ""))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not graph.is_file() or not repo.is_dir():
                self._event(
                    {
                        "call": "error",
                        "station": "board",
                        "title": "Point at a graph and a repository",
                        "detail": "graph.json must exist and the repo path must be a directory.",
                    }
                )
                return
            model = language_model
            if model is None:
                from app.ollama import OllamaAdapter

                model = OllamaAdapter()
            from app.bridge import retrieve

            def bridge(query: str) -> dict:
                return retrieve(query, adapter)

            for event in iter_live(
                graph_path=graph,
                repo=repo,
                output_dir=output_dir,
                model=model,
                retrieve=bridge,
            ):
                self._event(event)

        def _event(self, event: dict) -> None:
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()

        def _bytes(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

    return Handler
