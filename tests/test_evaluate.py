"""Golden exam and evaluation harness behavior at their public seams.

``python -m app eval`` loads ``eval/goldens.json`` and runs each golden
question through the same search the ask path uses. Search reads
``policy_chunks``, which default ingestion fills from ``corpora/banking``.
"""

import json

import pytest

from app.cli import main
from app.corpus.embeddings import embed_texts
from app.corpus.ingest import run
from app.generation.generate import PROMPT_PATH
from app.generation.schemas import AskResponse, Citation
from app.generation.validate import REFUSAL
from app.harness.evaluate import (
    Golden,
    answer_failures,
    format_table,
    load_goldens,
    recall_pass,
    run_exam,
    section_prefix_hit,
)
from app.retrieval.reranker import CrossEncoderReranker
from app.retrieval.retrieve import search
from app.storage.pgadapter import PgAdapter

EXPECTED_EXAM = {
    "broker-fee": "answer",
    "application-fee": "answer",
    "closing-agent-fee": "answer",
    "statement-timing": "answer",
    "minimum-interest-charge": "answer",
    "change-in-terms-notice": "answer",
    "counseling-referrals": "answer",
    "grace-period-heading": "hybrid_demo",
    "minimum-payment-warning": "hybrid_demo",
    "out-of-scope": "refusal",
}


def test_the_golden_file_holds_the_ten_case_exam():
    goldens = load_goldens()
    assert {golden.id: golden.kind for golden in goldens} == EXPECTED_EXAM
    assert all(golden.question.strip().endswith("?") for golden in goldens)
    assert all(golden.expected for golden in goldens if golden.kind != "refusal")
    assert not next(g for g in goldens if g.id == "out-of-scope").expected


def test_the_refusal_golden_targets_an_uncovered_topic():
    refusal = next(g for g in load_goldens() if g.kind == "refusal")
    assert refusal.must_contain == ["The provided policy does not answer this question."]


def test_every_expected_section_exists_in_the_ingested_table():
    adapter = PgAdapter()
    run(adapter)
    with adapter.connect() as conn:
        chunk_ids = {row[0] for row in conn.execute("SELECT chunk_id FROM policy_chunks")}
    for golden in load_goldens():
        for expected_section in golden.expected:
            assert any(
                chunk_id.startswith(f"{expected_section}:") for chunk_id in chunk_ids
            ), expected_section


def test_a_golden_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        Golden(
            id="x",
            question="?",
            expected=[],
            must_contain=[],
            must_not_contain=[],
            kind="vibes",
        )


def _golden(golden_id: str) -> Golden:
    return next(g for g in load_goldens() if g.id == golden_id)


def test_the_prefix_matcher_hits_on_any_child_of_the_expected_section():
    assert section_prefix_hit("reg-z-1026:s1:c01", ["reg-z-1026:s1"])
    assert section_prefix_hit("reg-z-1026:s1:c03", ["reg-z-1026:s1"])


def test_the_prefix_matcher_rejects_other_sections_and_false_prefixes():
    assert not section_prefix_hit("reg-z-1026:s2:c01", ["reg-z-1026:s1"])
    assert not section_prefix_hit("reg-z-1026:s11:c01", ["reg-z-1026:s1"])


def test_recall_pass_counts_any_expected_child_inside_the_ranked_chunks():
    broker_fee = _golden("broker-fee")
    without = [
        "reg-z-1026:s2:c01",
        "reg-z-1026:s3:c01",
        "reg-z-1026:s4:c01",
        "reg-z-1026:s5:c01",
    ]
    assert not recall_pass(without, broker_fee)
    assert recall_pass(without[:3] + ["reg-z-1026:s1:c02"], broker_fee)


def test_recall_pass_accepts_any_one_of_the_expected_sections():
    statement_timing = _golden("statement-timing")
    assert recall_pass(["reg-z-1026:s3:c01"], statement_timing)
    assert recall_pass(["reg-z-1026:s2:c04"], statement_timing)


def test_recall_pass_is_automatic_when_nothing_is_expected():
    refusal = _golden("out-of-scope")
    assert recall_pass([], refusal)


def test_answer_checks_pass_a_canned_good_answer():
    response = AskResponse(
        answer="Yes. Broker fees are a finance charge even when the creditor does not require one.",
        citation=Citation(
            source_doc="reg-z-1026.md",
            effective_date=None,
            section="reg-z-1026.md 1. Section 4",
        ),
        retrieved_chunks=[],
    )
    assert answer_failures(response, _golden("broker-fee")) == []


def test_answer_checks_fail_an_answer_missing_the_required_fact():
    response = AskResponse(
        answer="The rule is in the regulation.",
        citation=None,
        retrieved_chunks=[],
    )
    failures = answer_failures(response, _golden("minimum-interest-charge"))
    assert any("$1.00" in failure for failure in failures)


def test_the_refusal_check_demands_the_exact_refusal_without_a_citation():
    refusal = _golden("out-of-scope")
    paraphrase = AskResponse(
        answer="Sorry, the regulation has nothing on gym memberships.",
        citation=None,
        retrieved_chunks=[],
    )
    cited = AskResponse(
        answer=REFUSAL,
        citation=Citation(
            source_doc="reg-z-1026.md",
            effective_date=None,
            section="reg-z-1026.md 1. Section 4",
        ),
        retrieved_chunks=[],
    )
    exact = AskResponse(answer=REFUSAL, citation=None, retrieved_chunks=[])
    assert answer_failures(paraphrase, refusal)
    assert answer_failures(cited, refusal)
    assert answer_failures(exact, refusal) == []


class GoldenCannedModel:
    """Answers each golden from its own facts, citing the top-ranked section."""

    def __init__(self, goldens: list[Golden], wrong_for: str | None = None):
        self.goldens = goldens
        self.wrong_for = wrong_for
        self.prompts: list[str] = []

    def chat(self, prompt: str) -> str:
        self.prompts.append(prompt)
        asked = prompt.rsplit("\nQuestion:\n", 1)[-1]
        golden = next(g for g in self.goldens if g.question in asked)
        if golden.kind == "refusal":
            return json.dumps({"answer": REFUSAL, "section": ""})
        excerpts = prompt.rsplit("\nPolicy excerpts:\n", 1)[-1].split("\nQuestion:\n", 1)[0]
        label = excerpts.split("Section: ", 1)[1].split("\n", 1)[0]
        if golden.id == self.wrong_for:
            return json.dumps({"answer": "The rule is something else entirely.", "section": label})
        return json.dumps({"answer": " ".join(golden.must_contain), "section": label})


def test_the_canned_model_cites_the_retrieved_section_not_the_few_shot_example():
    golden = _golden("broker-fee")
    prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace(
            "{excerpts}",
            "Section: reg-z-1026.md 1. Section 4\nExcerpt:\nMortgage broker fees are finance charges.",
        )
        .replace("{question}", golden.question)
    )
    reply = json.loads(GoldenCannedModel([golden]).chat(prompt))
    assert reply["section"] == "reg-z-1026.md 1. Section 4"
    assert "finance charge" in reply["answer"]


def test_the_exam_passes_end_to_end_with_canned_generation_and_real_retrieval():
    adapter = PgAdapter()
    run(adapter)
    goldens = load_goldens()
    record = run_exam(
        goldens,
        adapter=adapter,
        model=GoldenCannedModel(goldens),
        reranker=CrossEncoderReranker(),
    )
    assert record["retriever"] == "hybrid"
    assert record["summary"] == {
        "total": 10,
        "passed": 10,
        "recall_hits": 10,
        "answer_passes": 10,
    }
    by_id = {result["id"]: result for result in record["results"]}
    assert by_id["grace-period-heading"]["recall"] is True
    assert by_id["minimum-payment-warning"]["recall"] is True


def test_the_exam_fails_on_an_induced_regression():
    adapter = PgAdapter()
    run(adapter)
    goldens = load_goldens()
    broken = [
        (
            golden.model_copy(update={"expected": ["reg-z-1026:s9"]})
            if golden.id == "broker-fee"
            else golden
        )
        for golden in goldens
    ]
    record = run_exam(
        broken,
        adapter=adapter,
        model=GoldenCannedModel(broken),
        reranker=CrossEncoderReranker(),
    )
    by_id = {result["id"]: result for result in record["results"]}
    assert by_id["broker-fee"]["recall"] is False
    assert by_id["broker-fee"]["passed"] is False
    assert by_id["application-fee"]["passed"] is True


def test_the_exam_runs_every_golden_through_the_model():
    adapter = PgAdapter()
    run(adapter)
    goldens = load_goldens()
    exam_model = GoldenCannedModel(goldens)
    run_exam(
        goldens,
        adapter=adapter,
        model=exam_model,
        reranker=CrossEncoderReranker(),
    )
    assert len(exam_model.prompts) == 10


def test_the_exact_phrase_goldens_are_recalled_by_hybrid_search():
    adapter = PgAdapter()
    run(adapter)
    for golden_id in ("grace-period-heading", "minimum-payment-warning"):
        golden = _golden(golden_id)
        rows = search(golden.question, adapter)
        ranked = [chunk["chunk_id"] for chunk in CrossEncoderReranker().rank(golden.question, rows)]
        assert recall_pass(ranked, golden), golden_id


def test_vector_mode_disables_the_keyword_lane_and_drops_the_exact_term_rescue():
    adapter = PgAdapter()
    run(adapter)
    question = "What is the annual sprocket allowance of 250 dollars?"
    opposite = [-value for value in embed_texts([question])[0]]
    with adapter.connect() as conn:
        conn.execute(
            """
            INSERT INTO policy_chunks (
                chunk_id, source_doc, section, section_title, effective_date,
                superseded_by, parent_text, text, embedding
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "test_sprocket_allowance:s1:c01",
                "test_sprocket_allowance.md",
                "1",
                "Sprocket Allowance",
                None,
                None,
                "The allowance budget is fixed.",
                "The annual sprocket allowance of 250 dollars covers morning snacks.",
                opposite,
            ),
        )
    try:
        hybrid_rows = search(question, adapter)
        vector_rows = search(question, adapter, mode="vector")
        ranker = CrossEncoderReranker()
        hybrid_five = [chunk["chunk_id"] for chunk in ranker.rank(question, hybrid_rows)]
        assert any(row["chunk_id"] == "test_sprocket_allowance:s1:c01" for row in hybrid_rows)
        assert "test_sprocket_allowance:s1:c01" in hybrid_five
        assert all(row["chunk_id"] != "test_sprocket_allowance:s1:c01" for row in vector_rows)
    finally:
        with adapter.connect() as conn:
            conn.execute(
                "DELETE FROM policy_chunks WHERE chunk_id = %s",
                ("test_sprocket_allowance:s1:c01",),
            )


def test_eval_prints_the_table_and_writes_the_record(tmp_path, capsys):
    adapter = PgAdapter()
    run(adapter)
    goldens = load_goldens()
    output_path = tmp_path / "record.json"

    exit_code = main(
        ["eval", "--output", str(output_path)],
        database_adapter=adapter,
        language_model=GoldenCannedModel(goldens),
        reranker=CrossEncoderReranker(),
    )

    assert exit_code == 0
    table = capsys.readouterr().out
    assert "retriever=hybrid" in table
    assert "passed=10" in table
    for golden in goldens:
        assert golden.id in table
    record = json.loads(output_path.read_text())
    assert record["summary"]["passed"] == 10
    assert record["summary"]["total"] == 10


def test_eval_exits_nonzero_when_a_golden_answer_misses_its_required_fact(tmp_path, capsys):
    adapter = PgAdapter()
    run(adapter)
    goldens = load_goldens()
    output_path = tmp_path / "record.json"

    exit_code = main(
        ["eval", "--output", str(output_path)],
        database_adapter=adapter,
        language_model=GoldenCannedModel(goldens, wrong_for="minimum-interest-charge"),
        reranker=CrossEncoderReranker(),
    )

    assert exit_code == 1
    table = capsys.readouterr().out
    assert "minimum-interest-charge" in table
    assert "$1.00" in table
    record = json.loads(output_path.read_text())
    by_id = {result["id"]: result for result in record["results"]}
    assert by_id["minimum-interest-charge"]["passed"] is False


def test_the_record_table_lists_every_failure_detail():
    record = {
        "retriever": "vector",
        "results": [
            {
                "id": "minimum-interest-charge",
                "kind": "answer",
                "recall": True,
                "answer_failures": ["missing required fact '$1.00'"],
                "passed": False,
            }
        ],
        "summary": {
            "total": 1,
            "passed": 0,
            "recall_hits": 1,
            "answer_passes": 0,
        },
    }
    table = format_table(record)
    assert "retriever=vector" in table
    assert "minimum-interest-charge" in table
    assert "FAIL" in table
    assert "missing required fact '$1.00'" in table


def test_the_reranker_returns_nothing_when_there_are_no_candidates():
    assert CrossEncoderReranker().rank("any question", []) == []


def test_generation_refuses_without_calling_the_model_when_retrieval_is_empty():
    from app.generation.generate import generate

    class ExplodingModel:
        def chat(self, prompt):
            raise AssertionError("the model must not be called")

    response = generate("What is the mileage rate?", [], ExplodingModel())
    assert response.answer == REFUSAL
    assert response.citation is None
    assert response.retrieved_chunks == []
