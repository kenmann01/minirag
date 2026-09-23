import app.chunking as chunking
from app.chunking import split

_SHORT_POLICY = """\
**Effective Date:** January 15, 2024

## 1. Purpose

Meals cost sixty-five dollars per day.
"""


def _rule(number: int) -> str:
    prefix = f"Rule {number} binds every claim "
    return prefix + ("a" * (400 - len(prefix) - 1)) + "."


_RULES = [_rule(number) for number in range(1, 5)]
_LONG_POLICY = f"""\
**Effective Date:** June 1, 2021

## 5. Travel Expenses

{" ".join(_RULES)}
"""


def test_long_section_overlaps_on_whole_rules():
    chunks = split(_LONG_POLICY, "sample_policy.md")

    assert [chunk["chunk_id"] for chunk in chunks] == [
        "sample_policy:s5:c01",
        "sample_policy:s5:c02",
        "sample_policy:s5:c03",
    ]
    assert chunks[0]["text"] == f"{_RULES[0]} {_RULES[1]}"
    assert chunks[1]["text"] == f"{_RULES[1]} {_RULES[2]}"
    assert chunks[2]["text"] == f"{_RULES[2]} {_RULES[3]}"
    assert all(chunk["parent_text"] == " ".join(_RULES) for chunk in chunks)
    assert all(800 <= len(chunk["text"]) <= 1200 for chunk in chunks)
    assert chunks[1]["text"].startswith(_RULES[1])


def test_chunking_docstring_states_the_window_and_why_not_flat():
    docstring = chunking.__doc__
    assert docstring is not None
    assert "800-1200" in docstring or "800–1200" in docstring
    assert "120-150" in docstring or "120–150" in docstring
    assert "500" in docstring
    assert "50" in docstring
    assert "shatter" in docstring.lower()


def test_short_section_is_one_child_carrying_lineage():
    chunks = split(_SHORT_POLICY, "minion_expense_policy_2024.md")

    assert chunks == [
        {
            "chunk_id": "minion_expense_policy_2024:s1:c01",
            "source_doc": "minion_expense_policy_2024.md",
            "section": "1",
            "section_title": "Purpose",
            "effective_date": "January 15, 2024",
            "superseded_by": None,
            "parent_text": "Meals cost sixty-five dollars per day.",
            "text": "Meals cost sixty-five dollars per day.",
        }
    ]
