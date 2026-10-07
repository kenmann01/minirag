# Internal and Confidential - Not for External Distribution.
"""Score one task list three times with pydantic-evals."""

import contextvars
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, LLMJudge, ReportEvaluator
from pydantic_evals.evaluators.llm_as_a_judge import set_default_judge_model
from pydantic_evals.reporting.analyses import TableResult

from app.agent import RunOutput, build_agent, ollama_base_url, run_agent
from app.config import Settings, get_settings
from app.generate import LanguageModel
from app.tasks import Task, TaskGenerationError, generate_tasks, map_excerpt

JUDGE_RUBRIC = (
    "The answer is supported by one of the allowed chunk ids in the input. "
    "The reason must cite that chunk id exactly. "
    "Fail when no allowed chunk id supports the answer."
)

CONTEXTS = (
    ("RUN 1", "bare"),
    ("RUN 2", "map"),
    ("RUN 3", "map_rules"),
)

_JUDGE_TOKENS = {"prompt": 0, "completion": 0}
_PRICES: "Prices | None" = None
_EMIT: contextvars.ContextVar[Callable[[dict], None] | None] = contextvars.ContextVar(
    "score_emit", default=None
)
_RUN: contextvars.ContextVar[str] = contextvars.ContextVar("score_run", default="")


def _trace(event: dict) -> None:
    """Forward one score call when a live trace is listening."""
    emit = _EMIT.get()
    if emit is not None:
        emit(event)


@dataclass(frozen=True)
class Prices:
    """Local accounting rates for one scoreboard."""

    input_usd_per_million: float
    output_usd_per_million: float
    tool_usd: float


def prices_from_settings(settings: Settings) -> Prices:
    """Read the scoreboard rates from configuration."""
    return Prices(
        input_usd_per_million=settings.input_usd_per_million,
        output_usd_per_million=settings.output_usd_per_million,
        tool_usd=settings.tool_usd,
    )


def cost_usd(prompt_tokens: int, completion_tokens: int, tool_calls: int, prices: Prices) -> float:
    """Turn observed tokens and tool calls into the scoreboard's dollar figure."""
    return (
        prompt_tokens * prices.input_usd_per_million / 1_000_000
        + completion_tokens * prices.output_usd_per_million / 1_000_000
        + tool_calls * prices.tool_usd
    )


def prepare_local_judge(settings: Settings) -> LLMJudge:
    """Point the judge at local Ollama before any grade runs.

    ``LLMJudge`` defaults to a cloud model. This replaces that default and
    passes the same local model on the evaluator itself.
    """
    from pydantic_ai.models.ollama import OllamaModel
    from pydantic_ai.providers.ollama import OllamaProvider

    model = OllamaModel(
        settings.ollama_model,
        provider=OllamaProvider(base_url=ollama_base_url(settings.ollama_host)),
        settings={"temperature": 0, "seed": 77},
    )
    set_default_judge_model(model)
    return LLMJudge(
        rubric=JUDGE_RUBRIC,
        model=model,
        include_input=True,
        model_settings={"temperature": 0, "seed": 77},
    )


def judge_rule(prompt: str, answer: str, allowed: list[str]) -> dict:
    """Ask the local model which allowed chunk id supports the answer.

    The returned citation is checked by ``RuleCitation``. Tokens are included
    in the run cost.
    """
    from pydantic import BaseModel
    from pydantic_ai import Agent

    class CitationVerdict(BaseModel):
        passed: bool
        citation: str

    settings = get_settings()
    from pydantic_ai.models.ollama import OllamaModel
    from pydantic_ai.providers.ollama import OllamaProvider

    model = OllamaModel(
        settings.ollama_model,
        provider=OllamaProvider(base_url=ollama_base_url(settings.ollama_host)),
        settings={"temperature": 0, "seed": 77},
    )
    agent = Agent(
        model,
        instructions=(
            "Cite exactly one chunk id from the allowed list. "
            "passed is true only when that chunk supports the answer."
        ),
        output_type=CitationVerdict,
    )
    allowed_text = ", ".join(allowed)
    result = agent.run_sync(
        f"Question:\n{prompt}\n\nAnswer:\n{answer}\n\nAllowed chunk ids: {allowed_text}"
    )
    usage = result.usage()
    return {
        "pass": bool(result.output.passed),
        "citation": result.output.citation,
        "prompt_tokens": int(usage.input_tokens),
        "completion_tokens": int(usage.output_tokens),
    }


@dataclass
class GraphFact(Evaluator):
    """Script check: the answer names the expected symbol and file."""

    def evaluate(self, ctx) -> EvaluationReason:
        expected = ctx.metadata["expected"]
        answer = (ctx.output.answer or "").lower()
        symbol = str(expected["target"]).split(":")[-1].split(".")[-1].lower()
        file_name = Path(expected.get("source_file") or expected.get("target_file") or "").name.lower()
        found_file = not file_name or file_name in answer
        ok = bool(symbol) and symbol in answer and found_file
        reason = f"{symbol} {file_name}".strip()
        _trace(
            {
                "call": "grade",
                "station": _RUN.get() or "bare",
                "title": "Grade the code fact",
                "detail": reason,
                "ran": str(ctx.output.answer or ""),
                "returned": "pass" if ok else "fail",
                "task": str((ctx.inputs or {}).get("id") or ""),
                "passed": ok,
            }
        )
        return EvaluationReason(value=ok, reason=reason)


@dataclass
class RuleCitation(Evaluator):
    """Fail unless the judge cites one of this case's origin chunk ids."""

    def evaluate(self, ctx) -> EvaluationReason:
        allowed = list(ctx.metadata["origin"]["chunk_ids"])
        verdict = judge_rule(str(ctx.inputs["prompt"]), ctx.output.answer, allowed)
        _JUDGE_TOKENS["prompt"] += int(verdict.get("prompt_tokens") or 0)
        _JUDGE_TOKENS["completion"] += int(verdict.get("completion_tokens") or 0)
        citation = verdict.get("citation")
        ok = bool(verdict.get("pass")) and citation in allowed
        _trace(
            {
                "call": "grade",
                "station": _RUN.get() or "rules",
                "title": "Grade the cited rule",
                "detail": str(citation),
                "ran": str(ctx.output.answer or ""),
                "returned": f"{'pass' if ok else 'fail'} · {citation}",
                "task": str((ctx.inputs or {}).get("id") or ""),
                "passed": ok,
            }
        )
        return EvaluationReason(value=ok, reason=str(citation))


@dataclass
class ScoreboardReport(ReportEvaluator):
    """Sum passes, tool calls, and cost for one experiment."""

    def evaluate(self, ctx) -> TableResult:
        meta = ctx.experiment_metadata or {}
        prices = _PRICES or Prices(0.15, 0.60, 0.001)
        passed_by_tier = {"1": {"passed": 0, "total": 0}, "2": {"passed": 0, "total": 0}}
        tool_calls = 0
        prompt_tokens = _JUDGE_TOKENS["prompt"]
        completion_tokens = _JUDGE_TOKENS["completion"]
        passed = 0
        for case in ctx.report.cases:
            tier = str(int(case.metadata["tier"]))
            passed_by_tier[tier]["total"] += 1
            ok = bool(case.assertions) and all(item.value for item in case.assertions.values())
            if ok:
                passed += 1
                passed_by_tier[tier]["passed"] += 1
            tool_calls += int(case.output.tool_calls)
            prompt_tokens += int(case.output.prompt_tokens)
            completion_tokens += int(case.output.completion_tokens)
        for failure in ctx.report.failures:
            tier = str((failure.metadata or {}).get("tier", ""))
            if tier in passed_by_tier:
                passed_by_tier[tier]["total"] += 1
        total = sum(item["total"] for item in passed_by_tier.values())
        row = {
            "run_id": meta.get("run_id", ""),
            "context": meta.get("context", ""),
            "tasks_passed": passed,
            "tasks_total": total,
            "passed_by_tier": passed_by_tier,
            "tool_calls": tool_calls,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": cost_usd(prompt_tokens, completion_tokens, tool_calls, prices),
        }
        ctx.report.experiment_metadata = {**(ctx.report.experiment_metadata or {}), "scoreboard": row}
        return TableResult(
            title="scoreboard",
            columns=[
                "run_id",
                "context",
                "tasks_passed",
                "tasks_total",
                "tool_calls",
                "cost_usd",
            ],
            rows=[
                [
                    row["run_id"],
                    row["context"],
                    row["tasks_passed"],
                    row["tasks_total"],
                    row["tool_calls"],
                    row["cost_usd"],
                ]
            ],
        )


def _rules_text(task: Task) -> str:
    return "\n\n".join(
        f"{chunk['chunk_id']}\n{chunk.get('text', '')}"
        for chunk in task.origin.get("chunks") or []
    )


def build_dataset(tasks: list[Task], tier2_judge: Evaluator | None) -> Dataset:
    """One dataset. Tier 1 is a script. Tier 2 is a model judge plus a citation check."""
    judge = tier2_judge if tier2_judge is not None else prepare_local_judge(get_settings())
    cases = []
    for task in tasks:
        evaluators: list[Evaluator] = []
        if task.tier == 1:
            evaluators.append(GraphFact())
        else:
            evaluators.extend([judge, RuleCitation()])
        cases.append(
            Case(
                name=task.id,
                inputs={"id": task.id, "prompt": task.prompt, "tier": task.tier},
                metadata={
                    "tier": task.tier,
                    "expected": task.expected,
                    "origin": task.origin,
                },
                evaluators=evaluators,
            )
        )
    return Dataset(
        name="map-writes-the-test",
        cases=cases,
        report_evaluators=[ScoreboardReport()],
    )


def _dump_output(output) -> dict:
    if isinstance(output, RunOutput):
        return output.model_dump()
    if hasattr(output, "model_dump"):
        return output.model_dump()
    return {"answer": str(output), "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


def write_report(report, path: Path) -> dict:
    """Write one experiment, including the scoreboard row the comparison page reads."""
    scoreboard = (report.experiment_metadata or {}).get("scoreboard")
    payload = {
        "name": report.name,
        "scoreboard": scoreboard,
        "cases": [
            {
                "name": case.name,
                "metadata": case.metadata,
                "output": _dump_output(case.output),
                "assertions": {
                    name: {"value": result.value, "reason": result.reason}
                    for name, result in case.assertions.items()
                },
            }
            for case in report.cases
        ],
        "failures": [failure.name for failure in report.failures],
        "analyses": [analysis.model_dump() for analysis in report.analyses],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return scoreboard


def run_score(
    *,
    graph_path: Path,
    repo: Path,
    output_dir: Path,
    model: LanguageModel,
    retrieve: Callable[[str], dict],
    task_fn: Callable[[str, str], RunOutput] | None = None,
    tier2_judge: Evaluator | None = None,
    prices: Prices | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Generate one task list and run it bare, with the map, and with the rules.

    Args:
        graph_path: Graphify graph the generator reads.
        repo: Repository the agent may read.
        output_dir: Where the task list and three reports are written.
        model: Model that phrases the tasks.
        retrieve: Bridge call. Run 3 calls it again and logs chunk ids.
        task_fn: Optional stand-in for the agent. Receives the prompt and context.
        tier2_judge: Optional stand-in for ``LLMJudge``. The citation check still runs.
        prices: Accounting rates. Defaults to configuration.
        on_event: Optional listener. Each graph, retrieve, agent, and grade call is passed through.

    Returns:
        The three scoreboard rows in run order.

    Raises:
        TaskGenerationError: The task list could not be grounded.
    """
    emit_token = _EMIT.set(on_event)
    try:
        return _run_score(
            graph_path=graph_path,
            repo=repo,
            output_dir=output_dir,
            model=model,
            retrieve=retrieve,
            task_fn=task_fn,
            tier2_judge=tier2_judge,
            prices=prices,
            on_event=on_event,
        )
    finally:
        _EMIT.reset(emit_token)


def _run_score(
    *,
    graph_path: Path,
    repo: Path,
    output_dir: Path,
    model: LanguageModel,
    retrieve: Callable[[str], dict],
    task_fn: Callable[[str, str], RunOutput] | None,
    tier2_judge: Evaluator | None,
    prices: Prices | None,
    on_event: Callable[[dict], None] | None,
) -> list[dict]:
    """Generate one task list and run it bare, with the map, and with the rules."""
    global _PRICES
    tasks, nodes, edges = generate_tasks(
        graph_path, model=model, retrieve=retrieve, on_event=on_event
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "tasks.json").write_text(
        json.dumps([task.model_dump() for task in tasks], indent=2) + "\n",
        encoding="utf-8",
    )
    excerpts = {
        task.id: {"map": map_excerpt(task, nodes, edges), "rules": _rules_text(task)}
        for task in tasks
    }
    if task_fn is None:
        def on_tool(event: dict) -> None:
            if on_event is not None:
                on_event({**event, "station": _RUN.get() or "bare"})

        agent = build_agent(repo, get_settings(), on_tool=on_tool if on_event is not None else None)

        def task_fn(prompt: str, context: str) -> RunOutput:
            del context
            return run_agent(agent, prompt)

    if prices is None:
        prices = prices_from_settings(get_settings())
    _PRICES = prices
    if tier2_judge is None:
        prepare_local_judge(get_settings())
    dataset = build_dataset(tasks, tier2_judge)
    rows = []
    for run_id, context in CONTEXTS:
        station = "rules" if context == "map_rules" else context
        if context == "map_rules":
            for task in tasks:
                if task.tier == 2:
                    retrieve(task.prompt)
                    if on_event is not None:
                        on_event(
                            {
                                "call": "retrieve",
                                "station": "retrieve",
                                "title": "Log the rule again",
                                "detail": task.prompt,
                                "ran": task.prompt,
                                "returned": ", ".join(task.expected.get("chunk_ids") or []),
                                "task": task.id,
                                "chunk_ids": list(task.expected.get("chunk_ids") or []),
                            }
                        )
        _JUDGE_TOKENS["prompt"] = 0
        _JUDGE_TOKENS["completion"] = 0
        run_token = _RUN.set(station)

        def task(inputs: dict, context: str = context, station: str = station) -> RunOutput:
            parts = [inputs["prompt"]]
            extra = excerpts[inputs["id"]]
            has_map = context in {"map", "map_rules"}
            has_rules = context == "map_rules"
            if has_map:
                parts.append(extra["map"])
            if has_rules:
                parts.append(extra["rules"])
            prompt = "\n\n".join(part for part in parts if part)
            result = task_fn(prompt, context)
            if on_event is not None:
                on_event(
                    {
                        "call": "agent",
                        "station": station,
                        "title": f"Ask the model · {context}",
                        "detail": inputs["prompt"],
                        "ran": prompt,
                        "returned": result.answer,
                        "task": inputs["id"],
                        "has_map": has_map,
                        "has_rules": has_rules,
                    }
                )
            return result

        try:
            report = dataset.evaluate_sync(
                task,
                name=context,
                max_concurrency=1,
                progress=False,
                metadata={"run_id": run_id, "context": context},
            )
        finally:
            _RUN.reset(run_token)
        rows.append(write_report(report, output_dir / f"{context}.json"))
    if on_event is not None:
        on_event(
            {
                "call": "scoreboard",
                "station": "board",
                "title": "Scoreboard",
                "detail": "Three runs of the same task list",
                "ran": "Same task list, three contexts",
                "returned": "\n".join(
                    f"{row['run_id']} {row['context']}: {row['tasks_passed']} of {row['tasks_total']}"
                    for row in rows
                ),
                "rows": rows,
            }
        )
    return rows


__all__ = ["TaskGenerationError", "run_score"]
