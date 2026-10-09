"""The Ossie unit validates against the pinned 0.1.1 schema."""

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


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
    assert extension["call_scan"]["mermaid"] == "module-level"
    assert extension["call_scan"]["skill"] == "skills/call-scan"
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
