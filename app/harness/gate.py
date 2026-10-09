# Internal and Confidential - Not for External Distribution.
"""Run the call-scan skill on a repository and record the five scale numbers."""

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.harness.adapter import source_tree
from app.harness.graph import load_graph, loans_covered, module_key, module_mermaid

SKILL_SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "call-scan" / "scripts" / "scan.py"


def run_call_scan(repo: Path, work_dir: Path) -> tuple[Path | None, list[str], float]:
    """Run the call-scan skill on the adapter's tree. Return path, errors, and seconds."""
    located = source_tree(repo)
    if located is None:
        return None, ["source tree is missing"], 0.0
    tree, _scope = located
    work_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(SKILL_SCRIPT),
                str(tree),
                "--base",
                str(repo),
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
        detail = (completed.stderr or completed.stdout or "call scan failed").strip()
        errors.append(detail[-4000:])
    graph_path = work_dir / "graph.json"
    if not graph_path.is_file():
        errors.append(f"graph.json was not written under {work_dir}")
        return None, errors, elapsed
    return graph_path, errors, elapsed


def module_diagrams(graph_path: Path) -> list[tuple[str, str, int, int]]:
    """Build one mermaid diagram per module as (module, diagram, drawn, raw) tuples.

    ``drawn`` is the node count inside the diagram (capped for legibility);
    ``raw`` is the module's full node count.
    """
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


def render_largest(
    diagrams: list[tuple[str, str, int, int]], out_dir: Path
) -> tuple[dict, list[str]]:
    """Write diagrams and keep the largest one ``mmdc`` can render.

    Each diagram is capped at 24 drawn nodes for legibility; the raw module
    size is recorded alongside. When the renderer is missing, the largest
    diagram is recorded as not rendered.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    renderer = shutil.which("mmdc")
    if renderer is None:
        errors.append("mermaid renderer mmdc is not installed")
    for name, source, count, raw in diagrams:
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
            return {
                "name": name,
                "nodes": count,
                "module_nodes": raw,
                "rendered": True,
                "path": str(svg),
            }, errors
        errors.append((completed.stderr or f"{name} did not render").strip())
    if diagrams:
        name, _source, count, raw = diagrams[0]
        return {
            "name": name,
            "nodes": count,
            "module_nodes": raw,
            "rendered": False,
        }, errors
    return {"name": None, "nodes": 0, "rendered": False}, errors


def summarize(
    graph_path: Path, elapsed: float, errors: list[str], mermaid: dict, scope: str
) -> dict:
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
    """Map the adapter's tree with the call-scan skill and record that scope."""
    root = work_root or Path(tempfile.gettempdir()) / "minirag-gate"
    located = source_tree(repo)
    scope = located[1] if located else "loans"
    graph_path, errors, elapsed = run_call_scan(repo, root / "scan")
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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record
