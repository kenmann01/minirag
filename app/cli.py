import argparse
import sys

from app.ingest import run
from app.pgadapter import PgAdapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", help="Ingest policy.md into the vector store")
    args = parser.parse_args(argv)
    if args.command == "ingest":
        run(PgAdapter())
        print("ingested 6 policy chunks", file=sys.stderr)
        return 0
    return 1
