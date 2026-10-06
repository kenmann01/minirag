"""Ask-trace handler: one question in, answer JSON plus the stages that ran."""

import json

import pytest

from app.generate import section_label
from app.validate import REFUSAL
from app.ingest import run
from app.pgadapter import PgAdapter
from app.retrieve import search
from app.trace import trace_ask
from tests.test_rag import FakeLanguageModel, OrderingReranker


@pytest.fixture(autouse=True)
def empty_question_cache():
    adapter = PgAdapter()
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS question_cache")
    yield


def test_an_uncached_question_returns_the_answer_and_every_stage():
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    top = search(question, adapter)[0]
    label = section_label(top)
    model = FakeLanguageModel(
        json.dumps(
            {
                "answer": "Domestic travel meals are $75 per day.",
                "section": label,
            }
        )
    )

    result = trace_ask(
        question,
        database_adapter=adapter,
        language_model=model,
        reranker=OrderingReranker([top["chunk_id"]]),
    )

    assert result["response"]["answer"] == "Domestic travel meals are $75 per day."
    assert result["response"]["citation"]["source_doc"] == top["source_doc"]
    assert [stage["name"] for stage in result["trace"]] == [
        "ingest",
        "cache",
        "question_embedding",
        "keyword_search",
        "rrf",
        "cross_encoder",
        "passed_for_generation",
        "store",
    ]
    assert result["trace"][1]["status"] == "miss"
    assert any(
        chunk["chunk_id"] == top["chunk_id"]
        for chunk in result["trace"][4]["chunks"]
    )
    passed = result["trace"][6]["chunks"]
    assert passed[0]["chunk_id"] == top["chunk_id"]
    assert result["trace"][6]["answer"] == "Domestic travel meals are $75 per day."
    assert result["trace"][7]["stored"] is True


def test_an_empty_question_is_rejected_without_a_trace():
    result = trace_ask(
        "   ",
        database_adapter=PgAdapter(),
        language_model=FakeLanguageModel("{}"),
        reranker=OrderingReranker([]),
    )

    assert result == {"error": "Enter a question."}
    assert "trace" not in result


class BoomModel:
    def chat(self, prompt: str) -> str:
        raise RuntimeError("ollama down")


def test_a_pipeline_error_returns_the_error_and_no_trace():
    adapter = PgAdapter()
    run(adapter)
    result = trace_ask(
        "How much can I spend on food each day?",
        database_adapter=adapter,
        language_model=BoomModel(),
        reranker=OrderingReranker([]),
    )

    assert result == {"error": "ollama down"}
    assert "trace" not in result


def test_each_stage_lists_the_chunks_and_scores_from_that_lane():
    adapter = PgAdapter()
    run(adapter)
    question = "What triggers Villain Protocol?"
    top = search(question, adapter)[0]
    label = section_label(top)
    model = FakeLanguageModel(
        json.dumps({"answer": "Unauthorized weapon prototypes trigger Villain Protocol.", "section": label})
    )

    result = trace_ask(
        question,
        database_adapter=adapter,
        language_model=model,
        reranker=OrderingReranker([top["chunk_id"]]),
    )
    stages = {stage["name"]: stage for stage in result["trace"]}

    stored = next(
        chunk
        for chunk in stages["ingest"]["chunks"]
        if chunk["source_doc"] == "minion_expense_policy_2024.md"
    )
    assert stored["section"]
    assert stored["effective_date"] == "January 15, 2024"
    assert stored["superseded"] is False
    assert all(isinstance(chunk["distance"], float) for chunk in stages["question_embedding"]["chunks"])
    assert stages["keyword_search"]["chunks"]
    assert all(isinstance(chunk["rrf_score"], float) for chunk in stages["rrf"]["chunks"])
    assert [chunk["chunk_id"] for chunk in stages["cross_encoder"]["chunks"]] == [
        chunk["chunk_id"] for chunk in stages["passed_for_generation"]["chunks"]
    ]
    assert stages["cross_encoder"]["chunks"][0]["chunk_id"] == top["chunk_id"]


def test_a_cached_question_returns_the_stored_answer_and_stops():
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    top = search(question, adapter)[0]
    label = section_label(top)
    first_model = FakeLanguageModel(
        json.dumps({"answer": "Domestic travel meals are $75 per day.", "section": label})
    )
    trace_ask(
        question,
        database_adapter=adapter,
        language_model=first_model,
        reranker=OrderingReranker([top["chunk_id"]]),
    )
    second_model = FakeLanguageModel(
        json.dumps({"answer": "A different model says $10.", "section": label})
    )

    result = trace_ask(
        question,
        database_adapter=adapter,
        language_model=second_model,
        reranker=OrderingReranker([top["chunk_id"]]),
    )

    assert result["response"]["answer"] == "Domestic travel meals are $75 per day."
    assert [stage["name"] for stage in result["trace"]] == ["cache"]
    assert result["trace"][0]["status"] == "hit"
    assert second_model.prompts == []


def test_a_refusal_keeps_retrieval_and_does_not_store():
    adapter = PgAdapter()
    run(adapter)
    question = "Are gym memberships reimbursable?"
    model = FakeLanguageModel(json.dumps({"answer": REFUSAL, "section": ""}))

    result = trace_ask(
        question,
        database_adapter=adapter,
        language_model=model,
        reranker=OrderingReranker([]),
    )

    assert result["response"]["answer"] == REFUSAL
    assert result["response"]["citation"] is None
    names = [stage["name"] for stage in result["trace"]]
    assert names == [
        "ingest",
        "cache",
        "question_embedding",
        "keyword_search",
        "rrf",
        "cross_encoder",
        "passed_for_generation",
        "store",
    ]
    generated = result["trace"][6]
    assert generated["answer"] == REFUSAL
    assert generated["citation"] is None
    assert result["trace"][7]["stored"] is False

    follow_up = trace_ask(
        question,
        database_adapter=adapter,
        language_model=FakeLanguageModel(
            json.dumps({"answer": "Gym is covered.", "section": "1. Missing"})
        ),
        reranker=OrderingReranker([]),
    )
    assert follow_up["trace"][1]["status"] == "miss"


def test_including_superseded_policy_skips_the_cache_and_admits_the_stale_section():
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    bypassed = search(question, adapter, include_superseded=True)
    stale = next(
        row for row in bypassed if row["chunk_id"].startswith("minion_expense_policy_2021:s5:")
    )
    model = FakeLanguageModel(
        json.dumps(
            {
                "answer": "Meals are reimbursed at $60 per day domestic.",
                "section": section_label(stale),
            }
        )
    )

    result = trace_ask(
        question,
        database_adapter=adapter,
        language_model=model,
        reranker=OrderingReranker([stale["chunk_id"]]),
        include_superseded=True,
    )

    names = [stage["name"] for stage in result["trace"]]
    assert "cache" not in names
    lanes = [
        chunk["chunk_id"]
        for stage in result["trace"]
        if stage["name"] in {"question_embedding", "keyword_search"}
        for chunk in stage["chunks"]
    ]
    assert stale["chunk_id"] in lanes
    assert result["trace"][-1]["stored"] is False

    replay = trace_ask(
        question,
        database_adapter=adapter,
        language_model=FakeLanguageModel(
            json.dumps({"answer": "Domestic travel meals are $75 per day.", "section": section_label(stale)})
        ),
        reranker=OrderingReranker([]),
    )
    assert replay["trace"][1]["name"] == "cache"
    assert replay["trace"][1]["status"] == "miss"
