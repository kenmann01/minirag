"""Run the two score paths over the committed parity fixture."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.harness.score import Prices, run_score
from tests.parity_fixture import (
    ANSWERS,
    EXPECTED,
    GRAPH,
    TASKS,
    FixtureModel,
    fixture_judge,
    fixture_retrieve,
    legacy_task_fn,
    make_candidate_task_fn,
)


def fixture_prices() -> Prices:
    """Read the fixture's per-model price row, the way a spec would."""
    row = EXPECTED["prices"][ANSWERS["model"]]
    return Prices(
        input_usd_per_million=row["input_usd_per_million"],
        output_usd_per_million=row["output_usd_per_million"],
        tool_usd=row["tool_usd"],
    )


def parity_rows(tmp_path: Path) -> dict[str, Any]:
    """Run the current path and the candidate path over the same task list."""
    import app.harness.score as score

    original = score.judge_rule
    score.judge_rule = fixture_judge
    try:
        legacy = run_score(
            graph_path=GRAPH,
            repo=tmp_path,
            output_dir=tmp_path / "legacy",
            model=FixtureModel(),
            retrieve=fixture_retrieve,
            task_fn=legacy_task_fn,
            tasks=TASKS,
            prices=fixture_prices(),
        )
        candidate = run_score(
            graph_path=GRAPH,
            repo=tmp_path,
            output_dir=tmp_path / "candidate",
            model=FixtureModel(),
            retrieve=fixture_retrieve,
            task_fn=make_candidate_task_fn(),
            tasks=TASKS,
            prices=fixture_prices(),
        )
    finally:
        score.judge_rule = original
    return {"legacy": legacy, "candidate": candidate, "expected": EXPECTED}
