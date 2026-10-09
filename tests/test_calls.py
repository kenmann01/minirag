"""The call-scan skill keeps unambiguous calls and drops the rest."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "call-scan" / "scripts" / "scan.py"
LOANS = Path("fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount")


def _write(repo: Path, name: str, source: str) -> None:
    path = repo / LOANS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _fixture(repo: Path) -> None:
    _write(
        repo,
        "Loan.java",
        """
        public class Loan {
            public void approve() {
                disburse();
                Schedule.recalc();
                // hidden();
                String note = "hidden()";
                ambiguous();
            }
            public void disburse() {}
        }
        """,
    )
    _write(
        repo,
        "Schedule.java",
        """
        public class Schedule {
            public void recalc() {}
            public void ambiguous() {}
        }
        """,
    )
    _write(
        repo,
        "Other.java",
        """
        public class Other {
            public void ambiguous() {}
        }
        """,
    )


def _scan(tree: Path, out: Path, base: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), str(tree), "--out", str(out)]
    if base is not None:
        command.extend(["--base", str(base)])
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_unique_and_qualified_calls_are_kept(tmp_path):
    _fixture(tmp_path)
    out = tmp_path / "out"
    completed = _scan(tmp_path / LOANS, out, base=tmp_path)
    assert completed.returncode == 0, completed.stderr
    graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    edges = {(link["source"], link["target"]) for link in graph["links"]}
    assert ("Loan.approve", "Loan.disburse") in edges
    assert ("Loan.approve", "Schedule.recalc") in edges
    assert all(link["relation"] == "calls" for link in graph["links"])
    assert all(link["confidence"] == "EXTRACTED" for link in graph["links"])
    assert all("ambiguous" not in target for _source, target in edges)
    assert all("hidden" not in target for _source, target in edges)
    ids = {node["id"] for node in graph["nodes"]}
    assert "Loan.approve" in ids
    assert all("loanaccount" in node["source_file"] for node in graph["nodes"])


def test_missing_source_tree_writes_nothing(tmp_path):
    out = tmp_path / "out"
    completed = _scan(tmp_path / "empty", out)
    assert completed.returncode == 1
    assert not (out / "graph.json").exists()
