"""Gate numbers come from the graph. A failed full map falls back to loans."""

import json

from app.gate import run_gate, summarize


def test_summarize_records_the_five_numbers(tmp_path):
    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "Loan", "source_file": "org/apache/fineract/portfolio/loanaccount/Loan.java"}
                ],
                "links": [
                    {
                        "source": "Loan",
                        "target": "Loan",
                        "relation": "calls",
                        "confidence": "EXTRACTED",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    record = summarize(
        graph,
        12.5,
        ["a limit"],
        {"name": "loanaccount", "nodes": 1, "rendered": False},
        "full",
    )
    assert record["wall_time_seconds"] == 12.5
    assert record["node_count"] == 1
    assert record["edge_count"] == 1
    assert record["largest_mermaid"]["name"] == "loanaccount"
    assert record["errors"] == ["a limit"]
    assert record["loans_covered"] is True


def test_full_map_failure_records_the_loans_module(tmp_path, monkeypatch):
    calls = []

    def fake_run(repo, work_dir):
        calls.append(work_dir.name)
        if work_dir.name == "full":
            return None, ["full failed"], 9.0
        graph = work_dir / "graphify-out" / "graph.json"
        graph.parent.mkdir(parents=True)
        graph.write_text(
            json.dumps(
                {
                    "nodes": [
                        {
                            "id": "Loan",
                            "source_file": "loanaccount/Loan.java",
                        }
                    ],
                    "links": [],
                }
            ),
            encoding="utf-8",
        )
        return graph, [], 4.0

    monkeypatch.setattr("app.gate.run_graphify", fake_run)
    monkeypatch.setattr(
        "app.gate.render_largest",
        lambda diagrams, out_dir: ({"name": "loanaccount", "nodes": 1, "rendered": False}, []),
    )
    output = tmp_path / "fineract-gate.json"
    record = run_gate(tmp_path, output, work_root=tmp_path / "work")
    assert calls == ["full", "loans"]
    assert record["scope"] == "loans"
    assert record["node_count"] == 1
    assert record["edge_count"] == 0
    assert record["wall_time_seconds"] == 4.0
    assert any("full failed" in error for error in record["errors"])
    assert json.loads(output.read_text(encoding="utf-8"))["loans_covered"] is True
