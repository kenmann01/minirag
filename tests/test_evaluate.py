"""Golden exam and evaluation harness behavior at their public seams."""

import json

import pytest

from app.cli import main
from app.generate import PROMPT_PATH
from app.embeddings import embed_texts
from app.evaluate import (
    Golden,
    answer_failures,
    format_table,
    load_goldens,
    recall_pass,
    run_exam,
    section_prefix_hit,
)
from app.ingest import run
from app.pgadapter import PgAdapter
from app.reranker import CrossEncoderReranker
from app.retrieve import search
from app.schemas import AskResponse, Citation
from app.validate import REFUSAL

EXPECTED_EXAM = {
    "per-diem": "answer",
    "mileage-rate": "answer",
    "submission-window": "answer",
    "mid-approval-tier": "answer",
    "villain-protocol": "hybrid_demo",
    "form-zx-4491": "hybrid_demo",
    "per-diem-poisoned": "poisoned",
    "home-office-stipend": "answer",
    "bananaphone": "refusal",
    "top-approval-tier": "answer",
}


def test_the_golden_file_holds_the_ten_case_exam():
    goldens = load_goldens()
    assert {golden.id: golden.kind for golden in goldens} == EXPECTED_EXAM
    assert all(golden.question.strip().endswith("?") for golden in goldens)
    assert all(golden.expected for golden in goldens if golden.kind != "refusal")
    assert not next(g for g in goldens if g.id == "bananaphone").expected


def test_the_poisoned_golden_demands_the_current_number_and_forbids_the_stale_one():
    poisoned = next(g for g in load_goldens() if g.id == "per-diem-poisoned")
    assert "$75" in poisoned.must_contain
    assert "$60" in poisoned.must_not_contain


def test_the_refusal_golden_targets_an_uncovered_product():
    refusal = next(g for g in load_goldens() if g.kind == "refusal")
    assert refusal.must_contain == [
        "The provided policy does not answer this question."
    ]


def test_every_expected_section_exists_in_the_ingested_table():
    adapter = PgAdapter()
    run(adapter)
    with adapter.connect() as conn:
        chunk_ids = {
            row[0] for row in conn.execute("SELECT chunk_id FROM policy_chunks")
        }
    for golden in load_goldens():
        for expected_section in golden.expected:
            assert any(
                chunk_id.startswith(f"{expected_section}:")
                for chunk_id in chunk_ids
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
    assert section_prefix_hit(
        "minion_expense_policy_2024:s5:c01", ["minion_expense_policy_2024:s5"]
    )
    assert section_prefix_hit(
        "minion_expense_policy_2024:s5:c03", ["minion_expense_policy_2024:s5"]
    )


def test_the_prefix_matcher_rejects_other_sections_and_false_prefixes():
    assert not section_prefix_hit(
        "minion_travel_security_policy:s6:c01", ["minion_expense_policy_2024:s5"]
    )
    assert not section_prefix_hit(
        "minion_expense_policy_2024:s51:c01", ["minion_expense_policy_2024:s5"]
    )


def test_recall_pass_counts_any_expected_child_inside_the_ranked_five():
    per_diem = _golden("per-diem")
    five_without = [
        "minion_expense_policy_2024:s2:c01",
        "minion_pto_policy:s2:c01",
        "minion_remote_work_policy:s5:c01",
        "minion_travel_security_policy:s4:c01",
        "minion_equipment_policy:s1:c01",
    ]
    assert not recall_pass(five_without, per_diem)
    assert recall_pass(five_without[:4] + ["minion_expense_policy_2024:s5:c02"], per_diem)


def test_recall_pass_is_automatic_when_nothing_is_expected():
    refusal = _golden("bananaphone")
    assert recall_pass([], refusal)


def test_answer_checks_pass_a_canned_good_answer():
    response = AskResponse(
        answer="Domestic travel meals are $75 per day.",
        citation=Citation(
            source_doc="minion_expense_policy_2024.md",
            effective_date="January 15, 2024",
            section="minion_expense_policy_2024.md 5. Travel Expenses",
        ),
        retrieved_chunks=[],
    )
    assert answer_failures(response, _golden("per-diem")) == []


def test_answer_checks_fail_an_answer_quoting_the_stale_number():
    response = AskResponse(
        answer="Meals are reimbursed at $60 per day domestic.",
        citation=None,
        retrieved_chunks=[],
    )
    failures = answer_failures(response, _golden("per-diem-poisoned"))
    assert any("$60" in failure for failure in failures)
    assert any("$75" in failure for failure in failures)


def test_the_refusal_check_demands_the_exact_refusal_without_a_citation():
    refusal = _golden("bananaphone")
    paraphrase = AskResponse(
        answer="Sorry, the policy has nothing on bananaphones.",
        citation=None,
        retrieved_chunks=[],
    )
    cited = AskResponse(
        answer=REFUSAL,
        citation=Citation(
            source_doc="minion_communications_policy.md",
            effective_date="February 10, 2024",
            section="minion_communications_policy.md 2. Official Channels",
        ),
        retrieved_chunks=[],
    )
    exact = AskResponse(answer=REFUSAL, citation=None, retrieved_chunks=[])
    assert answer_failures(paraphrase, refusal)
    assert answer_failures(cited, refusal)
    assert answer_failures(exact, refusal) == []


class GoldenCannedModel:
    """Answers each golden from its own facts, citing the top-ranked section."""

    def __init__(self, goldens: list[Golden], stale_for: str | None = None):
        self.goldens = goldens
        self.stale_for = stale_for
        self.prompts: list[str] = []

    def chat(self, prompt: str) -> str:
        self.prompts.append(prompt)
        asked = prompt.rsplit("\nQuestion:\n", 1)[-1]
        golden = next(g for g in self.goldens if g.question in asked)
        if golden.kind == "refusal":
            return json.dumps({"answer": REFUSAL, "section": ""})
        excerpts = prompt.rsplit("\nPolicy excerpts:\n", 1)[-1].split(
            "\nQuestion:\n", 1
        )[0]
        label = excerpts.split("Section: ", 1)[1].split("\n", 1)[0]
        if golden.id == self.stale_for:
            return json.dumps(
                {
                    "answer": f"The 2021 policy allowed ${'60'} per day domestic.",
                    "section": label,
                }
            )
        return json.dumps(
            {"answer": " ".join(golden.must_contain), "section": label}
        )


def test_the_canned_model_cites_the_retrieved_section_not_the_few_shot_example():
    golden = _golden("per-diem")
    prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace(
            "{excerpts}",
            "Section: minion_expense_policy_2024.md 3. Meals\nExcerpt:\nDomestic per diem is $75.",
        )
        .replace("{question}", golden.question)
    )
    reply = json.loads(GoldenCannedModel([golden]).chat(prompt))
    assert reply["section"] == "minion_expense_policy_2024.md 3. Meals"
    assert "$75" in reply["answer"]


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
    assert by_id["villain-protocol"]["recall"] is True
    assert by_id["form-zx-4491"]["recall"] is True
    assert any(
        chunk_id.startswith("minion_travel_security_policy:s6:")
        for chunk_id in by_id["villain-protocol"]["ranked_chunk_ids"]
    )


def test_the_exam_fails_on_an_induced_regression():
    adapter = PgAdapter()
    run(adapter)
    goldens = load_goldens()
    broken = [
        golden.model_copy(update={"expected": ["minion_visitors_policy:s5"]})
        if golden.id == "top-approval-tier"
        else golden
        for golden in goldens
    ]
    record = run_exam(
        broken,
        adapter=adapter,
        model=GoldenCannedModel(broken),
        reranker=CrossEncoderReranker(),
    )
    by_id = {result["id"]: result for result in record["results"]}
    assert by_id["top-approval-tier"]["recall"] is False
    assert by_id["top-approval-tier"]["passed"] is False
    assert by_id["per-diem"]["passed"] is True


def test_the_exam_runs_every_golden_through_the_model_even_when_cached(capsys):
    adapter = PgAdapter()
    run(adapter)
    goldens = load_goldens()
    stored = GoldenCannedModel(goldens)
    main(
        ["ask", goldens[0].question],
        database_adapter=adapter,
        language_model=stored,
        reranker=CrossEncoderReranker(),
    )
    capsys.readouterr()
    exam_model = GoldenCannedModel(goldens)
    run_exam(
        goldens,
        adapter=adapter,
        model=exam_model,
        reranker=CrossEncoderReranker(),
    )
    assert len(exam_model.prompts) == 10


def test_form_zx_4491_is_retrieved_by_hybrid_and_missed_by_vector_only():
    adapter = PgAdapter()
    run(adapter)
    golden = _golden("form-zx-4491")
    hybrid_rows = search(golden.question, adapter)
    vector_rows = search(golden.question, adapter, mode="vector")
    hybrid_ids = [
        chunk["chunk_id"]
        for chunk in CrossEncoderReranker().rank(golden.question, hybrid_rows)
    ]
    assert recall_pass(hybrid_ids, golden)
    assert any(
        chunk["chunk_id"].startswith("minion_dress_code_policy:s11:")
        for chunk in hybrid_rows
    )
    assert all(
        not chunk["chunk_id"].startswith("minion_dress_code_policy:s11:")
        for chunk in vector_rows
    )


def test_vector_mode_disables_the_keyword_lane_and_drops_the_exact_term_rescue():
    adapter = PgAdapter()
    run(adapter)
    question = "What is the zagat-grade biscuit budget of 250 dollars?"
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
                "test_zagat_biscuit:s1:c01",
                "test_zagat_biscuit.md",
                "1",
                "Biscuit Budget",
                None,
                None,
                "The snack budget is fixed.",
                "The zagat-grade biscuit budget of 250 dollars covers morning snacks.",
                opposite,
            ),
        )
    try:
        hybrid_rows = search(question, adapter)
        vector_rows = search(question, adapter, mode="vector")
        ranker = CrossEncoderReranker()
        hybrid_five = [
            chunk["chunk_id"] for chunk in ranker.rank(question, hybrid_rows)
        ]
        assert any(
            row["chunk_id"] == "test_zagat_biscuit:s1:c01" for row in hybrid_rows
        )
        assert "test_zagat_biscuit:s1:c01" in hybrid_five
        assert all(
            row["chunk_id"] != "test_zagat_biscuit:s1:c01" for row in vector_rows
        )
    finally:
        with adapter.connect() as conn:
            conn.execute(
                "DELETE FROM policy_chunks WHERE chunk_id = %s",
                ("test_zagat_biscuit:s1:c01",),
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


def test_eval_exits_nonzero_when_a_golden_quotes_the_stale_number(tmp_path, capsys):
    adapter = PgAdapter()
    run(adapter)
    goldens = load_goldens()
    output_path = tmp_path / "record.json"

    exit_code = main(
        ["eval", "--output", str(output_path)],
        database_adapter=adapter,
        language_model=GoldenCannedModel(goldens, stale_for="per-diem-poisoned"),
        reranker=CrossEncoderReranker(),
    )

    assert exit_code == 1
    table = capsys.readouterr().out
    assert "per-diem-poisoned" in table
    assert "$60" in table
    record = json.loads(output_path.read_text())
    by_id = {result["id"]: result for result in record["results"]}
    assert by_id["per-diem-poisoned"]["passed"] is False


def test_eval_with_the_lineage_bypass_admits_stale_chunks_into_the_exam(tmp_path):
    adapter = PgAdapter()
    run(adapter)
    goldens = load_goldens()
    output_path = tmp_path / "record-bypass.json"

    exit_code = main(
        ["eval", "--include-superseded", "--output", str(output_path)],
        database_adapter=adapter,
        language_model=GoldenCannedModel(goldens),
        reranker=CrossEncoderReranker(),
    )

    assert exit_code == 0
    record = json.loads(output_path.read_text())
    assert record["include_superseded"] is True
    every_id = [
        chunk_id
        for result in record["results"]
        for chunk_id in result["ranked_chunk_ids"]
    ]
    assert any(
        chunk_id.startswith("minion_expense_policy_2021:") for chunk_id in every_id
    )


def test_eval_without_the_bypass_keeps_stale_chunks_out_of_the_exam(tmp_path):
    adapter = PgAdapter()
    run(adapter)
    goldens = load_goldens()
    output_path = tmp_path / "record.json"

    main(
        ["eval", "--output", str(output_path)],
        database_adapter=adapter,
        language_model=GoldenCannedModel(goldens),
        reranker=CrossEncoderReranker(),
    )

    record = json.loads(output_path.read_text())
    assert record["include_superseded"] is False
    every_id = [
        chunk_id
        for result in record["results"]
        for chunk_id in result["ranked_chunk_ids"]
    ]
    assert not any(
        chunk_id.startswith("minion_expense_policy_2021:") for chunk_id in every_id
    )


def test_the_record_table_lists_every_failure_detail():
    record = {
        "retriever": "vector",
        "results": [
            {
                "id": "per-diem-poisoned",
                "kind": "poisoned",
                "recall": True,
                "answer_failures": ["forbidden fact present '$60'"],
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
    assert "per-diem-poisoned" in table
    assert "FAIL" in table
    assert "forbidden fact present '$60'" in table
