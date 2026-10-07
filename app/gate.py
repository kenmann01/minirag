# Internal and Confidential - Not for External Distribution.
"""Run Graphify on a repository and record the five scale numbers."""

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.graph import loans_covered, load_graph, module_key, module_mermaid

LOANS_RELATIVE = Path(
    "fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount"
)


def graphify_bin() -> str:
    """Return the Graphify executable next to this interpreter, else on PATH.

    The venv Python is often a symlink. Resolving it first would leave the
    venv ``bin`` directory and miss the installed console script.
    """
    executable = Path(sys.executable)
    for candidate in (executable.parent / "graphify", executable.resolve().parent / "graphify"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("graphify") or "graphify"


def run_graphify(repo: Path, work_dir: Path) -> tuple[Path | None, list[str], float]:
    """Run Graphify in ``work_dir``. Return the graph path, errors, and seconds."""
    work_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [
                graphify_bin(),
                "extract",
                str(repo),
                "--code-only",
                "--no-cluster",
                "--allow-partial",
                "--out",
                str(work_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return None, [str(exc)], time.perf_counter() - started
    elapsed = time.perf_counter() - started
    errors = []
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "graphify failed").strip()
        errors.append(detail[-4000:])
    graph_path = work_dir / "graphify-out" / "graph.json"
    if not graph_path.is_file():
        alt = repo / "graphify-out" / "graph.json"
        graph_path = alt if alt.is_file() else graph_path
    if not graph_path.is_file():
        errors.append(f"graph.json was not written under {work_dir}")
        return None, errors, elapsed
    return graph_path, errors, elapsed


def module_diagrams(graph_path: Path) -> list[tuple[str, str, int]]:
    """Build one mermaid diagram per module. The third value is its node count."""
    nodes, edges = load_graph(graph_path)
    grouped: dict[str, list[dict]] = {}
    for node in nodes:
        key = module_key(str(node.get("source_file") or node.get("file") or ""))
        grouped.setdefault(key, []).append(node)
    diagrams = []
    for key, members in grouped.items():
        source_file = str(members[0].get("source_file") or members[0].get("file") or key)
        diagram = module_mermaid(nodes, edges, source_file)
        raw = len(members)
        drawn = min(raw, 24)
        diagrams.append((key, diagram, drawn, raw))
    diagrams.sort(key=lambda item: (item[3], item[2]), reverse=True)
    return diagrams


_RENDER_CEILING = 200


def render_largest(diagrams: list[tuple[str, str, int]], out_dir: Path) -> tuple[dict, list[str]]:
    """Write diagrams and keep the largest one ``mmdc`` can render.

    Diagrams past a few hundred nodes are not sent to the renderer. When the
    renderer is missing, the largest diagram under that ceiling is recorded
    as not rendered.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    oversized = [item for item in diagrams if item[2] > _RENDER_CEILING]
    if oversized:
        errors.append(
            f"{len(oversized)} module diagrams exceed {_RENDER_CEILING} nodes and were not rendered"
        )
    candidates = [item for item in diagrams if item[2] <= _RENDER_CEILING]
    errors.append("module mermaid diagrams are capped at 24 nodes")
    renderer = shutil.which("mmdc")
    if renderer is None:
        errors.append("mermaid renderer mmdc is not installed")
    for name, source, count, *_rest in candidates:
        path = out_dir / f"{name}.mmd"
        path.write_text(source + "\n", encoding="utf-8")
        if renderer is None:
            continue
        svg = out_dir / f"{name}.svg"
        puppeteer = out_dir / "puppeteer.json"
        puppeteer.write_text(
            json.dumps({"args": ["--no-sandbox", "--disable-setuid-sandbox"]}),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [renderer, "-i", str(path), "-o", str(svg), "-p", str(puppeteer)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0 and svg.is_file():
            module_nodes = _rest[0] if _rest else count
            return {
                "name": name,
                "nodes": count,
                "module_nodes": module_nodes,
                "rendered": True,
                "path": str(svg),
            }, errors
        errors.append((completed.stderr or f"{name} did not render").strip())
    pool = candidates or diagrams
    if pool:
        name, _source, count, *_rest = pool[0]
        return {
            "name": name,
            "nodes": count,
            "module_nodes": _rest[0] if _rest else count,
            "rendered": False,
        }, errors
    return {"name": None, "nodes": 0, "rendered": False}, errors


def summarize(graph_path: Path, elapsed: float, errors: list[str], mermaid: dict, scope: str) -> dict:
    """The five gate numbers, plus whether the loans package is in the graph."""
    nodes, edges = load_graph(graph_path)
    return {
        "scope": scope,
        "wall_time_seconds": round(elapsed, 3),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "largest_mermaid": mermaid,
        "errors": errors,
        "loans_covered": loans_covered(nodes),
        "graph_path": str(graph_path),
    }


def run_gate(repo: Path, output: Path, work_root: Path | None = None) -> dict:
    """Map the repo. On failure, map the loans module and record that scope."""
    root = work_root or Path(tempfile.gettempdir()) / "minirag-gate"
    graph_path, errors, elapsed = run_graphify(repo, root / "full")
    scope = "full"
    covered = False
    if graph_path is not None:
        nodes, _edges = load_graph(graph_path)
        covered = loans_covered(nodes)
    if graph_path is None or not covered:
        if graph_path is None:
            errors.append("full repository map failed; mapping the loans module")
        else:
            errors.append("full repository map did not cover loanaccount; mapping the loans module")
        loans = repo / LOANS_RELATIVE
        if not loans.is_dir():
            loans = repo
        graph_path, loans_errors, elapsed = run_graphify(loans, root / "loans")
        errors.extend(loans_errors)
        scope = "loans"
    if graph_path is None:
        record = {
            "scope": scope,
            "wall_time_seconds": round(elapsed, 3),
            "node_count": 0,
            "edge_count": 0,
            "largest_mermaid": {"name": None, "nodes": 0, "rendered": False},
            "errors": errors,
            "loans_covered": False,
            "graph_path": None,
        }
    else:
        mermaid, render_errors = render_largest(module_diagrams(graph_path), root / "mermaid")
        errors.extend(render_errors)
        record = summarize(graph_path, elapsed, errors, mermaid, scope)
        if scope == "loans" and record["node_count"] > 0:
            record["loans_covered"] = True
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record
