"""Three pydantic-evals runs share one task list and record cost."""

from dataclasses import dataclass

from pydantic_evals.evaluators import Evaluator

from app.agent import RunOutput
from app.config import Settings
from app.score import JUDGE_RUBRIC, Prices, prepare_local_judge, run_score
from tests.test_tasks import Phraser, retrieve_factory, write_graph


@dataclass
class AlwaysPass(Evaluator):
    def evaluate(self, ctx) -> bool:
        return True


def test_three_runs_share_the_list_and_record_tools_and_cost(tmp_path, monkeypatch):
    graph = tmp_path / "graph.json"
    write_graph(graph)
    prompts = []

    def task_fn(prompt: str, context: str) -> RunOutput:
        prompts.append((context, prompt))
        calls = {"bare": 6, "map": 3, "map_rules": 1}[context]
        answer = "unknown" if context == "bare" else prompt
        return RunOutput(
            answer=answer,
            tool_calls=calls,
            prompt_tokens=100,
            completion_tokens=20,
        )

    def judge(prompt: str, answer: str, allowed: list[str]) -> dict:
        return {
            "pass": True,
            "citation": allowed[0],
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }

    monkeypatch.setattr("app.score.judge_rule", judge)
    rows = run_score(
        graph_path=graph,
        repo=tmp_path,
        output_dir=tmp_path / "runs",
        model=Phraser(),
        retrieve=retrieve_factory("bank"),
        task_fn=task_fn,
        tier2_judge=AlwaysPass(),
        prices=Prices(0.15, 0.60, 0.001),
    )
    assert [row["context"] for row in rows] == ["bare", "map", "map_rules"]
    assert rows[0]["tasks_passed"] < rows[1]["tasks_passed"]
    assert rows[1]["tasks_passed"] == rows[2]["tasks_passed"] == 8
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


def test_prepare_local_judge_pins_ollama(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "app.score.set_default_judge_model",
        lambda model: seen.setdefault("model", model),
    )
    settings = Settings(
        database_url="postgresql://minirag:minirag@127.0.0.1:5432/minirag",
        ollama_model="qwen3:8b",
        ollama_host="http://127.0.0.1:11434",
    )
    judge = prepare_local_judge(settings)
    assert judge.model is seen["model"]
    assert judge.rubric == JUDGE_RUBRIC
    assert judge.model_settings["seed"] == 77
    assert seen["model"]._model_name == "qwen3:8b" or getattr(seen["model"], "model_name", "") == "qwen3:8b"
