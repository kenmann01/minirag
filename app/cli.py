import argparse
import json
import sys
from pathlib import Path

from app.cache import lookup, store
from app.db import DatabaseAdapter
from app.generate import LanguageModel, generate
from app.ingest import run
from app.pgadapter import PgAdapter
from app.retrieve import search

EVAL_QUESTIONS = [
    "How much can I spend on food each day?",
    "Can I book first-class airfare?",
    "My hotel costs $250. What do I need?",
    "Do I need a receipt for a $20 taxi?",
    "Can I claim a limousine upgrade?",
    "Does the company reimburse gym memberships?",
]


def main(
    argv: list[str] | None = None,
    *,
    database_adapter: DatabaseAdapter | None = None,
    language_model: LanguageModel | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="app")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", help="Ingest the Policy folder into the vector store")
    ask_parser = sub.add_parser("ask", help="Answer a question from the policy")
    ask_parser.add_argument("question")
    eval_parser = sub.add_parser("eval", help="Evaluate the required questions")
    eval_parser.add_argument(
        "--output", type=Path, default=Path("tests/output.json")
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
    if args.command == "ask":
        cached = lookup(args.question, adapter)
        if cached is not None:
            print(cached.model_dump_json())
            return 0
        response = generate(
            args.question, search(args.question, adapter), language_model
        )
        store(args.question, response, adapter)
        print(response.model_dump_json())
        return 0
    if args.command == "eval":
        results = []
        for question in EVAL_QUESTIONS:
            response = generate(question, search(question, adapter), language_model)
            results.append({"question": question, **response.model_dump()})
        args.output.write_text(json.dumps(results, indent=2) + "\n")
        return 0
    return 1
