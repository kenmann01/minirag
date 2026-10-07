# Internal and Confidential - Not for External Distribution.
"""Dispatch Mini RAG ingestion, question answering, and evaluation commands."""

import argparse
import json
import sys
from pathlib import Path

from app.cache import lookup, store
from app.config import get_settings
from app.db import DatabaseAdapter
from app.evaluate import format_table, load_goldens, run_exam
from app.generate import REFUSAL, LanguageModel, generate
from app.ingest import EmptyCorpusError, run
from app.pgadapter import PgAdapter
from app.reranker import CrossEncoderReranker, Reranker
from app.retrieve import search


def main(
    argv: list[str] | None = None,
    *,
    database_adapter: DatabaseAdapter | None = None,
    language_model: LanguageModel | None = None,
    reranker: Reranker | None = None,
) -> int:
    """Run a Mini RAG command.

    Args:
        argv: Command-line arguments excluding the executable name. Uses the
            process arguments when omitted.
        database_adapter: Optional database dependency, primarily for testing.
        language_model: Optional generation dependency, primarily for testing.
        reranker: Optional reranking dependency, primarily for testing.

    Returns:
        A process exit status: zero for a handled command and one otherwise.
    """
    parser = argparse.ArgumentParser(prog="app")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", help="Ingest the Policy folder into the vector store")
    ask_parser = sub.add_parser("ask", help="Answer a question from the policy")
    ask_parser.add_argument("question")
    ask_parser.add_argument(
        "--include-superseded",
        action="store_true",
        help="Bypass the lineage filter to reproduce the planted defect; never cached",
    )
    eval_parser = sub.add_parser(
        "eval", help="Run the golden exam and write the harness record"
    )
    eval_parser.add_argument("--output", type=Path, default=Path("eval/record.json"))
    eval_parser.add_argument(
        "--retriever",
        choices=["hybrid", "vector"],
        default="hybrid",
        help="A/B the retriever: vector disables the keyword lane",
    )
    eval_parser.add_argument(
        "--include-superseded",
        action="store_true",
        help="Bypass the lineage filter in the exam retrieval",
    )
    serve_parser = sub.add_parser("serve", help="Open the ask-trace page")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    retrieve_parser = sub.add_parser("retrieve", help="Return chunk JSON without generating")
    retrieve_parser.add_argument("query")
    retrieve_parser.add_argument("--top-k", type=int, default=20)
    retrieve_parser.add_argument("--json", type=Path)
    score_parser = sub.add_parser("score", help="Score one task list three times")
    score_parser.add_argument("--repo", type=Path, required=True)
    score_parser.add_argument("--graph", type=Path, required=True)
    score_parser.add_argument("--output", type=Path, required=True)
    score_parser.add_argument(
        "--no-metrics",
        action="store_true",
        help="Skip writing the run metrics into Postgres",
    )
    compare_parser = sub.add_parser("compare", help="Show the three-run comparison")
    compare_parser.add_argument("report_dir", type=Path)
    compare_parser.add_argument("--host", default="127.0.0.1")
    compare_parser.add_argument("--port", type=int, default=8766)
    gate_parser = sub.add_parser("gate", help="Map a repository and record the scale numbers")
    gate_parser.add_argument("--repo", type=Path, required=True)
    gate_parser.add_argument("--output", type=Path, default=Path("eval/fineract-gate.json"))
    live_parser = sub.add_parser("live", help="Watch each score call while it runs")
    live_parser.add_argument("--host", default="127.0.0.1")
    live_parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args(argv)
    if args.command == "serve":
        from app.serve import serve

        serve(
            args.host,
            args.port,
            database_adapter=database_adapter,
            language_model=language_model,
            reranker=reranker,
        )
        return 0
    adapter = database_adapter or PgAdapter()
    if args.command == "ingest":
        configured = get_settings().corpus_dir.strip()
        try:
            count = run(adapter, corpus_dir=Path(configured) if configured else None)
        except EmptyCorpusError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"ingested {count} policy chunks", file=sys.stderr)
        return 0
    if args.command == "retrieve":
        from app.bridge import retrieve

        payload = retrieve(args.query, adapter, top_k=args.top_k)
        text = json.dumps(payload, indent=2)
        if args.json is not None:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    if args.command == "compare":
        from app.compare import serve_compare

        serve_compare(args.report_dir, args.host, args.port)
        return 0
    if args.command == "gate":
        from app.gate import run_gate

        record = run_gate(args.repo, args.output)
        print(json.dumps(record, indent=2))
        return 0 if record["node_count"] else 1
    if args.command == "live":
        from app.live import serve_live

        serve_live(
            args.host,
            args.port,
            database_adapter=database_adapter,
            language_model=language_model,
        )
        return 0
    if args.command == "score":
        from app.bridge import retrieve
        from app.score import TaskGenerationError, run_score

        if language_model is None:
            from app.ollama import OllamaAdapter

            language_model = OllamaAdapter()

        def bridge(query: str) -> dict:
            return retrieve(query, adapter)

        try:
            rows = run_score(
                graph_path=args.graph,
                repo=args.repo,
                output_dir=args.output,
                model=language_model,
                retrieve=bridge,
                database_adapter=None if args.no_metrics else adapter,
            )
        except TaskGenerationError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        for row in rows:
            print(
                f"{row['run_id']}  {row['context']}  "
                f"{row['tasks_passed']} of {row['tasks_total']}  "
                f"tools={row['tool_calls']}  cost=${row['cost_usd']:.4f}"
            )
        return 0
    if args.command in {"ask", "eval"}:
        if language_model is None:
            from app.ollama import OllamaAdapter

            language_model = OllamaAdapter()
        if reranker is None:
            reranker = CrossEncoderReranker()
    if args.command == "ask":
        if not args.include_superseded:
            cached = lookup(args.question, adapter)
            if cached is not None:
                print(cached.model_dump_json())
                return 0
        response = generate(
            args.question,
            reranker.rank(
                args.question,
                search(args.question, adapter, include_superseded=args.include_superseded),
            ),
            language_model,
        )
        if response.answer != REFUSAL and not args.include_superseded:
            store(args.question, response, adapter)
        print(response.model_dump_json())
        return 0
    if args.command == "eval":
        goldens = load_goldens()
        record = run_exam(
            goldens,
            adapter=adapter,
            model=language_model,
            reranker=reranker,
            mode=args.retriever,
            include_superseded=args.include_superseded,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2) + "\n")
        print(format_table(record))
        return 0 if record["summary"]["passed"] == record["summary"]["total"] else 1
    return 1
