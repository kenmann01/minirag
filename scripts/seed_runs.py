#!/usr/bin/env python
"""Seed the demo rig with labeled synthetic runs through the real pipeline.

Every seeded run uses the real graph fixture, the real bridge retrieval
against the ingested corpus (so grounding distances are real), and the real
metrics sink. Only the answering agent and the tier-2 judge are stand-ins,
and every row is stored with origin='seeded' so the board can tell seeded
runs from real model runs. Never present seeded rows as model results.

Usage, after compose is up and the corpus is ingested:

    python scripts/seed_runs.py
"""

import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.harness.agent import RunOutput  # noqa: E402
from app.harness.gate import run_gate  # noqa: E402
from app.harness.score import run_score  # noqa: E402
from app.harness.tasks import generate_tasks  # noqa: E402
from app.storage.pgadapter import PgAdapter  # noqa: E402

GRAPH = ROOT / "eval" / "demo-graph.json"
REPO = ROOT / "eval" / "demo-repo"

# Stand-in accounting: tokens a small local model would roughly spend.
AGENT_TOKENS = {"bare": (700, 40), "map": (1100, 45), "map_rules": (1300, 50)}
JUDGE_TOKENS = {"prompt": 240, "completion": 14}

# Each profile states which tier-1 tasks the stand-in agent gets right with
# the map attached (0-based indices into the four tier-1 tasks) and how many
# tool calls each arm spends per task. Bare never names the callee; tier 2
# only passes when the rule text was attached.
PROFILES = (
    {
        "label": "seed 1 · drift",
        "tier1_with_map": {0, 1},
        "tools_per_task": {"bare": 5, "map": 3, "map_rules": 1},
    },
    {
        "label": "seed 2 · steady",
        "tier1_with_map": {0, 1, 2},
        "tools_per_task": {"bare": 6, "map": 3, "map_rules": 1},
    },
    {
        "label": "seed 3 · strong map",
        "tier1_with_map": {0, 1, 2, 3},
        "tools_per_task": {"bare": 7, "map": 2, "map_rules": 1},
    },
    {
        "label": "seed 4 · regression watch",
        "tier1_with_map": {0},
        "tools_per_task": {"bare": 5, "map": 4, "map_rules": 2},
    },
    {
        "label": "seed 5 · steady again",
        "tier1_with_map": {0, 1, 2},
        "tools_per_task": {"bare": 6, "map": 3, "map_rules": 1},
    },
)


class SeedPhraser:
    """Deterministic task phraser: tier-2 questions from the retrieved facts."""

    def chat(self, prompt: str) -> str:
        """Return the deterministic tier-2 task JSON for the phrasing prompt."""
        import json

        facts = []
        current = None
        for line in prompt.splitlines():
            if line.startswith("Fact "):
                current = {
                    "fact": int(line.removeprefix("Fact ").strip()),
                    "source": "",
                    "cite": "",
                }
                facts.append(current)
            elif current is not None and line.startswith("source: "):
                current["source"] = line.split(": ", 1)[1].strip()
            elif current is not None and line.startswith("cite: "):
                current["cite"] = line.split(": ", 1)[1].strip()
        return json.dumps(
            {
                "tier2": [
                    {
                        "fact": fact["fact"],
                        "prompt": f"What rule does {fact['source']} state in {fact['cite']}?",
                        "chunk_ids": [fact["cite"]],
                    }
                    for fact in facts
                ]
            }
        )


def _task_key(graph: Path, retrieve) -> dict:
    """Map each task's question line to the task, so the stand-in can answer it."""
    tasks, _nodes, _edges = generate_tasks(graph, model=SeedPhraser(), retrieve=retrieve)
    return {task.prompt: task for task in tasks}


def _seed_judge(profile: dict):
    """A stand-in judge: only rule text that was actually shown can support a citation."""

    def judge(prompt: str, answer: str, references: list[dict]) -> dict:
        supported = "the rule is stated in" in answer.lower()
        cited = references[0]["chunk_id"] if (supported and references) else ""
        return {
            "passed": bool(supported and cited),
            "citation": cited,
            "prompt_tokens": JUDGE_TOKENS["prompt"],
            "completion_tokens": JUDGE_TOKENS["completion"],
        }

    return judge


def _seed_task_fn(profile: dict, key: dict):
    """A stand-in agent with the competence the profile describes."""

    def task_fn(prompt: str, context: str) -> RunOutput:
        question = prompt.split("\n\n", 1)[0]
        task = key[question]
        tier1_index = int(task.id.split("-")[1]) - 1
        tools = profile["tools_per_task"][context]
        prompt_tokens, completion_tokens = AGENT_TOKENS[context]
        if task.tier == 1:
            knows = context in {"map", "map_rules"} and tier1_index in profile["tier1_with_map"]
            if knows:
                expected = task.expected
                symbol = str(expected["target"]).rsplit(".", 1)[-1].rsplit(":", 1)[-1]
                file_name = PurePosixPath(str(expected.get("source_file") or "")).name
                answer = f"It calls {symbol} in {file_name}."
            else:
                answer = "unknown"
        else:
            if context == "map_rules":
                chunk_id = task.expected["chunk_ids"][0]
                answer = f"The rule is stated in {chunk_id}."
            else:
                answer = "The map does not carry the rule text."
        return RunOutput(
            answer=answer,
            tool_calls=tools,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    return task_fn


def _backdate_run(adapter: PgAdapter, run_id: str, hours_ago: float) -> None:
    """Spread one seeded run's rows over the board's six-hour window.

    The seeder writes runs seconds apart, which would stack every trend point
    at the right edge of the time axis. Backdating is presentation of seeded
    data only; real runs always keep their true timestamps.
    """
    with adapter.connect() as conn:
        for table in ("runs", "task_results", "tool_calls"):
            conn.execute(
                f"UPDATE {table} SET created_at = now() - interval '{hours_ago} hours' "
                "WHERE run_id::text = %s",
                (run_id,),
            )


def seed_score_runs(adapter: PgAdapter) -> None:
    """Write one seeded three-arm run per profile through the real pipeline."""
    from app.harness import score as score_module
    from app.retrieval.bridge import retrieve

    def bridge(query: str) -> dict:
        return retrieve(query, adapter)

    key = _task_key(GRAPH, bridge)
    original_judge = score_module.judge_rule
    spread_hours = [5.5, 4.0, 2.5, 1.0, 0.0]
    for profile, hours_ago in zip(PROFILES, spread_hours, strict=True):
        score_module.judge_rule = _seed_judge(profile)
        try:
            rows = run_score(
                graph_path=GRAPH,
                repo=REPO,
                output_dir=ROOT / "scratch" / "seed-reports",
                model=SeedPhraser(),
                retrieve=bridge,
                task_fn=_seed_task_fn(profile, key),
                database_adapter=adapter,
                label=profile["label"],
                origin="seeded",
                model_name="seed-agent",
            )
        finally:
            score_module.judge_rule = original_judge
        with adapter.connect() as conn:
            run_id = conn.execute(
                "SELECT run_id::text FROM runs WHERE label = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (profile["label"],),
            ).fetchone()[0]
        _backdate_run(adapter, run_id, hours_ago)
        summary = ", ".join(
            f"{row['context']} {row['tasks_passed']}/{row['tasks_total']}" for row in rows
        )
        print(f"seeded run written: {profile['label']} ({summary}), spread {hours_ago}h back")


def seed_gate_runs(adapter: PgAdapter, count: int = 3) -> None:
    """Append real gate runs on the committed demo tree for history sparklines."""
    from app.harness.metrics import MetricsSink

    sink = MetricsSink(adapter)
    spread_hours = [5.0, 2.5, 0.0]
    for hours_ago in spread_hours[:count]:
        record = run_gate(REPO, ROOT / "scratch" / "seed-gate.json")
        run_id = sink.record_gate(record, label="eval/demo-repo")
        with adapter.connect() as conn:
            conn.execute(
                "UPDATE gate_metrics SET created_at = now() - interval '%s hours' "
                "WHERE run_id::text = %s",
                (hours_ago, str(run_id)),
            )
        print(
            f"gate row written: nodes={record['node_count']} "
            f"edges={record['edge_count']} seconds={record['wall_time_seconds']}"
        )


def main() -> int:
    """Seed the rig: five score runs and three gate runs, all labeled."""
    adapter = PgAdapter()
    seed_score_runs(adapter)
    seed_gate_runs(adapter)
    print("seed_runs: done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
