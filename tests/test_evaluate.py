"""Golden exam and evaluation harness behavior at their public seams."""

import pytest

from app.evaluate import Golden, load_goldens
from app.ingest import run
from app.pgadapter import PgAdapter

EXPECTED_EXAM = {
    "per-diem": "answer",
    "mileage-rate": "answer",
    "submission-window": "answer",
    "mid-approval-tier": "answer",
    "villain-protocol": "hybrid_demo",
    "per-diem-poisoned": "poisoned",
    "home-office-stipend": "answer",
    "bananaphone": "refusal",
    "top-approval-tier": "answer",
}


def test_the_golden_file_holds_the_nine_case_exam():
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
