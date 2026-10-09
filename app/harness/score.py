# Internal and Confidential - Not for External Distribution.
"""Score one task list three times with pydantic-evals."""

import contextvars
import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, ReportEvaluator
from pydantic_evals.reporting.analyses import TableResult

from app.config import Settings, get_settings
from app.generation.generate import LanguageModel
from app.generation.judge import judge_rule
from app.harness.agent import RunOutput, build_agent, run_agent
from app.harness.graph import callee_symbol
from app.harness.metrics import MetricsSink
from app.harness.tasks import Task, TaskGenerationError, generate_tasks, map_excerpt
from app.storage.db import DatabaseAdapter

CONTEXTS = (
    ("RUN 1", "bare"),
    ("RUN 2", "map"),
    ("RUN 3", "map_rules"),
)

# Per-run accounting state (judge tokens and prices). A ContextVar keeps
# concurrent runs on the threaded live server from contaminating each other,
# because each run sets its own value inside its own thread.
_RUN_STATE: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "score_run_state", default=None
)
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


@dataclass
class GraphFact(Evaluator):
    """Script check: the answer names the expected symbol and file."""

    def evaluate(self, ctx) -> EvaluationReason:
        """Pass when the answer names the expected symbol and source file."""
        expected = ctx.metadata["expected"]
        answer = (ctx.output.answer or "").lower()
        symbol = callee_symbol(expected["target"])
        file_name = Path(
            expected.get("source_file") or expected.get("target_file") or ""
        ).name.lower()
        found_file = bool(file_name) and file_name in answer
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
        """Pass when the judge's verdict is true and its citation is an allowed id."""
        chunks = list(ctx.metadata["origin"]["chunks"])
        references = [
            {"chunk_id": str(chunk.get("chunk_id")), "text": str(chunk.get("text") or "")}
            for chunk in chunks
        ]
        verdict = judge_rule(str(ctx.inputs["prompt"]), ctx.output.answer, references)
        state = _RUN_STATE.get()
        if state is not None:
            state["judge_prompt"] += int(verdict.get("prompt_tokens") or 0)
            state["judge_completion"] += int(verdict.get("completion_tokens") or 0)
        allowed = [reference["chunk_id"] for reference in references]
        citation = verdict.get("citation")
        ok = bool(verdict.get("passed")) and citation in allowed
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
    """Sum passes, tool calls, judge calls, and cost for one experiment."""

    def evaluate(self, ctx) -> TableResult:
        """Aggregate the report into one scoreboard row on the experiment."""
        meta = ctx.experiment_metadata or {}
        state = _RUN_STATE.get() or {}
        prices = state.get("prices") or prices_from_settings(get_settings())
        passed_by_tier = {"1": {"passed": 0, "total": 0}, "2": {"passed": 0, "total": 0}}
        tool_calls = 0
        prompt_tokens = int(state.get("judge_prompt", 0))
        completion_tokens = int(state.get("judge_completion", 0))
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
        ctx.report.experiment_metadata = {
            **(ctx.report.experiment_metadata or {}),
            "scoreboard": row,
        }
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
        f"{chunk['chunk_id']}\n{chunk.get('text', '')}" for chunk in task.origin.get("chunks") or []
    )


def build_dataset(tasks: list[Task]) -> Dataset:
    """One dataset. Tier 1 is a script. Tier 2 is the evidence-based citation judge."""
    cases = []
    for task in tasks:
        evaluators: list[Evaluator] = [GraphFact()] if task.tier == 1 else [RuleCitation()]
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


def _case_rows(report, grounding: "dict[str, float | None] | None" = None) -> list[dict]:
    """Pull one row per case out of an experiment report."""
    distances = grounding or {}
    rows = []
    for case in report.cases:
        rows.append(
            {
                "task_id": case.name,
                "tier": int(case.metadata["tier"]),
                "passed": bool(case.assertions)
                and all(item.value for item in case.assertions.values()),
                "origin": case.metadata.get("origin"),
                "grounding_distance": distances.get(case.name),
                "tool_calls": int(getattr(case.output, "tool_calls", 0) or 0),
            }
        )
    for failure in report.failures:
        metadata = failure.metadata or {}
        rows.append(
            {
                "task_id": failure.name,
                "tier": int(metadata.get("tier") or 0),
                "passed": False,
                "origin": metadata.get("origin"),
                "grounding_distance": distances.get(failure.name),
                "tool_calls": 0,
            }
        )
    return rows


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


def _default_task_fn(
    repo: Path, settings: Settings, on_event: Callable[[dict], None] | None
) -> Callable[[str, str], RunOutput]:
    """Build the real jailed agent the arms will call."""

    def on_tool(event: dict) -> None:
        if on_event is not None:
            on_event({**event, "station": _RUN.get() or "bare"})

    agent = build_agent(repo, settings, on_tool=on_tool if on_event is not None else None)

    def task_fn(prompt: str, context: str) -> RunOutput:
        del context
        return run_agent(agent, prompt)

    return task_fn


def _task_list_sha(output_dir: Path) -> str:
    """Short content hash of the written task list, linking runs that share it."""
    payload = (output_dir / "tasks.json").read_bytes()
    return hashlib.sha256(payload).hexdigest()[:12]


def _provenance(
    *,
    graph_path: Path,
    label: str | None,
    origin: str,
    model_name: str | None,
    task_list_sha: str,
) -> dict:
    """Describe what produced this run so the board can tell runs apart."""
    settings = get_settings()
    corpus = settings.corpus_dir.strip() or "corpora/banking"
    return {
        "label": label,
        "model": model_name or settings.ollama_model,
        "corpus": corpus,
        "graph": graph_path.name,
        "task_list_sha": task_list_sha,
        "origin": origin,
    }


def run_score(
    *,
    graph_path: Path,
    repo: Path,
    output_dir: Path,
    model: LanguageModel,
    retrieve: Callable[[str], dict],
    task_fn: Callable[[str, str], RunOutput] | None = None,
    prices: Prices | None = None,
    on_event: Callable[[dict], None] | None = None,
    database_adapter: DatabaseAdapter | None = None,
    label: str | None = None,
    origin: str = "real",
    model_name: str | None = None,
) -> list[dict]:
    """Generate one task list and run it bare, with the map, and with the rules.

    Args:
        graph_path: Graphify graph the generator reads.
        repo: Repository the agent may read.
        output_dir: Where the task list and three reports are written.
        model: Model that phrases the tasks.
        retrieve: Bridge call. Run 3 calls it again and logs the returned chunk ids.
        task_fn: Optional stand-in for the agent. Receives the prompt and context.
        prices: Accounting rates. Defaults to configuration.
        on_event: Optional listener. Each graph, retrieve, agent, and grade call is passed through.
        database_adapter: Optional database adapter. When given, run metrics are
            written to Postgres under one shared run identity.
        label: Optional human label for the run, stored as provenance.
        origin: Provenance marker: ``real`` for full runs, ``seeded`` for rig seeds.
        model_name: Provenance override for the answering model. Defaults to settings.

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
            prices=prices,
            on_event=on_event,
            database_adapter=database_adapter,
            label=label,
            origin=origin,
            model_name=model_name,
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
    prices: Prices | None,
    on_event: Callable[[dict], None] | None,
    database_adapter: DatabaseAdapter | None,
    label: str | None,
    origin: str,
    model_name: str | None,
) -> list[dict]:
    """Generate one task list and run it bare, with the map, and with the rules."""
    tasks, nodes, edges = generate_tasks(
        graph_path, model=model, retrieve=retrieve, on_event=on_event
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "tasks.json").write_text(
        json.dumps([task.model_dump() for task in tasks], indent=2) + "\n",
        encoding="utf-8",
    )
    provenance = _provenance(
        graph_path=graph_path,
        label=label,
        origin=origin,
        model_name=model_name,
        task_list_sha=_task_list_sha(output_dir),
    )
    excerpts = {
        task.id: {"map": map_excerpt(task, nodes, edges), "rules": _rules_text(task)}
        for task in tasks
    }
    if task_fn is None:
        task_fn = _default_task_fn(repo, get_settings(), on_event)
    if prices is None:
        prices = prices_from_settings(get_settings())
    dataset = build_dataset(tasks)
    sink = MetricsSink(database_adapter) if database_adapter is not None else None
    grounding = {task.id: task.grounding_distance for task in tasks}
    run_key = uuid.uuid4()
    rows = []
    for run_id, context in CONTEXTS:
        station = "rules" if context == "map_rules" else context
        if context == "map_rules":
            for task in tasks:
                if task.tier == 2:
                    payload = retrieve(task.prompt)
                    returned_ids = [
                        str(chunk.get("chunk_id")) for chunk in payload.get("chunks") or []
                    ]
                    if on_event is not None:
                        on_event(
                            {
                                "call": "retrieve",
                                "station": "retrieve",
                                "title": "Log the rule again",
                                "detail": task.prompt,
                                "ran": task.prompt,
                                "returned": ", ".join(returned_ids) or "no chunks",
                                "task": task.id,
                                "chunk_ids": returned_ids,
                                "expected_ids": list(task.expected.get("chunk_ids") or []),
                            }
                        )
        state = {"prices": prices, "judge_prompt": 0, "judge_completion": 0}
        state_token = _RUN_STATE.set(state)
        run_token = _RUN.set(station)

        def task(inputs: dict, context: str = context) -> RunOutput:
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
            _RUN_STATE.reset(state_token)
        row = write_report(report, output_dir / f"{context}.json")
        if sink is not None:
            sink.record_context(
                run_id=run_key,
                context=context,
                scoreboard=row,
                cases=_case_rows(report, grounding),
                provenance=provenance,
            )
        rows.append(row)
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


__all__ = ["Prices", "TaskGenerationError", "run_score"]
