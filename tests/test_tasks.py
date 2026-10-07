"""The eval generator may phrase tasks. It may not invent their evidence."""

import json
from pathlib import Path

import pytest

from app.tasks import TaskGenerationError, generate_tasks


def write_graph(path: Path) -> None:
    nodes = []
    links = []
    for index in range(1, 5):
        nodes.append(
            {
                "id": f"Loan{index}",
                "label": f"Loan{index}",
                "source_file": f"loanaccount/Loan{index}.java",
            }
        )
        nodes.append(
            {
                "id": f"apply{index}",
                "label": f"apply{index}",
                "source_file": f"loanaccount/Loan{index}.java",
            }
        )
        links.append(
            {
                "source": f"Loan{index}",
                "target": f"apply{index}",
                "relation": "calls",
                "confidence": "EXTRACTED",
                "source_file": f"loanaccount/Loan{index}.java",
                "target_file": f"loanaccount/Loan{index}.java",
            }
        )
    nodes.extend(
        [
            {"id": "Other", "label": "Other", "source_file": "other/Other.java"},
            {"id": "helper", "label": "helper", "source_file": "other/Other.java"},
        ]
    )
    links.append(
        {
            "source": "Other",
            "target": "helper",
            "relation": "calls",
            "confidence": "EXTRACTED",
            "source_file": "other/Other.java",
            "target_file": "other/Other.java",
        }
    )
    links.append(
        {
            "source": "Loan1",
            "target": "guessed",
            "relation": "calls",
            "confidence": "INFERRED",
            "source_file": "loanaccount/Loan1.java",
            "target_file": "loanaccount/Loan1.java",
        }
    )
    path.write_text(json.dumps({"nodes": nodes, "links": links}), encoding="utf-8")


class Phraser:
    def __init__(self):
        self.prompts = []

    def chat(self, prompt: str) -> str:
        self.prompts.append(prompt)
        chunk_ids = []
        for line in prompt.splitlines():
            if line.startswith("- "):
                chunk_ids.append(line.split(":", 1)[0][2:].strip())
        return json.dumps(
            {
                "tier1": [
                    {"prompt": "Which method does this loan type call?", "target": "made-up"}
                    for _ in range(4)
                ],
                "tier2": [
                    {"prompt": f"What rule is in {chunk_id}?", "chunk_ids": [chunk_id]}
                    for chunk_id in chunk_ids[:4]
                ],
            }
        )


def retrieve_factory(prefix: str):
    def retrieve(query: str) -> dict:
        index = query.removeprefix("Loan")
        return {
            "chunks": [
                {
                    "chunk_id": f"{prefix}-{index}",
                    "section_title": "Limit",
                    "text": f"{prefix} text for {query}",
                }
            ]
        }

    return retrieve


def test_tier1_keeps_the_graph_edge_when_the_model_invents_one(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)
    tasks, _nodes, _edges = generate_tasks(
        graph, model=Phraser(), retrieve=retrieve_factory("bank")
    )
    tier1 = [task for task in tasks if task.tier == 1]
    assert [task.expected["target"] for task in tier1] == [
        "apply1",
        "apply2",
        "apply3",
        "apply4",
    ]
    assert tier1[0].expected["target"] != "made-up"
    assert [task.origin["chunk_ids"] for task in tasks if task.tier == 2] == [
        ["bank-1"],
        ["bank-2"],
        ["bank-3"],
        ["bank-4"],
    ]


def test_a_chunk_id_outside_the_bridge_log_is_rejected(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)

    class Bad(Phraser):
        def chat(self, prompt: str) -> str:
            return json.dumps(
                {
                    "tier1": [{"prompt": "Which method?"} for _ in range(4)],
                    "tier2": [{"prompt": "Rule?", "chunk_ids": ["not-retrieved"]} for _ in range(4)],
                }
            )

    with pytest.raises(TaskGenerationError, match="bridge log"):
        generate_tasks(graph, model=Bad(), retrieve=retrieve_factory("bank"))


def test_swapping_the_corpus_changes_the_tier2_prompts(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)
    banking, _, _ = generate_tasks(graph, model=Phraser(), retrieve=retrieve_factory("bank"))
    aviation, _, _ = generate_tasks(graph, model=Phraser(), retrieve=retrieve_factory("faa"))
    bank_prompts = [task.prompt for task in banking if task.tier == 2]
    faa_prompts = [task.prompt for task in aviation if task.tier == 2]
    assert bank_prompts != faa_prompts
    assert any("bank-1" in prompt for prompt in bank_prompts)
    assert any("faa-1" in prompt for prompt in faa_prompts)
