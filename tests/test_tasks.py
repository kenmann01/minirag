"""The eval generator may phrase tasks. It may not invent their evidence."""

import json
from pathlib import Path

import pytest

from app.harness.tasks import TaskGenerationError, generate_tasks


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


def facts_from_prompt(prompt: str) -> list[dict]:
    facts = []
    current = None
    for line in prompt.splitlines():
        if line.startswith("Fact "):
            current = {"fact": int(line.removeprefix("Fact ").strip()), "source": "", "cite": ""}
            facts.append(current)
        elif current is not None and line.startswith("source: "):
            current["source"] = line.split(": ", 1)[1].strip()
        elif current is not None and line.startswith("cite: "):
            current["cite"] = line.split(": ", 1)[1].strip()
    return facts


def tier2_items(prompt: str) -> list[dict]:
    return [
        {
            "fact": fact["fact"],
            "prompt": f"What rule does {fact['source']} state in {fact['cite']}?",
            "chunk_ids": [fact["cite"]],
            "target": "made-up",
        }
        for fact in facts_from_prompt(prompt)
    ]


class Phraser:
    def __init__(self):
        self.prompts = []

    def chat(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps({"tier2": tier2_items(prompt)})


class Rewrite(Phraser):
    def __init__(self, rewrite):
        super().__init__()
        self.rewrite = rewrite

    def chat(self, prompt: str) -> str:
        self.prompts.append(prompt)
        items = tier2_items(prompt)
        self.rewrite(items)
        return json.dumps({"tier2": items})


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
    assert tier1[0].prompt == (
        "In Loan1 (Loan1.java), which symbol does it call? Name the symbol and the source file."
    )
    assert "apply1" not in tier1[0].prompt.lower()
    assert [task.origin["chunk_ids"] for task in tasks if task.tier == 2] == [
        ["bank-1"],
        ["bank-2"],
        ["bank-3"],
        ["bank-4"],
    ]


def test_a_chunk_id_outside_the_bridge_log_is_rejected(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)

    def cite_unknown(items: list[dict]) -> None:
        for item in items:
            item["chunk_ids"] = ["not-retrieved"]

    with pytest.raises(TaskGenerationError, match="bridge log"):
        generate_tasks(graph, model=Rewrite(cite_unknown), retrieve=retrieve_factory("bank"))


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


def test_a_repeated_call_key_gives_its_slot_to_a_later_edge(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)
    data = json.loads(graph.read_text(encoding="utf-8"))
    data["links"].append(
        {
            "source": "Loan1",
            "target": "apply1b",
            "relation": "calls",
            "confidence": "EXTRACTED",
            "source_file": "loanaccount/Loan1.java",
            "target_file": "loanaccount/Loan1.java",
        }
    )
    graph.write_text(json.dumps(data), encoding="utf-8")
    tasks, _, _ = generate_tasks(graph, model=Phraser(), retrieve=retrieve_factory("bank"))
    assert [task.expected["target"] for task in tasks if task.tier == 1] == [
        "apply2",
        "apply3",
        "apply4",
        "helper",
    ]


def test_a_question_that_contains_the_callee_is_skipped(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)
    data = json.loads(graph.read_text(encoding="utf-8"))
    data["nodes"].extend(
        [
            {
                "id": "ApproveLoan",
                "label": "ApproveLoan",
                "source_file": "loanaccount/ApproveLoan.java",
            },
            {"id": "approve", "label": "approve", "source_file": "loanaccount/ApproveLoan.java"},
        ]
    )
    data["links"].append(
        {
            "source": "ApproveLoan",
            "target": "approve",
            "relation": "calls",
            "confidence": "EXTRACTED",
            "source_file": "loanaccount/ApproveLoan.java",
            "target_file": "loanaccount/ApproveLoan.java",
        }
    )
    graph.write_text(json.dumps(data), encoding="utf-8")
    tasks, _, _ = generate_tasks(graph, model=Phraser(), retrieve=retrieve_factory("bank"))
    assert [task.expected["target"] for task in tasks if task.tier == 1] == [
        "apply1",
        "apply2",
        "apply3",
        "apply4",
    ]


def test_an_import_edge_asks_which_symbol_it_imports(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)
    data = json.loads(graph.read_text(encoding="utf-8"))
    data["links"][3]["relation"] = "imports"
    graph.write_text(json.dumps(data), encoding="utf-8")
    tasks, _, _ = generate_tasks(graph, model=Phraser(), retrieve=retrieve_factory("bank"))
    tier1 = [task for task in tasks if task.tier == 1]
    assert "does it import?" in tier1[3].prompt
    assert "Loan4.java" in tier1[3].prompt


def test_reordered_tier2_items_stay_paired_by_fact_id(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)
    tasks, _, _ = generate_tasks(
        graph, model=Rewrite(lambda items: items.reverse()), retrieve=retrieve_factory("bank")
    )
    tier2 = [task for task in tasks if task.tier == 2]
    assert [task.origin["chunk_ids"] for task in tier2] == [
        ["bank-1"],
        ["bank-2"],
        ["bank-3"],
        ["bank-4"],
    ]
    assert "Loan1" in tier2[0].prompt
    assert "Loan4" in tier2[3].prompt


def test_a_duplicate_fact_id_is_rejected(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)

    def duplicate_first(items: list[dict]) -> None:
        items[1]["fact"] = items[0]["fact"]

    with pytest.raises(TaskGenerationError, match="duplicated"):
        generate_tasks(graph, model=Rewrite(duplicate_first), retrieve=retrieve_factory("bank"))


def test_an_unknown_fact_id_is_rejected(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)

    def renumber(items: list[dict]) -> None:
        items[0]["fact"] = 9

    with pytest.raises(TaskGenerationError, match="unknown"):
        generate_tasks(graph, model=Rewrite(renumber), retrieve=retrieve_factory("bank"))


def test_a_retrieved_id_other_than_the_shown_cite_is_rejected(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)

    def retrieve(query: str) -> dict:
        index = query.removeprefix("Loan")
        return {
            "chunks": [
                {"chunk_id": f"shown-{index}", "text": "shown"},
                {"chunk_id": f"extra-{index}", "text": "extra"},
            ]
        }

    def cite_extra(items: list[dict]) -> None:
        for item in items:
            item["chunk_ids"] = [item["chunk_ids"][0].replace("shown-", "extra-", 1)]

    with pytest.raises(TaskGenerationError, match="bridge log"):
        generate_tasks(graph, model=Rewrite(cite_extra), retrieve=retrieve)


def test_a_tier2_prompt_that_omits_its_source_is_rejected(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)

    def drop_source(items: list[dict]) -> None:
        for item in items:
            item["prompt"] = f"What rule is in {item['chunk_ids'][0]}?"

    with pytest.raises(TaskGenerationError, match="source"):
        generate_tasks(graph, model=Rewrite(drop_source), retrieve=retrieve_factory("bank"))


def test_a_source_prefix_does_not_count_as_naming_the_source(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)

    def prefix_only(items: list[dict]) -> None:
        for item in items:
            item["prompt"] = f"What rule does Loan state in {item['chunk_ids'][0]}?"

    with pytest.raises(TaskGenerationError, match="source"):
        generate_tasks(graph, model=Rewrite(prefix_only), retrieve=retrieve_factory("bank"))


def test_repeated_tier2_wording_is_rejected(tmp_path):
    graph = tmp_path / "graph.json"
    write_graph(graph)

    def same_wording(items: list[dict]) -> None:
        for item in items:
            item["prompt"] = "What rule does Loan1 Loan2 Loan3 Loan4 state?"

    with pytest.raises(TaskGenerationError, match="unique"):
        generate_tasks(graph, model=Rewrite(same_wording), retrieve=retrieve_factory("bank"))
