# Internal and Confidential - Not for External Distribution.
"""Lock docstring coverage: fail naming any public definition without one.

The walk covers the ``app`` package and the root ``build_submission.py``
script, parses each file with ``ast``, and demands a module docstring
plus a one-line docstring on every public module-level def or class and
every public method, so coverage cannot decay silently.

Issue #39.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGETS = [ROOT / "app", ROOT / "build_submission.py"]


def _python_files():
    for target in TARGETS:
        if target.is_file():
            yield target
        else:
            yield from sorted(
                path
                for path in target.rglob("*.py")
                if "__pycache__" not in path.parts
            )


def _public_nodes(tree):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield node
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        yield child


def _collect_gaps():
    gaps = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        name = path.relative_to(ROOT).as_posix()
        if not ast.get_docstring(tree):
            gaps.append(f"{name}: module docstring missing")
        for node in _public_nodes(tree):
            if node.name.startswith("_"):
                continue
            if not ast.get_docstring(node):
                gaps.append(f"{name}: {node.name} docstring missing")
    return gaps


def test_every_public_definition_carries_a_docstring():
    """Every walked module and public def, class, and method has a docstring."""
    gaps = _collect_gaps()
    assert gaps == [], "Public definitions without docstrings:\n" + "\n".join(gaps)
