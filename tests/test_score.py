"""Three pydantic-evals runs share one task list, cost honestly, and hide answers."""

from pathlib import Path

from app.harness.agent import RunOutput
from app.harness.score import Prices, run_score
from app.harness.tasks import generate_tasks, map_excerpt
from tests.test_tasks import Phraser, retrieve_factory, write_graph


def _answer_key(graph: Path) -> dict:
    """Build task.prompt -> task so the stand-in agent can answer correctly."""
    tasks, _nodes, _edges = generate_tasks(
        graph, model=Phraser(), retrieve=retrieve_factory("bank")
    )
    return {task.prompt: task for task in tasks}


def _task_fn(key: dict):
    """A stand-in agent: efficient and right with the map, wrong and noisy bare."""

    def task_fn(prompt: str, context: str) -> RunOutput:
        question = prompt.split("\n\n", 1)[0]
        task = key[question]
        calls = {"bare": 6, "map": 3, "map_rules": 1}[context]
        if context == "bare":
            answer = "unknown"
        elif task.tier == 1:
            # A competent agent that used the map scaffold and its tools.
            expected = task.expected
            answer = f"It calls {expected['target']} in {Path(expected['source_file']).name}."
        else:
            chunk_id = task.expected["chunk_ids"][0]
            answer = f"The rule is stated in {chunk_id}."
        return RunOutput(
            answer=answer,
            tool_calls=calls,
            prompt_tokens=100,
            completion_tokens=20,
        )

    return task_fn


def _judge(prompt: str, answer: str, references: list[dict]) -> dict:
    return {
        "passed": True,
        "citation": references[0]["chunk_id"],
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }


def test_three_runs_share_the_list_and_record_tools_and_cost(tmp_path, monkeypatch):
    graph = tmp_path / "graph.json"
    write_graph(graph)
    key = _answer_key(graph)
    prompts = []
    monkeypatch.setattr("app.harness.score.judge_rule", _judge)

    def task_fn(prompt: str, context: str) -> RunOutput:
        prompts.append((context, prompt))
        return _task_fn(key)(prompt, context)

    rows = run_score(
        graph_path=graph,
        repo=tmp_path,
        output_dir=tmp_path / "runs",
        model=Phraser(),
        retrieve=retrieve_factory("bank"),
        task_fn=task_fn,
        prices=Prices(0.15, 0.60, 0.001),
    )

    assert [row["context"] for row in rows] == ["bare", "map", "map_rules"]
    assert rows[0]["tasks_passed"] < rows[1]["tasks_passed"]
    assert rows[2]["tasks_passed"] == 8
    assert rows[0]["tool_calls"] > rows[1]["tool_calls"] > rows[2]["tool_calls"]
    assert rows[0]["passed_by_tier"]["1"]["passed"] == 0
    assert rows[1]["passed_by_tier"]["1"]["passed"] == 4
    assert rows[2]["passed_by_tier"]["2"]["passed"] == 4
    assert rows[0]["cost_usd"] > 0
    bare_prompts = [prompt for context, prompt in prompts if context == "bare"]
    map_prompts = [prompt for context, prompt in prompts if context == "map"]
    rule_prompts = [prompt for context, prompt in prompts if context == "map_rules"]
    assert "flowchart LR" not in bare_prompts[0]
    assert "flowchart LR" in map_prompts[0]
    assert any("bank-1" in prompt for prompt in rule_prompts)
    saved = (tmp_path / "runs" / "bare.json").read_text(encoding="utf-8")
    assert "GraphFact" in saved or "graphfact" in saved.lower()


def test_the_map_excerpt_carries_no_answer_key(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)
    tasks, nodes, edges = generate_tasks(graph, model=Phraser(), retrieve=retrieve_factory("bank"))
    for task in tasks:
        excerpt = map_excerpt(task, nodes, edges)
        # No labeled answer line: the graded target must not be printed as a
        # key. The module diagram may still show the callee as one node among
        # the module's members, which is the map doing its job.
        assert not any(line.startswith("target") for line in excerpt.splitlines())
        assert "source:" in excerpt
        assert "relation:" in excerpt
        assert "file:" in excerpt
        assert "flowchart" in excerpt


def test_the_judge_sees_the_reference_chunk_text(tmp_path, monkeypatch):
    seen = {}

    def judge(prompt: str, answer: str, references: list[dict]) -> dict:
        seen["references"] = references
        return _judge(prompt, answer, references)

    monkeypatch.setattr("app.harness.score.judge_rule", judge)

    graph = tmp_path / "graph.json"
    write_graph(graph)
    key = _answer_key(graph)
    run_score(
        graph_path=graph,
        repo=tmp_path,
        output_dir=tmp_path / "runs",
        model=Phraser(),
        retrieve=retrieve_factory("bank"),
        task_fn=_task_fn(key),
        prices=Prices(0.15, 0.60, 0.001),
    )
    assert seen.get("references"), "the tier-2 judge was never called"
    assert all(
        reference.get("chunk_id") and reference.get("text") for reference in seen["references"]
    )


def test_judge_rule_sends_question_answer_and_chunk_text(monkeypatch):
    captured = {}

    class FakeResult:
        output = type("Verdict", (), {"passed": True, "citation": "reg-z-1026:s1:c01"})()
        usage = type("Usage", (), {"input_tokens": 10, "output_tokens": 5})()

    def fake_run_sync(self, body):
        captured["body"] = body
        return FakeResult()

    monkeypatch.setattr("pydantic_ai.Agent.run_sync", fake_run_sync)
    from app.harness.score import judge_rule

    verdict = judge_rule(
        "What rule does Loan state?",
        "The rule is about broker fees.",
        [{"chunk_id": "reg-z-1026:s1:c01", "text": "Mortgage broker fees are finance charges."}],
    )
    assert verdict == {
        "passed": True,
        "citation": "reg-z-1026:s1:c01",
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }
    body = captured["body"]
    assert "What rule does Loan state?" in body
    assert "The rule is about broker fees." in body
    assert "Reference chunks" in body
    assert "reg-z-1026:s1:c01" in body
    assert "Mortgage broker fees are finance charges." in body


def test_judge_tokens_count_toward_the_run_cost(tmp_path, monkeypatch):
    def judge(prompt: str, answer: str, references: list[dict]) -> dict:
        return {
            "passed": True,
            "citation": references[0]["chunk_id"],
            "prompt_tokens": 1000,
            "completion_tokens": 500,
        }

    monkeypatch.setattr("app.harness.score.judge_rule", judge)

    graph = tmp_path / "graph.json"
    write_graph(graph)
    key = _answer_key(graph)
    rows = run_score(
        graph_path=graph,
        repo=tmp_path,
        output_dir=tmp_path / "runs",
        model=Phraser(),
        retrieve=retrieve_factory("bank"),
        task_fn=_task_fn(key),
        prices=Prices(0.15, 0.60, 0.001),
    )
    # Four tier-2 cases at (1000 prompt, 500 completion) judge tokens each.
    for row in rows:
        assert row["prompt_tokens"] >= 4 * 1000
        assert row["completion_tokens"] >= 4 * 500
        assert row["cost_usd"] > (4 * 1000 * 0.15 + 4 * 500 * 0.60) / 1_000_000
