# Internal and Confidential - Not for External Distribution.
"""Read a graph and select the edges a task list may use."""

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_CALLS = {"call", "calls", "import", "imports"}


@dataclass(frozen=True)
class Edge:
    """One directed relationship taken from a graphify graph."""

    source: str
    target: str
    relation: str
    confidence: str
    source_file: str
    target_file: str


def node_file(node: dict | None) -> str:
    """Return the first file path key present on a graph node."""
    if not node:
        return ""
    for key in ("source_file", "file", "path", "file_path"):
        if node.get(key):
            return str(node[key])
    return ""


def _endpoint(value) -> str:
    if isinstance(value, dict):
        return str(value.get("id", ""))
    return str(value)


def load_graph(path: Path) -> tuple[list[dict], list[Edge]]:
    """Load nodes and edges from a graphify ``graph.json``.

    The file may name its relationships ``edges`` or ``links``.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = list(data.get("nodes") or [])
    raw_edges = data.get("edges") or data.get("links") or []
    by_id = {str(node.get("id")): node for node in nodes if node.get("id") is not None}
    edges: list[Edge] = []
    for raw in raw_edges:
        source = _endpoint(raw.get("source"))
        target = _endpoint(raw.get("target"))
        relation = str(raw.get("relation") or raw.get("type") or raw.get("label") or "")
        confidence = str(raw.get("confidence") or raw.get("confidence_tag") or "").upper()
        edges.append(
            Edge(
                source=source,
                target=target,
                relation=relation,
                confidence=confidence,
                source_file=str(raw.get("source_file") or node_file(by_id.get(source))),
                target_file=str(raw.get("target_file") or node_file(by_id.get(target))),
            )
        )
    return nodes, edges


def _sort_key(edge: Edge) -> tuple[str, str, str]:
    return (edge.source, edge.target, edge.relation)


def _question_key(edge: Edge) -> tuple[str, str, str]:
    return (edge.source, edge.relation.lower(), edge.source_file)


def callee_symbol(target: str) -> str:
    """Return the graded callee: the last ``:`` or ``.`` segment, lowercased."""
    return str(target).split(":")[-1].split(".")[-1].lower()


def candidate_edges(edges: list[Edge], limit: int = 4) -> list[Edge]:
    """Pick EXTRACTED call and import edges that ask one question, loan paths first.

    A key is source, relation, and source file. A key that occurs more than
    once is dropped, because the question would have two answers.
    The order is source, target, relation. A human does not choose the facts.
    """
    extracted = [
        edge for edge in edges if edge.confidence == "EXTRACTED" and edge.relation.lower() in _CALLS
    ]
    counts = Counter(_question_key(edge) for edge in extracted)
    unique = [edge for edge in extracted if counts[_question_key(edge)] == 1]

    def mentions_loan(edge: Edge) -> bool:
        blob = " ".join((edge.source, edge.target, edge.source_file, edge.target_file)).lower()
        return "loan" in blob

    primary = sorted((edge for edge in unique if mentions_loan(edge)), key=_sort_key)
    rest = sorted((edge for edge in unique if not mentions_loan(edge)), key=_sort_key)
    return (primary + rest)[:limit]


def node_label(nodes: list[dict], node_id: str) -> str:
    """Return the human label for a node id, or the id itself."""
    for node in nodes:
        if str(node.get("id")) == node_id:
            return str(node.get("label") or node_id)
    return node_id


def _mermaid_id(value: str, taken: set[str] | None = None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"
    base = cleaned[:48]
    if taken is None:
        return base
    candidate = base
    number = 2
    while candidate in taken:
        suffix = f"_{number}"
        candidate = f"{base[: 48 - len(suffix)]}{suffix}"
        number += 1
    taken.add(candidate)
    return candidate


def node_degree(edges: list[Edge]) -> dict[str, int]:
    """Count edges that touch each node. A self-loop counts once."""
    degree: dict[str, int] = {}
    for edge in edges:
        degree[edge.source] = degree.get(edge.source, 0) + 1
        if edge.target != edge.source:
            degree[edge.target] = degree.get(edge.target, 0) + 1
    return degree


def by_density(nodes: list[dict], edges: list[Edge]) -> list[dict]:
    """Return nodes with the most incident edges first.

    Equal degree keeps the original order.
    """
    degree = node_degree(edges)
    ranked = sorted(
        enumerate(nodes),
        key=lambda item: (-degree.get(str(item[1].get("id")), 0), item[0]),
    )
    return [node for _, node in ranked]


def module_key(path: str) -> str:
    """Group a file into a module. The loans package stays its own module."""
    parts = Path(path).parts
    if "loanaccount" in parts:
        return "loanaccount"
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else "module"


def module_mermaid(nodes: list[dict], edges: list[Edge], source_file: str, cap: int = 24) -> str:
    """Draw the most connected nodes in the file's module, up to ``cap``."""
    key = module_key(source_file)
    members = []
    for node in nodes:
        if module_key(node_file(node)) == key:
            members.append(node)
    members = by_density(members, edges)[:cap]
    ids = {str(node.get("id")) for node in members}
    taken: set[str] = set()
    drawn: dict[str, str] = {}
    lines = ["flowchart LR"]
    for node in members:
        node_id = str(node.get("id"))
        label = str(node.get("label") or node_id).replace('"', "'")
        drawn[node_id] = _mermaid_id(node_id, taken)
        lines.append(f'  {drawn[node_id]}["{label}"]')
    for edge in edges:
        if edge.source in ids and edge.target in ids:
            lines.append(f"  {drawn[edge.source]} -->|{edge.relation}| {drawn[edge.target]}")
    return "\n".join(lines)


def loans_covered(nodes: list[dict]) -> bool:
    """True when any node path mentions the Fineract loans package."""
    for node in nodes:
        if "loanaccount" in json.dumps(node).lower():
            return True
    return False
