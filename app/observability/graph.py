# Internal and Confidential - Not for External Distribution.
"""Read a graphify graph without pulling in the evaluation harness.

The OSSIE materialisation step needs the nodes and edges of a graph and the
file each node came from. Those three facts lived in the harness graph
module; they live here now so observability never imports the harness.
"""

import json
from dataclasses import dataclass
from pathlib import Path


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
