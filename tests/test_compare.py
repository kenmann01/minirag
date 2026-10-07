"""The comparison page reads the three report files and shows every run."""

import json

from app.compare import load_summaries, render_page


def _row(run_id: str, context: str, passed: int, tools: int, cost: float) -> dict:
    return {
        "run_id": run_id,
        "context": context,
        "tasks_passed": passed,
        "tasks_total": 8,
        "passed_by_tier": {
            "1": {"passed": min(passed, 4), "total": 4},
            "2": {"passed": max(passed - 4, 0), "total": 4},
        },
        "tool_calls": tools,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cost_usd": cost,
    }


def test_page_shows_all_three_runs(tmp_path):
    rows = [
        _row("RUN 1", "bare", 2, 64, 0.42),
        _row("RUN 2", "map", 5, 31, 0.19),
        _row("RUN 3", "map_rules", 8, 18, 0.11),
    ]
    for name, row in zip(("bare", "map", "map_rules"), rows, strict=True):
        (tmp_path / f"{name}.json").write_text(
            json.dumps({"name": name, "scoreboard": row}),
            encoding="utf-8",
        )
    loaded = load_summaries(tmp_path)
    page = render_page(loaded)
    assert "RUN 1" in page and "RUN 2" in page and "RUN 3" in page
    assert "bare agent" in page or "bare" in page
    assert "64" in page and "0.11" in page
    assert "https://" not in page
