"""The score CLI writes its run metrics into Postgres while the run happens."""

import json
import re
from dataclasses import dataclass

import pytest
from pydantic_evals.evaluators import Evaluator

from app.agent import RunOutput
from app.cli import main
from app.ingest import run as run_ingest
from app.pgadapter import PgAdapter
from tests.test_tasks import write_graph

METRICS_TABLES = ("tool_calls", "task_results", "runs")

_CHUNK_LINE = re.compile(r"^- (\S+:[sc]\d+:[sc]\d+):")


@dataclass
class AlwaysPass(Evaluator):
    def evaluate(self, ctx) -> bool:
        return True


class CorpusPhraser:
    """Phrase tier tasks from the real corpus, reading only fact-line chunk ids."""

    def __init__(self):
        self.prompts = []

    def chat(self, prompt: str) -> str:
        self.prompts.append(prompt)
        fact_blocks = re.split(r"^Fact \d+$", prompt, flags=re.MULTILINE)[1:]
        first_chunk_per_fact = [
            next(
                (match.group(1) for line in block.splitlines() if (match := _CHUNK_LINE.match(line))),
                None,
            )
            for block in fact_blocks
        ]
        return json.dumps(
            {
                "tier1": [
                    {"prompt": "Which method does this loan type call?", "target": "made-up"}
                    for _ in range(4)
                ],
                "tier2": [
                    {"prompt": f"What rule is in {chunk_id}?", "chunk_ids": [chunk_id]}
                    for chunk_id in first_chunk_per_fact
                ],
            }
        )


@pytest.fixture(scope="module", autouse=True)
def corpus():
    run_ingest(PgAdapter())


@pytest.fixture(autouse=True)
def fresh_metrics_tables():
    adapter = PgAdapter()
    with adapter.connect() as conn:
        for table in METRICS_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    yield


def score_through_cli(tmp_path, monkeypatch, *extra_args) -> None:
    graph = tmp_path / "graph.json"
    write_graph(graph)

    class LockedAgent:
        """Stand-in so the score run never reaches Ollama."""

    def fake_run_agent(agent, prompt):
        has_map = "flowchart" in prompt
        answer = prompt if has_map else "unknown"
        return RunOutput(
            answer=answer,
            tool_calls=2 if has_map else 3,
            prompt_tokens=100,
            completion_tokens=20,
        )

    monkeypatch.setattr("app.score.prepare_local_judge", lambda settings: AlwaysPass())
    monkeypatch.setattr(
        "app.score.judge_rule",
        lambda prompt, answer, allowed: {
            "pass": True,
            "citation": allowed[0],
            "prompt_tokens": 10,
            "completion_tokens": 5,
        },
    )
    monkeypatch.setattr("app.score.build_agent", lambda repo, settings, on_tool=None: LockedAgent())
    monkeypatch.setattr("app.score.run_agent", fake_run_agent)
    exit_code = main(
        [
            "score",
            "--repo",
            str(tmp_path),
            "--graph",
            str(graph),
            "--output",
            str(tmp_path / "reports"),
            *extra_args,
        ],
        database_adapter=PgAdapter(),
        language_model=CorpusPhraser(),
    )
    assert exit_code == 0


def load_reports(tmp_path):
    payloads = {}
    for context in ("bare", "map", "map_rules"):
        payload = json.loads((tmp_path / "reports" / f"{context}.json").read_text(encoding="utf-8"))
        payloads[context] = payload
    return payloads


def test_score_cli_writes_one_runs_row_per_context_arm(tmp_path, monkeypatch):
    score_through_cli(tmp_path, monkeypatch)
    adapter = PgAdapter()
    with adapter.connect() as conn:
        rows = conn.execute(
            """
            SELECT run_id, context, tasks_total, tasks_passed,
                   passed_tier1, passed_tier2, tool_calls, cost_usd
            FROM runs
            ORDER BY context
            """
        ).fetchall()
    assert [row[1] for row in rows] == ["bare", "map", "map_rules"]
    assert len({row[0] for row in rows}) == 1
    boards = load_reports(tmp_path)
    stored = {row[1]: row for row in rows}
    for context in boards:
        board = boards[context]["scoreboard"]
        row = stored[context]
        assert row[2] == board["tasks_total"]
        assert row[3] == board["tasks_passed"]
        assert row[4] == board["passed_by_tier"]["1"]["passed"]
        assert row[5] == board["passed_by_tier"]["2"]["passed"]
        assert row[6] == board["tool_calls"]
        assert row[7] == board["cost_usd"]
    assert stored["bare"][3] < stored["map"][3]
    assert stored["map"][3] == stored["map_rules"][3] == 8
    assert stored["map"][7] > 0


def test_score_cli_writes_a_task_results_row_per_case(tmp_path, monkeypatch):
    score_through_cli(tmp_path, monkeypatch)
    adapter = PgAdapter()
    with adapter.connect() as conn:
        rows = conn.execute(
            """
            SELECT run_id, context, task_id, tier, passed, origin, created_at
            FROM task_results
            ORDER BY context, task_id
            """
        ).fetchall()
        run_ids = {row[0] for row in conn.execute("SELECT run_id FROM runs").fetchall()}
    assert len(rows) == 24
    assert {row[0] for row in rows} == run_ids
    assert {row[1] for row in rows} == {"bare", "map", "map_rules"}
    by_context = {}
    for row in rows:
        by_context.setdefault(row[1], []).append(row)
    expected_ids = {f"t1-0{n}" for n in range(1, 5)} | {f"t2-0{n}" for n in range(1, 5)}
    for context, context_rows in by_context.items():
        assert {row[2] for row in context_rows} == expected_ids
        assert {row[3] for row in context_rows} == {1, 2}
        assert all(row[6] is not None for row in context_rows)
    bare = {row[2]: row[4] for row in by_context["bare"]}
    assert sum(bare.values()) == 4
    assert not any(bare[f"t1-0{n}"] for n in range(1, 5))
    assert all(bare[f"t2-0{n}"] for n in range(1, 5))
    tier1_map = [row for row in by_context["map"] if row[3] == 1]
    assert all(row[4] for row in tier1_map)
    tier2_origins = [row[5] for row in by_context["map"] if row[3] == 2]
    assert all(origin["chunk_ids"] for origin in tier2_origins)
    tier1_origins = [row[5] for row in tier1_map]
    assert all(origin["node_ids"] for origin in tier1_origins)
    boards = load_reports(tmp_path)
    for context, board in boards.items():
        stored_passes = sum(1 for row in by_context[context] if row[4])
        assert stored_passes == board["scoreboard"]["tasks_passed"]


def test_score_cli_stores_grounding_distance_per_tier(tmp_path, monkeypatch):
    score_through_cli(tmp_path, monkeypatch)
    adapter = PgAdapter()
    with adapter.connect() as conn:
        rows = conn.execute(
            """
            SELECT task_id, tier, grounding_distance, origin
            FROM task_results
            ORDER BY context, task_id
            """
        ).fetchall()
    tier1 = [row for row in rows if row[1] == 1]
    tier2 = [row for row in rows if row[1] == 2]
    assert tier1 and tier2
    assert all(row[2] is None for row in tier1)
    for task_id, _tier, distance, origin in tier2:
        cited = origin["chunks"]
        expected = sum(chunk["distance"] for chunk in cited) / len(cited)
        assert isinstance(distance, float)
        assert distance == pytest.approx(expected)


def test_score_cli_reconciles_tool_call_rows_with_the_scoreboard(tmp_path, monkeypatch):
    score_through_cli(tmp_path, monkeypatch)
    adapter = PgAdapter()
    with adapter.connect() as conn:
        rows = conn.execute(
            "SELECT context, task_id, tool_calls FROM tool_calls ORDER BY context, task_id"
        ).fetchall()
        run_totals = dict(conn.execute("SELECT context, tool_calls FROM runs").fetchall())
    assert len(rows) == 24
    payloads = load_reports(tmp_path)
    summed = {}
    stored_by_task = {}
    for context, task_id, calls in rows:
        summed[context] = summed.get(context, 0) + calls
        stored_by_task.setdefault(context, {})[task_id] = calls
    for context, payload in payloads.items():
        assert summed[context] == payload["scoreboard"]["tool_calls"]
        assert run_totals[context] == payload["scoreboard"]["tool_calls"]
        report_calls = {case["name"]: case["output"]["tool_calls"] for case in payload["cases"]}
        assert stored_by_task[context] == report_calls


def test_score_cli_no_metrics_writes_no_tables(tmp_path, monkeypatch):
    score_through_cli(tmp_path, monkeypatch, "--no-metrics")
    adapter = PgAdapter()
    with adapter.connect() as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        }
    assert not {"runs", "task_results", "tool_calls"} & names
    for context in ("bare", "map", "map_rules"):
        assert (tmp_path / "reports" / f"{context}.json").exists()


def test_score_cli_self_heals_a_partial_runs_table(tmp_path, monkeypatch):
    adapter = PgAdapter()
    with adapter.connect() as conn:
        conn.execute(
            """
            CREATE TABLE runs (
                run_id UUID NOT NULL,
                context TEXT NOT NULL,
                PRIMARY KEY (run_id, context)
            )
            """
        )
    score_through_cli(tmp_path, monkeypatch)
    with adapter.connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'runs'"
            ).fetchall()
        }
        rows = conn.execute(
            "SELECT run_id, context, tasks_total, tasks_passed FROM runs ORDER BY context"
        ).fetchall()
    assert {
        "created_at",
        "tasks_total",
        "tasks_passed",
        "passed_tier1",
        "passed_tier2",
        "tool_calls",
        "cost_usd",
    } <= columns
    assert [row[1] for row in rows] == ["bare", "map", "map_rules"]
    assert all(row[2] == 8 and row[3] >= 0 for row in rows)
