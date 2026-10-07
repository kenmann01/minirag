# Internal and Confidential - Not for External Distribution.
"""Read a Graphify graph and select the edges a task list may use."""

import json
import re
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


def candidate_edges(edges: list[Edge], limit: int = 4) -> list[Edge]:
    """Pick EXTRACTED call and import edges, loan paths first.

    The order is source, target, relation. A human does not choose the facts.
    """
    extracted = [
        edge for edge in edges if edge.confidence == "EXTRACTED" and edge.relation.lower() in _CALLS
    ]

    def mentions_loan(edge: Edge) -> bool:
        blob = " ".join((edge.source, edge.target, edge.source_file, edge.target_file)).lower()
        return "loan" in blob

    primary = sorted((edge for edge in extracted if mentions_loan(edge)), key=_sort_key)
    rest = sorted((edge for edge in extracted if not mentions_loan(edge)), key=_sort_key)
    return (primary + rest)[:limit]


def node_label(nodes: list[dict], node_id: str) -> str:
    """Return the human label for a node id, or the id itself."""
    for node in nodes:
        if str(node.get("id")) == node_id:
            return str(node.get("label") or node_id)
    return node_id


def _mermaid_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"
    return cleaned[:48]


def module_key(path: str) -> str:
    """Group a file into a module. The loans package stays its own module."""
    parts = Path(path).parts
    if "loanaccount" in parts:
        return "loanaccount"
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else "module"


def module_mermaid(nodes: list[dict], edges: list[Edge], source_file: str, cap: int = 24) -> str:
    """Draw a module-level diagram that includes the file's module only."""
    key = module_key(source_file)
    members = []
    for node in nodes:
        if module_key(node_file(node)) == key:
            members.append(node)
    members = members[:cap]
    ids = {str(node.get("id")) for node in members}
    lines = ["flowchart LR"]
    for node in members:
        node_id = str(node.get("id"))
        label = str(node.get("label") or node_id).replace('"', "'")
        lines.append(f'  {_mermaid_id(node_id)}["{label}"]')
    for edge in edges:
        if edge.source in ids and edge.target in ids:
            lines.append(
                f"  {_mermaid_id(edge.source)} -->|{edge.relation}| {_mermaid_id(edge.target)}"
            )
    return "\n".join(lines)


def loans_covered(nodes: list[dict]) -> bool:
    """True when any node path mentions the Fineract loans package."""
    for node in nodes:
        if "loanaccount" in json.dumps(node).lower():
            return True
    return False
