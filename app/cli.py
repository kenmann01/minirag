# Internal and Confidential - Not for External Distribution.
"""Dispatch Mini RAG ingestion, retrieval, answering, and exam commands."""

import argparse
import json
import sys
from pathlib import Path

from app.config import get_settings
from app.corpus.ingest import EmptyCorpusError, run
from app.generation.generate import LanguageModel
from app.retrieval.exam import format_table, load_goldens, run_exam
from app.retrieval.reranker import CrossEncoderReranker, Reranker
from app.storage.db import DatabaseAdapter


def main(
    argv: list[str] | None = None,
    *,
    database_adapter: DatabaseAdapter | None = None,
    language_model: LanguageModel | None = None,
    reranker: Reranker | None = None,
    candidate_deps=None,
) -> int:
    """Run a Mini RAG command.

    Args:
        argv: Command-line arguments excluding the executable name. Uses the
            process arguments when omitted.
        database_adapter: Optional database dependency, primarily for testing.
        language_model: Optional generation dependency, primarily for testing.
        reranker: Optional reranking dependency, primarily for testing.
        candidate_deps: Optional candidate dependency bundle, primarily for testing.

    Returns:
        A process exit status: zero for a handled command and one otherwise.
    """
    parser = argparse.ArgumentParser(prog="app")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", help="Ingest the corpus folder into the vector store")
    # The retrieval exam: each fixed golden question through the same search
    # the answer path runs. Search reads policy_chunks.
    eval_parser = sub.add_parser("eval", help="Run the golden retrieval exam and write the record")
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
    retrieve_parser = sub.add_parser("retrieve", help="Return chunk JSON without generating")
    retrieve_parser.add_argument("query")
    retrieve_parser.add_argument("--top-k", type=int, default=20)
    retrieve_parser.add_argument("--json", type=Path)
    candidate_parser = sub.add_parser(
        "candidate", help="Serve the v1 candidate endpoint or run one request file"
    )
    candidate_mode = candidate_parser.add_mutually_exclusive_group()
    candidate_mode.add_argument("--serve", action="store_true", help="Serve POST /run")
    candidate_mode.add_argument("--request", type=Path, help="Run one request JSON file")
    candidate_parser.add_argument("--response", type=Path, help="Write the response JSON here")
    candidate_parser.add_argument("--host", default="127.0.0.1")
    candidate_parser.add_argument("--port", type=int, default=8768)
    ossie_parser = sub.add_parser("ossie", help="Materialize the graph and validate the OSSIE map")
    ossie_sub = ossie_parser.add_subparsers(dest="ossie_command", required=True)
    materialize_parser = ossie_sub.add_parser(
        "materialize", help="Load a graphify graph into graph_nodes and graph_edges"
    )
    materialize_parser.add_argument("--graph", type=Path, required=True)
    validate_parser = ossie_sub.add_parser(
        "validate", help="Check the OSSIE map against its schema and the live database"
    )
    validate_parser.add_argument("--map", type=Path, help="Path to the OSSIE map YAML")
    args = parser.parse_args(argv)
    if database_adapter is None:
        # The Postgres driver loads only when a command needs the real store,
        # so listing help or a candidate call never imports psycopg.
        from app.storage.pgadapter import PgAdapter

        database_adapter = PgAdapter()
    adapter = database_adapter
    if args.command == "candidate":
        from app.candidate import DefaultCandidateDeps, run_request_file, serve_candidate

        deps = candidate_deps
        if deps is None:
            deps = DefaultCandidateDeps(adapter=adapter, model=language_model)
        if args.serve:
            return serve_candidate(args.host, args.port, deps=deps)
        if args.request is None or args.response is None:
            print(
                "candidate needs --serve, or --request FILE --response FILE",
                file=sys.stderr,
            )
            return 2
        response = run_request_file(args.request, args.response, deps=deps)
        print(json.dumps(response, indent=2))
        return 0
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
        from app.retrieval.bridge import retrieve

        payload = retrieve(args.query, adapter, top_k=args.top_k)
        text = json.dumps(payload, indent=2)
        if args.json is not None:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    if args.command == "ossie":
        from app.observability.ossie import DEFAULT_MAP, materialize, validate

        if args.ossie_command == "materialize":
            try:
                rows = materialize(adapter, args.graph)
            except (OSError, ValueError) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(f"materialized {rows} graph rows", file=sys.stderr)
            return 0
        code, message = validate(adapter, args.map or DEFAULT_MAP)
        if message:
            print(message, file=sys.stderr)
        return code
    if args.command == "eval":
        if language_model is None:
            from app.generation.ollama import OllamaAdapter

            language_model = OllamaAdapter()
        if reranker is None:
            reranker = CrossEncoderReranker()
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
