"""Ossie materialization and live validate against the real Postgres."""

import uuid
from pathlib import Path

import pytest
import yaml

from app.cli import main
from app.corpus.ingest import run as run_ingest
from app.harness.metrics import MetricsSink
from app.storage.pgadapter import PgAdapter
from tests.test_tasks import write_graph

ROOT = Path(__file__).resolve().parents[1]
NODE_COUNT = 10
EDGE_COUNT = 6
GATE_RECORD = {
    "scope": "full",
    "wall_time_seconds": 0.0,
    "node_count": 0,
    "edge_count": 0,
    "largest_mermaid": {"name": None, "nodes": 0, "rendered": False},
    "errors": [],
    "loans_covered": False,
    "graph_path": None,
}


def test_materialize_loads_the_fixture_graph_and_reruns_without_duplicates(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)
    adapter = PgAdapter()
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS graph_edges")
        conn.execute("DROP TABLE IF EXISTS graph_nodes")

    first = main(["ossie", "materialize", "--graph", str(graph)], database_adapter=adapter)
    with adapter.connect() as conn:
        nodes = conn.execute(
            "SELECT node_id, label, source_file FROM graph_nodes ORDER BY node_id"
        ).fetchall()
        edges = conn.execute("""
            SELECT source_id, target_id, relation, confidence
            FROM graph_edges
            ORDER BY source_id, target_id, relation
            """).fetchall()

    second = main(["ossie", "materialize", "--graph", str(graph)], database_adapter=adapter)
    with adapter.connect() as conn:
        nodes_again = conn.execute("SELECT count(*) FROM graph_nodes").fetchone()[0]
        edges_again = conn.execute("SELECT count(*) FROM graph_edges").fetchone()[0]

    assert first == 0
    assert second == 0
    assert len(nodes) == NODE_COUNT
    assert ("Loan1", "Loan1", "loanaccount/Loan1.java") in nodes
    assert ("Loan1", "apply1", "calls", "EXTRACTED") in edges
    assert ("Loan1", "guessed", "calls", "INFERRED") in edges
    assert (nodes_again, edges_again) == (NODE_COUNT, EDGE_COUNT)


@pytest.fixture(scope="module", autouse=True)
def ingested_corpus():
    run_ingest(PgAdapter())


def test_ingest_writes_one_sections_row_per_parent_section():
    adapter = PgAdapter()
    with adapter.connect() as conn:
        sections = conn.execute("""
            SELECT source_doc, section, section_title, parent_text,
                   effective_date, superseded_by
            FROM sections
            ORDER BY source_doc, section
            """).fetchall()
        chunk_pairs = conn.execute(
            "SELECT DISTINCT source_doc, section FROM policy_chunks"
        ).fetchall()
    assert {(row[0], row[1]) for row in sections} == set(chunk_pairs)
    assert all(
        row[4] is None and row[5] is None for row in sections
    ), "the public banking corpus carries no lineage markers"
    with adapter.connect() as conn:
        parent = conn.execute("""
            SELECT s.parent_text, c.parent_text
            FROM sections s
            JOIN policy_chunks c
              ON c.source_doc = s.source_doc AND c.section = s.section
            LIMIT 1
            """).fetchone()
    assert parent[0] == parent[1]


def test_ingest_self_heals_a_partial_sections_table():
    adapter = PgAdapter()
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS sections")
        conn.execute("""
            CREATE TABLE sections (
                source_doc TEXT NOT NULL,
                section TEXT NOT NULL,
                PRIMARY KEY (source_doc, section)
            )
            """)
    run_ingest(PgAdapter())
    with adapter.connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'sections'"
            ).fetchall()
        }
        count = conn.execute("SELECT count(*) FROM sections").fetchone()[0]
    assert {"section_title", "parent_text", "effective_date", "superseded_by"} <= columns
    assert count > 0


def _ensure_metrics_tables(adapter):
    """Touch the four metrics tables through their public writers."""
    MetricsSink(adapter).record_context(
        run_id=uuid.uuid4(),
        context="bare",
        scoreboard={
            "tasks_total": 0,
            "tasks_passed": 0,
            "passed_by_tier": {"1": {"passed": 0}, "2": {"passed": 0}},
            "tool_calls": 0,
            "cost_usd": 0.0,
        },
        cases=[],
    )
    MetricsSink(adapter).record_gate(dict(GATE_RECORD))


@pytest.fixture(autouse=True)
def materialized_graph(tmp_path):
    adapter = PgAdapter()
    _ensure_metrics_tables(adapter)
    graph = tmp_path / "graph.json"
    write_graph(graph)
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS graph_edges")
        conn.execute("DROP TABLE IF EXISTS graph_nodes")
    assert main(["ossie", "materialize", "--graph", str(graph)], database_adapter=adapter) == 0
    return adapter


def test_validate_passes_when_every_declared_table_is_real(materialized_graph):
    assert main(["ossie", "validate"], database_adapter=materialized_graph) == 0


def test_validate_rejects_a_map_that_breaks_the_schema(materialized_graph, tmp_path, capsys):
    broken = tmp_path / "broken.yaml"
    document = yaml.safe_load(
        (ROOT / "ossie" / "map-writes-the-test.yaml").read_text(encoding="utf-8")
    )
    document["version"] = "9.9.9"
    broken.write_text(yaml.safe_dump(document), encoding="utf-8")
    code = main(
        ["ossie", "validate", "--map", str(broken)],
        database_adapter=materialized_graph,
    )
    assert code != 0
    err = capsys.readouterr().err
    assert "fails the OSSIE 0.1.1 schema" in err
    assert "was expected" in err


def test_validate_names_the_first_missing_table(materialized_graph, capsys):
    adapter = materialized_graph
    with adapter.connect() as conn:
        conn.execute("DROP TABLE graph_edges")
    code = main(["ossie", "validate"], database_adapter=adapter)
    err = capsys.readouterr().err
    assert code != 0
    assert "missing table: public.graph_edges" in err


def test_validate_names_the_drifted_column(materialized_graph, capsys):
    adapter = materialized_graph
    with adapter.connect() as conn:
        conn.execute("ALTER TABLE graph_edges RENAME COLUMN confidence TO trust")
    code = main(["ossie", "validate"], database_adapter=adapter)
    err = capsys.readouterr().err
    assert code != 0
    assert "missing column: public.graph_edges.confidence" in err


def test_validate_names_a_field_that_is_not_a_bare_column(materialized_graph, tmp_path, capsys):
    document = yaml.safe_load(
        (ROOT / "ossie" / "map-writes-the-test.yaml").read_text(encoding="utf-8")
    )
    field = document["semantic_model"][0]["datasets"][0]["fields"][0]
    field["expression"]["dialects"][0]["expression"] = "count(*)"
    broken = tmp_path / "expression.yaml"
    broken.write_text(yaml.safe_dump(document), encoding="utf-8")
    code = main(["ossie", "validate", "--map", str(broken)], database_adapter=materialized_graph)
    err = capsys.readouterr().err
    assert code != 0
    assert "non-column expression" in err
