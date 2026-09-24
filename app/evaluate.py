"""The fixed exam: golden cases, recall and answer checks, the harness run."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.db import DatabaseAdapter
from app.generate import LanguageModel, generate
from app.reranker import Reranker
from app.retrieve import search
from app.schemas import AskResponse
from app.validate import REFUSAL

GOLDENS_PATH = Path(__file__).resolve().parents[1] / "eval" / "goldens.json"

Kind = Literal["answer", "hybrid_demo", "poisoned", "refusal"]


class Golden(BaseModel):
    id: str
    question: str
    expected: list[str]
    must_contain: list[str]
    must_not_contain: list[str]
    kind: Kind


def load_goldens(path: Path = GOLDENS_PATH) -> list[Golden]:
    return [Golden.model_validate(case) for case in json.loads(path.read_text(encoding="utf-8"))]


def section_prefix_hit(chunk_id: str, expected_sections: list[str]) -> bool:
    return any(
        chunk_id.startswith(f"{expected_section}:")
        for expected_section in expected_sections
    )


def recall_pass(ranked_chunk_ids: list[str], golden: Golden) -> bool:
    if not golden.expected:
        return True
    return any(section_prefix_hit(chunk_id, golden.expected) for chunk_id in ranked_chunk_ids)


def answer_failures(response: AskResponse, golden: Golden) -> list[str]:
    if golden.kind == "refusal":
        if response.answer != REFUSAL:
            return [f"expected the exact refusal, got: {response.answer!r}"]
        if response.citation is not None:
            return ["refusal must carry no citation"]
        return []
    lowered = response.answer.lower()
    failures = [
        f"missing required fact {fact!r}"
        for fact in golden.must_contain
        if fact.lower() not in lowered
    ]
    failures += [
        f"forbidden fact present {fact!r}"
        for fact in golden.must_not_contain
        if fact.lower() in lowered
    ]
    return failures


def run_exam(
    goldens: list[Golden],
    *,
    adapter: DatabaseAdapter,
    model: LanguageModel,
    reranker: Reranker,
    mode: str = "hybrid",
) -> dict:
    """Each golden through the exact ask path minus the question cache."""
    results = []
    for golden in goldens:
        candidates = search(golden.question, adapter, mode=mode)
        ranked = reranker.rank(golden.question, candidates)
        response = generate(golden.question, ranked, model)
        ranked_chunk_ids = [chunk["chunk_id"] for chunk in ranked]
        recall = recall_pass(ranked_chunk_ids, golden)
        failures = answer_failures(response, golden)
        results.append(
            {
                "id": golden.id,
                "kind": golden.kind,
                "question": golden.question,
                "recall": recall,
                "answer_failures": failures,
                "passed": recall and not failures,
                "answer": response.answer,
                "citation": (
                    response.citation.model_dump() if response.citation else None
                ),
                "ranked_chunk_ids": ranked_chunk_ids,
            }
        )
    return {
        "retriever": mode,
        "results": results,
        "summary": {
            "total": len(results),
            "passed": sum(result["passed"] for result in results),
            "recall_hits": sum(result["recall"] for result in results),
            "answer_passes": sum(
                not result["answer_failures"] for result in results
            ),
        },
    }


def format_table(record: dict) -> str:
    summary = record["summary"]
    lines = [
        "retriever={retriever}  goldens={total}  passed={passed}  "
        "recall={recall_hits}/{total}  answers={answer_passes}/{total}".format(
            retriever=record["retriever"], **summary
        ),
        "",
        f"{'id':<22}{'kind':<13}{'recall':<8}{'answer':<8}result",
    ]
    for result in record["results"]:
        lines.append(
            f"{result['id']:<22}{result['kind']:<13}"
            f"{'hit' if result['recall'] else 'MISS':<8}"
            f"{'ok' if not result['answer_failures'] else 'FAIL':<8}"
            f"{'PASS' if result['passed'] else 'FAIL'}"
        )
        lines.extend(f"    {failure}" for failure in result["answer_failures"])
    return "\n".join(lines)
