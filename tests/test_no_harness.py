"""Guard the split: the RAG repository carries no evaluation harness.

Issue #16 moved scoring, the generated dashboard, graph materialisation
provisioning and the repository scanner out of this repository. These tests
fail if any of them creeps back in: no runtime module names the harness, the
CLI offers no removed command, and the candidate seam imports nothing from the
deleted package.
"""

import ast
from pathlib import Path

import pytest

from app.cli import main

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
REMOVED_COMMANDS = ("score", "gate", "live", "compare")
KEPT_COMMANDS = "{ingest,eval,retrieve,candidate,ossie}"


def _app_sources() -> list[Path]:
    return sorted(
        path for path in APP.rglob("*.py") if "__pycache__" not in path.parts
    )


def test_no_application_module_names_the_harness():
    hits = []
    for path in _app_sources():
        text = path.read_text(encoding="utf-8")
        for needle in ("app.harness", "evalharness"):
            if needle in text:
                hits.append(f"{path.relative_to(ROOT).as_posix()}: {needle}")
    assert hits == [], "the harness must not be referenced from app/"


@pytest.mark.parametrize("command", REMOVED_COMMANDS)
def test_the_cli_rejects_every_removed_command(command, capsys):
    with pytest.raises(SystemExit) as raised:
        main([command])
    assert raised.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_the_cli_help_lists_only_the_kept_commands(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert KEPT_COMMANDS in capsys.readouterr().out


def test_the_candidate_runtime_module_imports_no_harness_module():
    source = (APP / "candidate.py").read_text(encoding="utf-8")
    modules = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    assert not any(
        name == "app.harness" or name.startswith("app.harness.") for name in modules
    )
