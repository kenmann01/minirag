"""The gate CLI writes its scale numbers into Postgres as history rows."""

import json
from pathlib import Path

import pytest

from app.cli import main
from app.metrics import MetricsSink
from app.pgadapter import PgAdapter

ROOT = Path(__file__).resolve().parents[1]

MINIMAL_RECORD = {
    "scope": "full",
    "wall_time_seconds": 0.0,
    "node_count": 0,
    "edge_count": 0,
    "largest_mermaid": {"name": None, "nodes": 0, "rendered": False},
    "errors": [],
    "loans_covered": False,
    "graph_path": None,
}


@pytest.fixture(scope="module", autouse=True)
def gate_metrics_table():
    """Create the sink table once through its public writer."""
    MetricsSink(PgAdapter()).record_gate(dict(MINIMAL_RECORD))


@pytest.fixture(autouse=True)
def mocked_mapper(tmp_path, monkeypatch):
    """Stand in for the call-scan skill the way the gate tests do."""

    def fake_run(repo, work_dir):
        graph = work_dir / "graph.json"
        graph.parent.mkdir(parents=True, exist_ok=True)
        graph.write_text(
            json.dumps(
                {
                    "nodes": [
                        {"id": "Loan", "source_file": "loanaccount/Loan.java"},
                        {"id": "Apply", "source_file": "loanaccount/Apply.java"},
                    ],
                    "links": [
                        {
                            "source": "Loan",
                            "target": "Apply",
                            "relation": "calls",
                            "confidence": "EXTRACTED",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return graph, [], 1.5

    monkeypatch.setattr("app.gate.run_call_scan", fake_run)
    monkeypatch.setattr(
        "app.gate.render_largest",
        lambda diagrams, out_dir: ({"name": "loanaccount", "nodes": 2, "rendered": False}, []),
    )


def gate_through_cli(tmp_path) -> Path:
    output = tmp_path / "fineract-gate.json"
    code = main(
        ["gate", "--repo", str(tmp_path), "--output", str(output)],
        database_adapter=PgAdapter(),
    )
    assert code == 0
    return output


def test_gate_cli_writes_one_gate_metrics_row_with_the_five_numbers(tmp_path):
    adapter = PgAdapter()
    with adapter.connect() as conn:
        before_ids = {row[0] for row in conn.execute("SELECT run_id FROM gate_metrics").fetchall()}
    output = gate_through_cli(tmp_path)
    with adapter.connect() as conn:
        rows = conn.execute("""
            SELECT run_id, gate, wall_time_seconds, node_count, edge_count,
                   passed, created_at
            FROM gate_metrics
            """).fetchall()
    new_rows = [row for row in rows if row[0] not in before_ids]
    document = json.loads(output.read_text(encoding="utf-8"))
    assert len(new_rows) == 1
    row = new_rows[0]
    assert row[1] == document
    assert isinstance(row[2], float)
    assert row[2] == document["wall_time_seconds"] == 1.5
    assert isinstance(row[3], int)
    assert row[3] == document["node_count"] == 2
    assert isinstance(row[4], int)
    assert row[4] == document["edge_count"] == 1
    assert row[5] is True
    assert row[6] is not None


def test_gate_cli_appends_a_second_row_and_leaves_history_untouched(tmp_path):
    adapter = PgAdapter()
    gate_through_cli(tmp_path)
    with adapter.connect() as conn:
        first = conn.execute("""
            SELECT run_id, gate, wall_time_seconds, node_count, edge_count,
                   passed, created_at
            FROM gate_metrics
            """).fetchall()
    gate_through_cli(tmp_path)
    with adapter.connect() as conn:
        second = conn.execute("""
            SELECT run_id, gate, wall_time_seconds, node_count, edge_count,
                   passed, created_at
            FROM gate_metrics
            """).fetchall()
    assert len(second) == len(first) + 1
    assert {row[0] for row in second} - {row[0] for row in first}
    assert len({row[0] for row in second}) == len(second)
    stored = {row[0]: row for row in second}
    for row in first:
        assert stored[row[0]] == row


def test_gate_cli_keeps_the_committed_json_output_shape(tmp_path):
    committed = json.loads((ROOT / "eval" / "fineract-gate.json").read_text(encoding="utf-8"))
    output = gate_through_cli(tmp_path)
    document = json.loads(output.read_text(encoding="utf-8"))
    assert set(document) == set(committed)
    assert document["scope"] == "loans"
    assert document["wall_time_seconds"] == 1.5
    assert document["node_count"] == 2
    assert document["edge_count"] == 1
    assert document["largest_mermaid"] == {
        "name": "loanaccount",
        "nodes": 2,
        "rendered": False,
    }
    assert document["errors"] == []
    assert document["loans_covered"] is True
    assert document["graph_path"].endswith("graph.json")
