import argparse
import sys

from app.db import DatabaseAdapter
from app.generate import LanguageModel, generate
from app.ingest import run
from app.pgadapter import PgAdapter
from app.retrieve import search


def main(
    argv: list[str] | None = None,
    *,
    database_adapter: DatabaseAdapter | None = None,
    language_model: LanguageModel | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="app")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", help="Ingest policy.md into the vector store")
    ask_parser = sub.add_parser("ask", help="Answer a question from the policy")
    ask_parser.add_argument("question")
    args = parser.parse_args(argv)
    adapter = database_adapter or PgAdapter()
    if args.command == "ingest":
        run(adapter)
        print("ingested 6 policy chunks", file=sys.stderr)
        return 0
    if args.command == "ask":
        if language_model is None:
            from app.ollama import OllamaAdapter

            language_model = OllamaAdapter()
        response = generate(args.question, search(args.question, adapter), language_model)
        print(response.model_dump_json())
        return 0
    return 1
