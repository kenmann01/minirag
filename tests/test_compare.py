"""The comparison page reads the three report files and shows every run."""

import json
from http.client import HTTPConnection
from threading import Thread

from app.web.compare import load_summaries, render_page, serve_compare


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


def _write_reports(report_dir, rows):
    for name, row in zip(("bare", "map", "map_rules"), rows, strict=True):
        (report_dir / f"{name}.json").write_text(
            json.dumps({"name": name, "scoreboard": row}),
            encoding="utf-8",
        )


def test_page_shows_all_three_runs(tmp_path):
    rows = [
        _row("RUN 1", "bare", 2, 64, 0.42),
        _row("RUN 2", "map", 5, 31, 0.19),
        _row("RUN 3", "map_rules", 8, 18, 0.11),
    ]
    _write_reports(tmp_path, rows)
    loaded = load_summaries(tmp_path)
    page = render_page(loaded)
    assert "RUN 1" in page and "RUN 2" in page and "RUN 3" in page
    assert "bare agent" in page or "bare" in page
    assert "64" in page and "0.11" in page
    assert "https://" not in page


def test_the_comparison_server_serves_the_page_and_rejects_other_paths(tmp_path):
    _write_reports(tmp_path, [_row("RUN 1", "bare", 2, 64, 0.42)] * 3)
    Thread(
        target=serve_compare,
        kwargs={"report_dir": tmp_path, "host": "127.0.0.1", "port": 8770},
        daemon=True,
    ).start()
    page, status, _ = _request(8770, "/", method="GET")
    assert b"RUN 1" in page
    _, missing_status, _ = _request(8770, "/elsewhere", method="GET")
    assert missing_status == 404


def _request(port: int, path: str, method: str = "GET", body: bytes | None = None):
    for _ in range(20):
        try:
            connection = HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request(method, path, body=body)
            response = connection.getresponse()
            return response.read(), response.status, response
        except OSError:
            continue
    raise AssertionError("comparison server did not answer")
