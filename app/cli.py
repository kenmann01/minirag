# Internal and Confidential - Not for External Distribution.
"""Dispatch Mini RAG ingestion, question answering, and evaluation commands."""

import argparse
import json
import sys
from pathlib import Path

from app.cache import lookup, store
from app.db import DatabaseAdapter
from app.evaluate import format_table, load_goldens, run_exam
from app.generate import REFUSAL, LanguageModel, generate
from app.ingest import run
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
    args = parser.parse_args(argv)
    adapter = database_adapter or PgAdapter()
    if args.command == "ingest":
        count = run(adapter)
        print(f"ingested {count} policy chunks", file=sys.stderr)
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
