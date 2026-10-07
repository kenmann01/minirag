# Internal and Confidential - Not for External Distribution.
"""Ask one model to phrase a task list whose facts are already fixed."""

import json
import re
from collections.abc import Callable

from pydantic import BaseModel

from app.generate import LanguageModel
from app.graph import Edge, candidate_edges, load_graph, module_mermaid, node_label

TASK_COUNT = 4


class Task(BaseModel):
    """One auditable eval item. The origin points back at the map or the corpus."""

    id: str
    tier: int
    prompt: str
    expected: dict
    origin: dict


class TaskGenerationError(RuntimeError):
    """The generator could not produce four valid tasks in a tier."""


Retrieve = Callable[[str], dict]


def _parse_object(raw: str) -> dict:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise TaskGenerationError("generator did not return JSON")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise TaskGenerationError("generator returned malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise TaskGenerationError("generator JSON was not an object")
    return parsed


def _prompt(edges: list[Edge], retrieved: list[list[dict]], labels: list[str]) -> str:
    facts = []
    for index, edge in enumerate(edges, start=1):
        chunks = retrieved[index - 1]
        chunk_lines = [
            f"- {chunk['chunk_id']}: {chunk.get('section_title', '')} {chunk.get('text', '')[:240]}"
            for chunk in chunks
        ]
        facts.append(
            "\n".join(
                [
                    f"Fact {index}",
                    f"source: {edge.source}",
                    f"relation: {edge.relation}",
                    f"target: {edge.target}",
                    f"file: {edge.source_file or edge.target_file}",
                    f"label: {labels[index - 1]}",
                    "chunks:",
                    *(chunk_lines or ["- none"]),
                ]
            )
        )
    return (
        "Phrase a task list. Do not invent facts. "
        f"Return JSON with tier1 and tier2, each a list of {TASK_COUNT} objects in this fact order. "
        'tier1 objects are {"prompt": "..."} and ask for the target symbol and the file. '
        'tier2 objects are {"prompt": "...", "chunk_ids": ["..."]} and must cite only the chunk ids listed for that fact. '
        "Temperature is already zero. JSON only.\n\n" + "\n\n".join(facts)
    )


def _edge_payload(edge: Edge) -> dict:
    return {
        "source": edge.source,
        "relation": edge.relation,
        "target": edge.target,
        "source_file": edge.source_file,
        "target_file": edge.target_file,
    }


def generate_tasks(
    graph_path,
    *,
    model: LanguageModel,
    retrieve: Retrieve,
    on_event: Callable[[dict], None] | None = None,
) -> tuple[list[Task], list[dict], list[Edge]]:
    """Build four tier-1 and four tier-2 tasks from a graph and the bridge.

    The script chooses the edges. The model only phrases the questions.
    A tier-2 citation that the bridge did not return fails the whole list.

    Args:
        graph_path: Path to graphify ``graph.json``.
        model: Deterministic chat model. Temperature and seed are the caller's.
        retrieve: Bridge call returning ``{"chunks": [...]}``.

    Returns:
        The task list, the graph nodes, and the selected edges.

    Raises:
        TaskGenerationError: Fewer than four valid tasks could be produced.
    """
    nodes, edges = load_graph(graph_path)
    chosen = candidate_edges(edges, TASK_COUNT)
    if len(chosen) < TASK_COUNT:
        raise TaskGenerationError(
            f"need {TASK_COUNT} EXTRACTED edges, found {len(chosen)}"
        )
    if on_event is not None:
        on_event(
            {
                "call": "load_graph",
                "station": "graph",
                "title": "Read the code map",
                "detail": f"{len(chosen)} extracted edges kept",
                "ran": str(graph_path),
                "returned": "\n".join(
                    f"{edge.source} {edge.relation} {edge.target} · {edge.source_file}"
                    for edge in chosen
                ),
                "edges": [_edge_payload(edge) for edge in chosen],
            }
        )
    labels = [node_label(nodes, edge.source) for edge in chosen]
    retrieved: list[list[dict]] = []
    for label, edge in zip(labels, chosen, strict=True):
        payload = retrieve(label)
        chunks = list(payload.get("chunks") or [])
        retrieved.append(chunks)
        if on_event is not None:
            on_event(
                {
                    "call": "retrieve",
                    "station": "retrieve",
                    "title": "Retrieve the rule",
                    "detail": label,
                    "ran": label,
                    "returned": "\n".join(
                        f"{chunk['chunk_id']}: {chunk.get('text', '')[:240]}" for chunk in chunks
                    )
                    or "no chunks",
                    "target": edge.target,
                    "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
                }
            )
    parsed = _parse_object(model.chat(_prompt(chosen, retrieved, labels)))
    tier1 = parsed.get("tier1")
    tier2 = parsed.get("tier2")
    if not isinstance(tier1, list) or not isinstance(tier2, list):
        raise TaskGenerationError("generator JSON is missing tier lists")
    if len(tier1) < TASK_COUNT or len(tier2) < TASK_COUNT:
        raise TaskGenerationError("generator returned fewer than four tasks in a tier")
    if on_event is not None:
        on_event(
            {
                "call": "phrase",
                "station": "tasks",
                "title": "Phrase the task list",
                "detail": f"{TASK_COUNT} code questions and {TASK_COUNT} rule questions",
                "ran": "Phrase questions from the extracted edges and the retrieved chunks.",
                "returned": "\n".join(
                    str(item.get("prompt", "")) for item in [*tier1[:TASK_COUNT], *tier2[:TASK_COUNT]] if isinstance(item, dict)
                ),
            }
        )
    tasks: list[Task] = []
    for index, edge in enumerate(chosen):
        item = tier1[index]
        prompt = str(item.get("prompt", "")).strip() if isinstance(item, dict) else ""
        if not prompt:
            raise TaskGenerationError(f"tier 1 task {index + 1} has no prompt")
        payload = _edge_payload(edge)
        tasks.append(
            Task(
                id=f"t1-{index + 1:02d}",
                tier=1,
                prompt=prompt,
                expected=payload,
                origin={"node_ids": [edge.source, edge.target], "edge": payload},
            )
        )
    for index, edge in enumerate(chosen):
        item = tier2[index]
        if not isinstance(item, dict):
            raise TaskGenerationError(f"tier 2 task {index + 1} is not an object")
        prompt = str(item.get("prompt", "")).strip()
        cited = item.get("chunk_ids") or []
        allowed = {chunk["chunk_id"] for chunk in retrieved[index]}
        if not prompt or not cited or any(chunk_id not in allowed for chunk_id in cited):
            raise TaskGenerationError(
                f"tier 2 task {index + 1} cites chunks outside the bridge log"
            )
        kept = [chunk for chunk in retrieved[index] if chunk["chunk_id"] in cited]
        payload = _edge_payload(edge)
        tasks.append(
            Task(
                id=f"t2-{index + 1:02d}",
                tier=2,
                prompt=prompt,
                expected={"chunk_ids": list(cited)},
                origin={
                    "chunk_ids": list(cited),
                    "chunks": kept,
                    "node_id": edge.source,
                    "edge": payload,
                },
            )
        )
    return tasks, nodes, edges


def map_excerpt(task: Task, nodes: list[dict], edges: list[Edge]) -> str:
    """The map context for one task: its edge, its file, and the module diagram."""
    edge = task.origin.get("edge") or task.expected
    source_file = edge.get("source_file") or edge.get("target_file") or ""
    diagram = module_mermaid(nodes, edges, source_file)
    return (
        f"source: {edge.get('source')}\n"
        f"relation: {edge.get('relation')}\n"
        f"target: {edge.get('target')}\n"
        f"file: {source_file}\n"
        f"{diagram}"
    )
