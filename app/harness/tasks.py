# Internal and Confidential - Not for External Distribution.
"""Ask one model to phrase tier-2 questions whose facts are already fixed."""

import json
import re
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from app.generation.generate import LanguageModel
from app.harness.graph import (
    Edge,
    callee_symbol,
    candidate_edges,
    load_graph,
    module_mermaid,
    node_label,
)

TASK_COUNT = 4
# One chunk per fact. A longer list blows the local model's context and it
# stops returning tier 2, or cites a chunk from a different fact.
SHOWN_CHUNKS = 1
_RELATION_VERB = {
    "call": "call",
    "calls": "call",
    "import": "import",
    "imports": "import",
}
TASK_RESPONSE_FORMAT = {
    "type": "object",
    "properties": {
        "tier2": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "integer"},
                    "prompt": {"type": "string"},
                    "chunk_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["fact", "prompt", "chunk_ids"],
            },
        },
    },
    "required": ["tier2"],
}


class Task(BaseModel):
    """One auditable eval item. The origin points back at the map or the corpus."""

    id: str
    tier: int
    prompt: str
    expected: dict
    origin: dict
    grounding_distance: float | None = None


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


def _source_file_name(edge: Edge) -> str:
    return Path(edge.source_file or edge.target_file).name


def _tier1_prompt(edge: Edge) -> str:
    verb = _RELATION_VERB.get(edge.relation.lower(), edge.relation.lower())
    return (
        f"In {edge.source} ({_source_file_name(edge)}), which symbol does it {verb}? "
        "Name the symbol and the source file."
    )


def _template_contains_callee(edge: Edge) -> bool:
    """True when the tier-1 wording already contains the graded symbol.

    The grader accepts the symbol as a substring of the answer, so a question
    the agent can copy would pass the bare arm.
    """
    symbol = callee_symbol(edge.target)
    return bool(symbol) and symbol in _tier1_prompt(edge).lower()


def _choose_edges(edges: list[Edge]) -> list[Edge]:
    pool = candidate_edges(edges, limit=max(len(edges), TASK_COUNT))
    chosen: list[Edge] = []
    for edge in pool:
        if _template_contains_callee(edge):
            continue
        chosen.append(edge)
        if len(chosen) == TASK_COUNT:
            break
    return chosen


def _mentions_ident(text: str, ident: str) -> bool:
    if not ident:
        return False
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(ident)}(?![A-Za-z0-9_])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _names_source(prompt: str, source: str, label: str) -> bool:
    return _mentions_ident(prompt, source) or _mentions_ident(prompt, label)


def _flat(prompt: str) -> str:
    return re.sub(r"\s+", " ", prompt.strip())


def _shown_cite(chunks: list[dict]) -> str | None:
    shown = chunks[:SHOWN_CHUNKS]
    if not shown:
        return None
    chunk_id = shown[0].get("chunk_id")
    return str(chunk_id) if chunk_id else None


def _prompt(edges: list[Edge], retrieved: list[list[dict]], labels: list[str]) -> str:
    facts = []
    for index, edge in enumerate(edges, start=1):
        shown = retrieved[index - 1][:SHOWN_CHUNKS]
        chunk_lines = [
            f"- {chunk['chunk_id']}: {chunk.get('section_title', '')} {chunk.get('text', '')[:160]}"
            for chunk in shown
        ]
        cite = _shown_cite(retrieved[index - 1]) or "none"
        facts.append(
            "\n".join(
                [
                    f"Fact {index}",
                    f"source: {edge.source}",
                    f"relation: {edge.relation}",
                    f"file: {edge.source_file or edge.target_file}",
                    f"label: {labels[index - 1]}",
                    f"cite: {cite}",
                    "chunks:",
                    *(chunk_lines or ["- none"]),
                ]
            )
        )
    return (
        "Phrase tier-2 questions. Do not invent facts. "
        f"Return JSON with tier2, a list of exactly {TASK_COUNT} objects. "
        "Each object has fact, prompt, and chunk_ids. "
        "fact is that fact's number. "
        "Each prompt must name that fact's source and ask what rule the cited chunk states. "
        "chunk_ids must be a one-item list of that fact's cite value and no other id. "
        "JSON only.\n\n" + "\n\n".join(facts)
    )


def _phrase(model: LanguageModel, prompt: str) -> str:
    """Ask for the task JSON. A schema is sent when the model accepts one."""
    chat = model.chat
    try:
        return chat(prompt, response_format=TASK_RESPONSE_FORMAT)
    except TypeError:
        return chat(prompt)


def _edge_payload(edge: Edge) -> dict:
    return {
        "source": edge.source,
        "relation": edge.relation,
        "target": edge.target,
        "source_file": edge.source_file,
        "target_file": edge.target_file,
    }


def _tier2_by_fact(items) -> dict[int, dict]:
    if not isinstance(items, list):
        raise TaskGenerationError("generator JSON is missing the tier 2 list")
    expected = set(range(1, TASK_COUNT + 1))
    by_fact: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            raise TaskGenerationError("tier 2 task is not an object")
        fact = item.get("fact")
        if isinstance(fact, bool) or not isinstance(fact, int) or fact not in expected:
            raise TaskGenerationError("tier 2 fact id is missing or unknown")
        if fact in by_fact:
            raise TaskGenerationError("tier 2 fact id is duplicated")
        by_fact[fact] = item
    if set(by_fact) != expected:
        raise TaskGenerationError("tier 2 fact ids are not 1 through 4")
    return by_fact


def generate_tasks(
    graph_path,
    *,
    model: LanguageModel,
    retrieve: Retrieve,
    on_event: Callable[[dict], None] | None = None,
) -> tuple[list[Task], list[dict], list[Edge]]:
    """Build four tier-1 and four tier-2 tasks from a graph and the bridge.

    The script chooses the edges and writes the tier-1 questions.
    The model only phrases tier 2.
    A tier-2 citation that is not the shown bridge chunk fails the whole list.

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
    chosen = _choose_edges(edges)
    if len(chosen) < TASK_COUNT:
        raise TaskGenerationError(f"need {TASK_COUNT} EXTRACTED edges, found {len(chosen)}")
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
    parsed = _parse_object(_phrase(model, _prompt(chosen, retrieved, labels)))
    by_fact = _tier2_by_fact(parsed.get("tier2"))
    tasks: list[Task] = []
    tier2_prompts: list[str] = []
    for index, edge in enumerate(chosen):
        payload = _edge_payload(edge)
        tasks.append(
            Task(
                id=f"t1-{index + 1:02d}",
                tier=1,
                prompt=_tier1_prompt(edge),
                expected=payload,
                origin={"node_ids": [edge.source, edge.target], "edge": payload},
            )
        )
    for index, edge in enumerate(chosen):
        item = by_fact[index + 1]
        prompt = str(item.get("prompt", "")).strip()
        cite = _shown_cite(retrieved[index])
        cited = item.get("chunk_ids")
        if not prompt:
            raise TaskGenerationError(f"tier 2 task {index + 1} has no prompt")
        if cited != [cite]:
            raise TaskGenerationError(
                f"tier 2 task {index + 1} cites chunks outside the bridge log"
            )
        if not _names_source(prompt, edge.source, labels[index]):
            raise TaskGenerationError(f"tier 2 task {index + 1} does not name its source")
        tier2_prompts.append(prompt)
        kept = [chunk for chunk in retrieved[index] if chunk["chunk_id"] in cited]
        distances = [chunk.get("distance") for chunk in kept]
        grounding = (
            sum(distances) / len(distances)
            if distances and all(isinstance(distance, (int, float)) for distance in distances)
            else None
        )
        payload = _edge_payload(edge)
        tasks.append(
            Task(
                id=f"t2-{index + 1:02d}",
                tier=2,
                prompt=prompt,
                expected={"chunk_ids": list(cited)},
                grounding_distance=grounding,
                origin={
                    "chunk_ids": list(cited),
                    "chunks": kept,
                    "node_id": edge.source,
                    "edge": payload,
                },
            )
        )
    if len({_flat(prompt) for prompt in tier2_prompts}) != len(tier2_prompts):
        raise TaskGenerationError("tier 2 prompts are not unique")
    if on_event is not None:
        on_event(
            {
                "call": "phrase",
                "station": "tasks",
                "title": "Phrase the task list",
                "detail": f"{TASK_COUNT} code questions and {TASK_COUNT} rule questions",
                "ran": "Template the code questions and phrase the rule questions.",
                "returned": "\n".join(task.prompt for task in tasks),
            }
        )
    return tasks, nodes, edges


def map_excerpt(task: Task, nodes: list[dict], edges: list[Edge]) -> str:
    """The map context for one task: its file's module diagram, never the answer.

    The excerpt deliberately omits the edge's target symbol. Printing the
    graded answer into the prompt would make the map arm pass by
    construction, so the map carries the scaffold (source, relation, file,
    module diagram) and the agent still has to find the callee itself.
    """
    edge = task.origin.get("edge") or task.expected
    source_file = edge.get("source_file") or edge.get("target_file") or ""
    diagram = module_mermaid(nodes, edges, source_file)
    return (
        f"source: {edge.get('source')}\n"
        f"relation: {edge.get('relation')}\n"
        f"file: {source_file}\n"
        f"{diagram}"
    )
