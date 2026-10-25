"""The deterministic fakes behind the committed parity fixture.

The fixture uses a fake graph, a canned model, and a canned judge. No Postgres
and no Ollama are involved. The helper is imported by tests/test_candidate.py
and tests/parity_score.py; it lives outside the app package so the runtime
path never depends on a test seam.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.candidate import run_candidate
from app.generation.generate import generate
from app.generation.ollama import ChatReply
from app.generation.validate import REFUSAL
from app.harness.agent import RunOutput
from app.harness.graph import load_graph
from app.harness.tasks import Task, map_excerpt

FIXTURES = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "parity"
GRAPH = FIXTURES / "graph.json"


def load_json(name: str) -> Any:
    """Load one committed fixture file."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


TASKS = [Task.model_validate(item) for item in load_json("tasks.json")]
RETRIEVAL = load_json("retrieval.json")
ANSWERS = load_json("answers.json")
EXPECTED = load_json("expected.json")
TASK_BY_PROMPT = {task.prompt: task for task in TASKS}
TASK_BY_ID = {task.id: task for task in TASKS}
NODES, EDGES = load_graph(GRAPH)


def fixture_retrieve(query: str) -> dict:
    """Return the committed chunks for a query, or an empty retrieval."""
    entry = RETRIEVAL["queries"].get(query)
    if entry is None:
        return {"chunks": []}
    return {"chunks": [dict(chunk) for chunk in entry["chunks"]]}


def fixture_judge(prompt: str, answer: str, references: list[dict]) -> dict:
    """Pass only when the answer names the cited chunk."""
    del prompt
    chunk_id = str(references[0]["chunk_id"])
    judged = ANSWERS["judge"]
    return {
        "passed": chunk_id in answer,
        "citation": chunk_id,
        "prompt_tokens": judged["prompt_tokens"],
        "completion_tokens": judged["completion_tokens"],
    }


class FixtureModel:
    """A deterministic model that answers from the committed table.

    The map excerpt answers tier 1 and the retrieved rule answers tier 2, so
    the three arms differ for the same reason the real scoreboard differs.
    """

    model_name = ANSWERS["model"]

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.seeds: list[int | None] = []

    def chat(self, prompt: str, *, seed: int | None = None) -> ChatReply:
        self.prompts.append(prompt)
        self.seeds.append(seed)
        question = prompt.rsplit("\nQuestion:\n", 1)[-1].strip()
        task = TASK_BY_PROMPT[question]
        excerpts = prompt.rsplit("\nPolicy excerpts:\n", 1)[-1].split("\nQuestion:\n", 1)[0]
        lines = excerpts.splitlines()
        labels = [line.removeprefix("Section: ") for line in lines if line.startswith("Section: ")]
        if task.tier == 1:
            label = next((line for line in labels if "map" in line.lower()), "")
            answering = bool(label)
        else:
            chunk_id = str(task.expected["chunk_ids"][0])
            label = next((line for line in labels if chunk_id in line), "")
            answering = bool(label)
        answer = ANSWERS["answers"][task.id] if answering else REFUSAL
        usage = ANSWERS["usage"][task.id]
        return ChatReply(
            json.dumps({"answer": answer, "section": label if answering else ""}),
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            model=self.model_name,
        )


class FixtureDeps:
    """The candidate dependencies backed by the committed fixture."""

    model_name = ANSWERS["model"]

    def __init__(self) -> None:
        self.model = FixtureModel()
        self.retrieved: list[str] = []
        self.generated: list[dict] = []

    def retrieve(self, query: str, *, top_k: int = 20) -> dict:
        del top_k
        self.retrieved.append(query)
        return fixture_retrieve(query)

    def generate(
        self,
        question: str,
        chunks: list[dict],
        *,
        prompt_text: str | None = None,
        seed: int | None = None,
    ) -> Any:
        self.generated.append(
            {
                "question": question,
                "chunks": chunks,
                "prompt_text": prompt_text,
                "seed": seed,
            }
        )
        return generate(question, chunks, self.model, prompt_text=prompt_text, seed=seed)

    def judge(self, prompt: str, answer: str, references: list[dict]) -> dict:
        return fixture_judge(prompt, answer, references)


def _task_for(prompt: str) -> Task:
    """Recover the fixture task from the scored prompt's first paragraph."""
    return TASK_BY_PROMPT[prompt.split("\n\n", 1)[0]]


def legacy_task_fn(prompt: str, context: str) -> RunOutput:
    """The current path: the fixture table, without the candidate adapter."""
    task = _task_for(prompt)
    usage = ANSWERS["usage"][task.id]
    if context == "bare":
        answering = task.tier == 2
        prompt_tokens = usage["prompt_tokens"] if answering else 0
        completion_tokens = usage["completion_tokens"] if answering else 0
    elif context == "map":
        answering = task.tier == 1
        prompt_tokens = usage["prompt_tokens"]
        completion_tokens = usage["completion_tokens"]
    else:
        answering = True
        prompt_tokens = usage["prompt_tokens"]
        completion_tokens = usage["completion_tokens"]
    answer = ANSWERS["answers"][task.id] if answering else REFUSAL
    return RunOutput(
        answer=answer,
        tool_calls=1 if context == "bare" else 0,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def make_candidate_task_fn() -> Any:
    """The spec path: every turn is a v1 request through run_candidate."""
    deps = FixtureDeps()

    def candidate_task_fn(prompt: str, context: str) -> RunOutput:
        task = _task_for(prompt)
        chunks: list[dict] = []
        if context in {"map", "map_rules"}:
            chunks.append(
                {
                    "chunk_id": f"map-{task.id}",
                    "text": map_excerpt(task, NODES, EDGES),
                }
            )
        if context == "map_rules":
            chunks.extend(
                {"chunk_id": chunk["chunk_id"], "text": chunk["text"]}
                for chunk in task.origin.get("chunks") or []
            )
        request = {
            "case_id": f"{context}/{task.id}",
            "prompt": task.prompt,
            "context": chunks,
            "prompt_override": None,
            "seed": ANSWERS["seed"],
        }
        response = run_candidate(request, deps=deps)
        return RunOutput(
            answer=response["answer"],
            tool_calls=response["tool_calls"],
            prompt_tokens=response["usage"]["prompt_tokens"],
            completion_tokens=response["usage"]["completion_tokens"],
        )

    return candidate_task_fn
