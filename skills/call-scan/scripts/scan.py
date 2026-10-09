"""Scan a Java tree for unambiguous method calls.

The script writes graph.json. It does not choose which repository to map.
A call is kept when a type prefix names the target, or when the bare method
name is declared once in the tree.
"""

import argparse
import json
import re
import sys
from pathlib import Path

_SKIP = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "do",
    "try",
    "new",
    "return",
    "throw",
    "else",
    "synchronized",
    "assert",
    "instanceof",
}
_TYPE_KIND = re.compile(r"\b(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)")
_THROWS = re.compile(r"throws\s+[\w.,\s]+")


def blank_literals(source: str) -> str:
    """Replace comments and quoted text with spaces, keeping newlines."""
    out: list[str] = []
    i = 0
    n = len(source)
    while i < n:
        c = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if c == "/" and nxt == "*":
            out.extend("  ")
            i += 2
            while i < n - 1 and not (source[i] == "*" and source[i + 1] == "/"):
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            if i < n - 1:
                out.extend("  ")
                i += 2
            continue
        if c == '"' and source[i : i + 3] == '"""':
            out.extend("   ")
            i += 3
            while i < n and source[i : i + 3] != '"""':
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            if i < n:
                out.extend("   ")
                i += 3
            continue
        if c in {'"', "'"}:
            quote = c
            out.append(" ")
            i += 1
            while i < n and source[i] != quote:
                if source[i] == "\\" and i + 1 < n:
                    out.extend("  ")
                    i += 2
                    continue
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            if i < n:
                out.append(" ")
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _header_before(source: str, index: int) -> str:
    j = index - 1
    while j >= 0 and source[j] not in "{};":
        j -= 1
    return source[j + 1 : index]


def _ident_before(source: str, index: int) -> tuple[str, int]:
    j = index - 1
    while j >= 0 and source[j].isspace():
        j -= 1
    end = j
    while j >= 0 and (source[j].isalnum() or source[j] == "_"):
        j -= 1
    return source[j + 1 : end + 1], j + 1


def _matching_paren(source: str, open_index: int) -> int:
    depth = 0
    for i in range(open_index, len(source)):
        if source[i] == "(":
            depth += 1
        elif source[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _method_name(header: str, type_name: str) -> str | None:
    if "=" in header:
        return None
    open_paren = header.rfind("(")
    if open_paren < 0:
        return None
    name, start = _ident_before(header, open_paren)
    if not name or name in _SKIP:
        return None
    close = _matching_paren(header, open_paren)
    if close < 0:
        return None
    tail = header[close + 1 :].strip()
    if tail and _THROWS.fullmatch(tail) is None:
        return None
    before = header[:start].strip()
    if name == type_name or before:
        return name
    return None


def _type_name(header: str) -> str | None:
    match = _TYPE_KIND.search(header)
    return match.group(1) if match else None


def _enclosing_type(types_at: dict[int, str], depth: int) -> tuple[str, int] | None:
    opened = [level for level in types_at if level < depth]
    if not opened:
        return None
    level = max(opened)
    return types_at[level], level


def _call_at(source: str, paren: int) -> tuple[str | None, str] | None:
    name, start = _ident_before(source, paren)
    if not name or name in _SKIP:
        return None
    j = start - 1
    while j >= 0 and source[j].isspace():
        j -= 1
    qualifier = None
    qual_start = start
    if j >= 0 and source[j] == ".":
        qualifier, qual_start = _ident_before(source, j)
        if not qualifier:
            qualifier = None
            qual_start = start
    prefix, prefix_start = _ident_before(source, qual_start)
    if prefix == "new":
        return None
    return qualifier, name


def _scan_file(path: Path, rel: str) -> tuple[list[dict], list[tuple[str, str | None, str]]]:
    try:
        blanked = blank_literals(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return [], []
    nodes: list[dict] = []
    calls: list[tuple[str, str | None, str]] = []
    types_at: dict[int, str] = {}
    methods_at: dict[int, str] = {}
    depth = 0
    for index, char in enumerate(blanked):
        if char == "{":
            header = _header_before(blanked, index)
            declared = _type_name(header)
            if declared:
                types_at[depth] = declared
            else:
                enclosing = _enclosing_type(types_at, depth)
                if enclosing and enclosing[1] == depth - 1:
                    method = _method_name(header, enclosing[0])
                    if method and method not in _SKIP:
                        node_id = f"{enclosing[0]}.{method}"
                        nodes.append(
                            {"id": node_id, "label": method, "source_file": rel}
                        )
                        methods_at[depth] = node_id
            depth += 1
            continue
        if char == "}":
            depth = max(0, depth - 1)
            types_at.pop(depth, None)
            methods_at.pop(depth, None)
            continue
        if char == ";":
            enclosing = _enclosing_type(types_at, depth)
            if enclosing and enclosing[1] == depth - 1:
                method = _method_name(_header_before(blanked, index), enclosing[0])
                if method and method not in _SKIP:
                    nodes.append(
                        {
                            "id": f"{enclosing[0]}.{method}",
                            "label": method,
                            "source_file": rel,
                        }
                    )
            continue
        if char != "(":
            continue
        opened = [level for level in methods_at if level < depth]
        if not opened:
            continue
        caller = methods_at[max(opened)]
        found = _call_at(blanked, index)
        if found:
            calls.append((caller, found[0], found[1]))
    return nodes, calls


def build_graph(tree: Path, base: Path | None = None) -> dict | None:
    """Return the call graph for ``tree``, or None when that directory is absent.

    ``source_file`` is relative to ``base`` when given, otherwise to ``tree``.
    """
    if not tree.is_dir():
        return None
    root = base or tree
    declared: dict[str, dict] = {}
    raw_calls: list[tuple[str, str | None, str]] = []
    for path in sorted(tree.rglob("*.java")):
        rel = path.relative_to(root).as_posix()
        nodes, calls = _scan_file(path, rel)
        for node in nodes:
            declared.setdefault(node["id"], node)
        raw_calls.extend(calls)
    by_label: dict[str, list[str]] = {}
    for node_id, node in declared.items():
        by_label.setdefault(node["label"], []).append(node_id)
    links: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for caller, qualifier, name in raw_calls:
        target = _resolve(caller, qualifier, name, declared, by_label)
        if target is None or target == caller or (caller, target) in seen:
            continue
        seen.add((caller, target))
        links.append(
            {
                "source": caller,
                "target": target,
                "relation": "calls",
                "confidence": "EXTRACTED",
            }
        )
    nodes = [declared[node_id] for node_id in sorted(declared)]
    links.sort(key=lambda item: (item["source"], item["target"]))
    return {"nodes": nodes, "links": links}


def _resolve(
    caller: str,
    qualifier: str | None,
    name: str,
    declared: dict[str, dict],
    by_label: dict[str, list[str]],
) -> str | None:
    if qualifier == "this":
        target = f"{caller.split('.', 1)[0]}.{name}"
        return target if target in declared else None
    if qualifier:
        target = f"{qualifier}.{name}"
        return target if target in declared else None
    options = by_label.get(name) or []
    if len(options) == 1:
        return options[0]
    return None


def main(argv: list[str] | None = None) -> int:
    """Write graph.json for ``tree`` under ``--out``. A missing tree exits 1."""
    parser = argparse.ArgumentParser(description="Scan a Java tree for method calls")
    parser.add_argument("tree", type=Path)
    parser.add_argument("--base", type=Path, help="Path prefix stored on each node")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    graph = build_graph(args.tree, args.base)
    if graph is None:
        print("source tree is missing", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "graph.json").write_text(
        json.dumps(graph, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
