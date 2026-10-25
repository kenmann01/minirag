"""The OSSIE unit: schema, graph loader, materialisation and live validation."""

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from app.cli import main
from app.observability.graph import load_graph, node_file

ROOT = Path(__file__).resolve().parents[1]
NODE_COUNT = 10
EDGE_COUNT = 6


def write_graph(path: Path) -> None:
    """Write the ten-node, six-edge graph the OSSIE tests materialise."""
    nodes = []
    links = []
    for index in range(1, 5):
        nodes.append(
            {
                "id": f"Loan{index}",
                "label": f"Loan{index}",
                "source_file": f"loanaccount/Loan{index}.java",
            }
        )
        nodes.append(
            {
                "id": f"apply{index}",
                "label": f"apply{index}",
                "source_file": f"loanaccount/Loan{index}.java",
            }
        )
        links.append(
            {
                "source": f"Loan{index}",
                "target": f"apply{index}",
                "relation": "calls",
                "confidence": "EXTRACTED",
                "source_file": f"loanaccount/Loan{index}.java",
                "target_file": f"loanaccount/Loan{index}.java",
            }
        )
    nodes.extend(
        [
            {"id": "Other", "label": "Other", "source_file": "other/Other.java"},
            {"id": "helper", "label": "helper", "source_file": "other/Other.java"},
        ]
    )
    links.append(
        {
            "source": "Other",
            "target": "helper",
            "relation": "calls",
            "confidence": "EXTRACTED",
            "source_file": "other/Other.java",
            "target_file": "other/Other.java",
        }
    )
    links.append(
        {
            "source": "Loan1",
            "target": "guessed",
            "relation": "calls",
            "confidence": "INFERRED",
            "source_file": "loanaccount/Loan1.java",
            "target_file": "loanaccount/Loan1.java",
        }
    )
    path.write_text(json.dumps({"nodes": nodes, "links": links}), encoding="utf-8")


def test_semantic_model_matches_the_pinned_schema():
    schema = json.loads((ROOT / "ossie" / "osi-schema.json").read_text(encoding="utf-8"))
    document = yaml.safe_load(
        (ROOT / "ossie" / "map-writes-the-test.yaml").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(document)
    assert document["version"] == "0.1.1"
    model = document["semantic_model"][0]
    names = {dataset["name"] for dataset in model["datasets"]}
    assert {"retrieved_chunks", "graph_nodes", "graph_edges"} <= names
    extension = json.loads(model["custom_extensions"][0]["data"])
    assert extension["minirag"]["heading"] == "## N. Title"
    assert "call_scan" not in extension
    assert (
        "chunk id" in model["ai_context"]["instructions"].lower()
        or "chunk ids" in model["ai_context"]["instructions"].lower()
    )


def test_a_short_source_defaults_to_the_public_schema():
    from app.observability.ossie import _schema_and_table

    assert _schema_and_table("graph_nodes") == ("public", "graph_nodes")


def test_a_non_column_expression_is_rejected():
    from app.observability.ossie import _declared_columns

    columns, error = _declared_columns(
        {
            "name": "dataset",
            "fields": [
                {
                    "name": "total",
                    "expression": {
                        "dialects": [{"dialect": "ANSI_SQL", "expression": "sum(amount)"}]
                    },
                }
            ],
        }
    )
    assert columns == []
    assert "non-column expression" in error
    assert "total" in error


def test_the_graph_loader_reads_links_with_object_endpoints(tmp_path):
    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps(
            {
                "nodes": [{"id": "Loan", "source_file": "pkg/Loan.java"}],
                "links": [
                    {
                        "source": {"id": "Loan"},
                        "target": {"id": "approve"},
                        "relation": "calls",
                        "confidence": "extracted",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    nodes, edges = load_graph(graph)
    assert nodes[0]["id"] == "Loan"
    assert edges[0].source == "Loan"
    assert edges[0].target == "approve"
    assert edges[0].confidence == "EXTRACTED"
    assert edges[0].source_file == "pkg/Loan.java"


def test_node_file_tolerates_missing_nodes_and_unknown_shapes():
    assert node_file(None) == ""
    assert node_file({}) == ""
    assert node_file({"file": "pkg/A.java"}) == "pkg/A.java"


@pytest.fixture(scope="module")
def ingested_corpus():
    """The live validation needs the corpus tables the map declares."""
    from app.corpus.ingest import run as run_ingest
    from app.storage.pgadapter import PgAdapter

    run_ingest(PgAdapter())


def _materialize(tmp_path: Path):
    """Point a live adapter at one freshly materialised fixture graph."""
    from app.storage.pgadapter import PgAdapter

    graph = tmp_path / "graph.json"
    write_graph(graph)
    adapter = PgAdapter()
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS graph_edges")
        conn.execute("DROP TABLE IF EXISTS graph_nodes")
    assert main(["ossie", "materialize", "--graph", str(graph)], database_adapter=adapter) == 0
    return adapter


def test_materialize_loads_the_fixture_graph_and_reruns_without_duplicates(
    tmp_path, ingested_corpus
):
    from app.storage.pgadapter import PgAdapter

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


def test_validate_passes_when_every_declared_table_is_real(tmp_path, ingested_corpus):
    assert main(["ossie", "validate"], database_adapter=_materialize(tmp_path)) == 0


def test_validate_rejects_a_map_that_breaks_the_schema(tmp_path, ingested_corpus, capsys):
    adapter = _materialize(tmp_path)
    broken = tmp_path / "broken.yaml"
    document = yaml.safe_load(
        (ROOT / "ossie" / "map-writes-the-test.yaml").read_text(encoding="utf-8")
    )
    document["version"] = "9.9.9"
    broken.write_text(yaml.safe_dump(document), encoding="utf-8")
    code = main(["ossie", "validate", "--map", str(broken)], database_adapter=adapter)
    assert code != 0
    err = capsys.readouterr().err
    assert "fails the OSSIE 0.1.1 schema" in err
    assert "was expected" in err


def test_validate_names_the_first_missing_table(tmp_path, ingested_corpus, capsys):
    adapter = _materialize(tmp_path)
    with adapter.connect() as conn:
        conn.execute("DROP TABLE graph_edges")
    code = main(["ossie", "validate"], database_adapter=adapter)
    err = capsys.readouterr().err
    assert code != 0
    assert "missing table: public.graph_edges" in err


def test_validate_names_the_drifted_column(tmp_path, ingested_corpus, capsys):
    adapter = _materialize(tmp_path)
    with adapter.connect() as conn:
        conn.execute("ALTER TABLE graph_edges RENAME COLUMN confidence TO trust")
    code = main(["ossie", "validate"], database_adapter=adapter)
    err = capsys.readouterr().err
    assert code != 0
    assert "missing column: public.graph_edges.confidence" in err


def test_validate_names_a_field_that_is_not_a_bare_column(tmp_path, ingested_corpus, capsys):
    adapter = _materialize(tmp_path)
    document = yaml.safe_load(
        (ROOT / "ossie" / "map-writes-the-test.yaml").read_text(encoding="utf-8")
    )
    field = document["semantic_model"][0]["datasets"][0]["fields"][0]
    field["expression"]["dialects"][0]["expression"] = "count(*)"
    broken = tmp_path / "expression.yaml"
    broken.write_text(yaml.safe_dump(document), encoding="utf-8")
    code = main(["ossie", "validate", "--map", str(broken)], database_adapter=adapter)
    err = capsys.readouterr().err
    assert code != 0
    assert "non-column expression" in err
