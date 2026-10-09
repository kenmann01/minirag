"""Gate numbers come from the call-scan graph. A failed scan stays empty."""

import json
from pathlib import Path
from types import SimpleNamespace

from app.harness.gate import render_largest, run_call_scan, run_gate, summarize


def _loans_repo(tmp_path: Path) -> Path:
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
    return tmp_path


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
        "app.harness.gate.render_largest",
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

    monkeypatch.setattr("app.harness.gate.run_call_scan", fake_run)
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


def test_call_scan_needs_a_source_tree(tmp_path):
    graph_path, errors, elapsed = run_call_scan(tmp_path, tmp_path / "work")
    assert graph_path is None
    assert errors == ["source tree is missing"]
    assert elapsed == 0.0


def test_call_scan_reports_an_os_error(tmp_path, monkeypatch):
    repo = _loans_repo(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("no interpreter")

    monkeypatch.setattr("app.harness.gate.subprocess.run", boom)
    graph_path, errors, _ = run_call_scan(repo, tmp_path / "work")
    assert graph_path is None
    assert errors == ["no interpreter"]


def test_call_scan_reports_a_failed_scan_and_a_missing_graph(tmp_path, monkeypatch):
    repo = _loans_repo(tmp_path)
    monkeypatch.setattr(
        "app.harness.gate.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=3, stderr="scan exploded", stdout=""),
    )
    graph_path, errors, _ = run_call_scan(repo, tmp_path / "work")
    assert graph_path is None
    assert errors[0] == "scan exploded"
    assert errors[1].startswith("graph.json was not written under")


def test_render_largest_without_diagrams_stays_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("app.harness.gate.shutil.which", lambda name: "mmdc")
    record, errors = render_largest([], tmp_path / "mermaid")
    assert record == {"name": None, "nodes": 0, "rendered": False}
    assert errors == []


def test_render_largest_records_a_missing_renderer(tmp_path, monkeypatch):
    monkeypatch.setattr("app.harness.gate.shutil.which", lambda name: None)
    record, errors = render_largest([("loanaccount", "flowchart LR\n  a", 2, 2)], tmp_path / "m")
    assert record == {
        "name": "loanaccount",
        "nodes": 2,
        "module_nodes": 2,
        "rendered": False,
    }
    assert (tmp_path / "m" / "loanaccount.mmd").read_text(encoding="utf-8").startswith(
        "flowchart LR"
    )
    assert errors == ["mermaid renderer mmdc is not installed"]


def test_render_largest_renders_the_first_diagram_that_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr("app.harness.gate.shutil.which", lambda name: "mmdc")

    def fake_run(cmd, **kwargs):
        svg = Path(cmd[cmd.index("-o") + 1])
        svg.write_text("<svg/>", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("app.harness.gate.subprocess.run", fake_run)
    record, errors = render_largest([("a", "flowchart LR", 1, 1)], tmp_path / "m")
    assert errors == []
    assert record["rendered"] is True
    assert record["name"] == "a"
    assert Path(record["path"]).is_file()


def test_render_largest_falls_back_when_the_renderer_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("app.harness.gate.shutil.which", lambda name: "mmdc")
    replies = [SimpleNamespace(returncode=1, stderr="boom"), SimpleNamespace(returncode=1, stderr="")]

    def fake_run(cmd, **kwargs):
        return replies.pop(0)

    monkeypatch.setattr("app.harness.gate.subprocess.run", fake_run)
    record, errors = render_largest([("a", "flowchart LR", 1, 1), ("b", "flowchart LR", 2, 2)], tmp_path / "m")
    assert errors == ["boom", "b did not render"]
    assert record == {"name": "a", "nodes": 1, "module_nodes": 1, "rendered": False}


def test_a_directory_already_named_loanaccount_is_the_scan_scope(tmp_path):
    from app.harness.adapter import source_tree

    loans = tmp_path / "loanaccount"
    loans.mkdir()
    assert source_tree(loans) == (loans, "loans")
