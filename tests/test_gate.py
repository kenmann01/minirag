"""Gate numbers come from the call-scan graph. A failed scan stays empty."""

import json

from app.gate import run_gate, summarize


def test_summarize_records_the_five_numbers(tmp_path):
    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "Loan",
                        "source_file": "org/apache/fineract/portfolio/loanaccount/Loan.java",
                    }
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


def test_gate_runs_the_call_scan_script(tmp_path, monkeypatch):
    loans = tmp_path / "fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount"
    loans.mkdir(parents=True)
    (loans / "Loan.java").write_text(
        """
        public class Loan {
            public void approve() { disburse(); }
            public void disburse() {}
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.gate.render_largest",
        lambda diagrams, out_dir: ({"name": "loanaccount", "nodes": 1, "rendered": False}, []),
    )
    record = run_gate(tmp_path, tmp_path / "gate.json", work_root=tmp_path / "work")
    assert record["scope"] == "loans"
    assert record["node_count"] >= 2
    assert record["edge_count"] >= 1
    assert record["loans_covered"] is True
    assert record["graph_path"].endswith("graph.json")


def test_scan_failure_records_an_empty_loans_map(tmp_path, monkeypatch):
    calls = []

    def fake_run(repo, work_dir):
        calls.append(work_dir.name)
        return None, ["source tree is missing"], 1.0

    monkeypatch.setattr("app.gate.run_call_scan", fake_run)
    output = tmp_path / "fineract-gate.json"
    record = run_gate(tmp_path, output, work_root=tmp_path / "work")
    assert calls == ["scan"]
    assert record["scope"] == "loans"
    assert record["node_count"] == 0
    assert record["edge_count"] == 0
    assert record["wall_time_seconds"] == 1.0
    assert record["graph_path"] is None
    assert any("missing" in error for error in record["errors"])
    assert json.loads(output.read_text(encoding="utf-8"))["loans_covered"] is False
