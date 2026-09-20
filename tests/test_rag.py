"""Ingest/store seam: ingest makes policy chunks retrievable from the store."""

import pytest

from app.ingest import run
from app.pgadapter import PgAdapter
from app.retrieve import search


def test_ingest_stores_six_policy_chunks():
    adapter = PgAdapter()
    run(adapter)
    with adapter.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM policy_chunks").fetchone()[0]
    assert count == 6


def test_ingest_stores_meals_chunk_metadata():
    adapter = PgAdapter()
    run(adapter)
    with adapter.connect() as conn:
        row = conn.execute(
            """
            SELECT chunk_id, document, version, section, section_title, text
            FROM policy_chunks
            WHERE section = %s
            """,
            ("1",),
        ).fetchone()
    assert row == (
        "expense-policy:v2.0:section-1",
        "Employee Expense Policy",
        "2.0",
        "1",
        "Meals",
        "Employees may claim up to $65 per day for meals while traveling overnight.\n"
        "Alcohol is not reimbursable.",
    )


def test_ingest_stores_384_dimension_embeddings():
    adapter = PgAdapter()
    run(adapter)
    with adapter.connect() as conn:
        row = conn.execute(
            "SELECT embedding FROM policy_chunks WHERE section = %s",
            ("1",),
        ).fetchone()
    assert row is not None
    assert len(row[0].to_list()) == 384


def test_retrieve_returns_one_meals_chunk_for_food_question():
    adapter = PgAdapter()
    run(adapter)
    rows = search("How much can I spend on food each day?", adapter)
    assert len(rows) == 1
    assert rows[0]["section"] == "1"
    assert rows[0]["section_title"] == "Meals"


@pytest.mark.parametrize(
    ("question", "section", "title"),
    [
        ("Can I book first-class airfare?", "3", "Airfare"),
        ("My hotel costs $250. What do I need?", "2", "Hotels"),
        ("Do I need a receipt for a $20 taxi?", "5", "Receipts"),
        ("Can I claim a limousine upgrade?", "4", "Ground Transportation"),
    ],
)
def test_retrieve_returns_expected_section_for_in_policy_question(question, section, title):
    adapter = PgAdapter()
    run(adapter)
    rows = search(question, adapter)
    assert len(rows) == 1
    assert rows[0]["section"] == section
    assert rows[0]["section_title"] == title


def test_reingest_upserts_and_keeps_six_chunks():
    adapter = PgAdapter()
    run(adapter)
    with adapter.connect() as conn:
        conn.execute(
            "UPDATE policy_chunks SET text = %s WHERE section = %s",
            ("changed", "1"),
        )
    run(adapter)
    with adapter.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM policy_chunks").fetchone()[0]
        text = conn.execute(
            "SELECT text FROM policy_chunks WHERE section = %s",
            ("1",),
        ).fetchone()[0]
    assert count == 6
    assert "65" in text
