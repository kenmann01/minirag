# Internal and Confidential - Not for External Distribution.
"""Materialize a graphify graph into Postgres and validate the OSSIE map.

The OSSIE map declares the physical tables the demo contract depends on.
Materialization loads a repository graph into ``graph_nodes`` and
``graph_edges``; validation proves every declared dataset exists with its
declared columns in the live database.
"""

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from app.db import DatabaseAdapter
from app.graph import load_graph, node_file

DEFAULT_MAP = Path(__file__).resolve().parents[1] / "ossie" / "map-writes-the-test.yaml"

_CREATE_GRAPH_NODES = """
CREATE TABLE IF NOT EXISTS graph_nodes (
    node_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    source_file TEXT NOT NULL DEFAULT ''
)
"""

_CREATE_GRAPH_EDGES = """
CREATE TABLE IF NOT EXISTS graph_edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source_id, target_id, relation, confidence)
)
"""

_INSERT_NODE = """
INSERT INTO graph_nodes (node_id, label, source_file)
VALUES (%(node_id)s, %(label)s, %(source_file)s)
ON CONFLICT (node_id) DO UPDATE SET
    label = EXCLUDED.label,
    source_file = EXCLUDED.source_file
"""

_INSERT_EDGE = """
INSERT INTO graph_edges (source_id, target_id, relation, confidence)
VALUES (%(source_id)s, %(target_id)s, %(relation)s, %(confidence)s)
ON CONFLICT (source_id, target_id, relation, confidence) DO NOTHING
"""


def materialize(adapter: DatabaseAdapter, graph_path: Path) -> int:
    """Replace the stored graph rows with one graphify graph.

    Args:
        adapter: Provider of a managed SQL connection.
        graph_path: Path to a graphify ``graph.json``.

    Returns:
        The number of rows written across both graph tables.

    Raises:
        OSError: The graph file could not be read.
        ValueError: The graph file is not valid JSON.

    Side Effects:
        Creates the graph tables when missing and swaps every row inside one
        transaction, so a re-run replaces the previous graph and never
        duplicates it.
    """
    nodes, edges = load_graph(graph_path)
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS graph_edges")
        conn.execute("DROP TABLE IF EXISTS graph_nodes")
        conn.execute(_CREATE_GRAPH_NODES)
        conn.execute(_CREATE_GRAPH_EDGES)
        for node in nodes:
            conn.execute(
                _INSERT_NODE,
                {
                    "node_id": str(node.get("id")),
                    "label": str(node.get("label") or node.get("id") or ""),
                    "source_file": node_file(node),
                },
            )
        for edge in edges:
            conn.execute(
                _INSERT_EDGE,
                {
                    "source_id": edge.source,
                    "target_id": edge.target,
                    "relation": edge.relation,
                    "confidence": edge.confidence,
                },
            )
    with adapter.connect() as conn:
        stored = conn.execute(
            "SELECT (SELECT count(*) FROM graph_nodes) + (SELECT count(*) FROM graph_edges)"
        ).fetchone()
    return int(stored[0])


def _declared_columns(dataset: dict) -> tuple[list[str], str]:
    """Return the physical columns for the dataset fields, or an error.

    A field's ANSI_SQL expression names the column, with any ``table.``
    qualifier stripped. Anything that is not a bare column name is rejected
    so live validation never silently skips an expression. The field name
    is the fallback when no dialects are declared.
    """
    columns = []
    for field in dataset.get("fields") or []:
        expression = field.get("expression") or {}
        ansi = next(
            (
                dialect["expression"]
                for dialect in expression.get("dialects") or []
                if dialect.get("dialect") == "ANSI_SQL"
            ),
            None,
        )
        column = field["name"] if ansi is None else str(ansi).split(".")[-1].strip()
        if not column.isidentifier():
            return [], (
                f"non-column expression on field {field['name']} of "
                f"{dataset.get('name', 'dataset')}: {ansi}"
            )
        columns.append(column)
    return columns, ""


def _schema_and_table(source: str) -> tuple[str, str]:
    """Split a ``database.schema.table`` source into its schema and table."""
    parts = source.split(".")
    if len(parts) < 2:
        return "public", parts[-1]
    return parts[-2], parts[-1]


def validate(adapter: DatabaseAdapter, map_path: Path = DEFAULT_MAP) -> tuple[int, str]:
    """Check the OSSIE map against its schema and the live database.

    Args:
        adapter: Provider of a managed SQL connection.
        map_path: Path to the OSSIE semantic model YAML.

    Returns:
        An exit status of zero when the map matches the pinned schema and
        every declared dataset exists with its declared columns. Otherwise
        one, with a message naming the first schema error, missing table, or
        missing column.
    """
    document = yaml.safe_load(map_path.read_text(encoding="utf-8"))
    schema = json.loads(DEFAULT_MAP.with_name("osi-schema.json").read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        return 1, f"{map_path.name} fails the OSSIE 0.1.1 schema: {errors[0].message}"
    with adapter.connect() as conn:
        for dataset in document["semantic_model"][0]["datasets"]:
            schema_name, table = _schema_and_table(dataset["source"])
            found = conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                """,
                (schema_name, table),
            ).fetchall()
            if not found:
                return 1, f"missing table: {schema_name}.{table}"
            columns = {row[0] for row in found}
            declared, field_error = _declared_columns(dataset)
            if field_error:
                return 1, field_error
            for column in declared:
                if column not in columns:
                    return 1, f"missing column: {schema_name}.{table}.{column}"
    return 0, ""
