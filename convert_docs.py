# Internal and Confidential - Not for External Distribution.
"""Convert a folder of public PDFs into heading-structured markdown."""

import argparse
import sys
from pathlib import Path

from app.convert import convert_dir


def main(argv: list[str] | None = None) -> int:
    """Run ``python convert_docs <pdf-dir> --out <md-dir>``."""
    parser = argparse.ArgumentParser(prog="convert_docs")
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    status = convert_dir(args.pdf_dir, args.out)
    if status != 0:
        print("refusing to write a document that yielded zero sections", file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
