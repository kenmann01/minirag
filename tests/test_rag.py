"""Ingest/store and Ask CLI behavior at their public seams."""

import json

import pytest
from pydantic import ValidationError

from app.cli import main
from app.ingest import run
from app.pgadapter import PgAdapter
from app.retrieve import search


class FakeLanguageModel:
    def __init__(self, response: str):
        self.response = response
        self.prompts = []

    def chat(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


class SequenceLanguageModel:
    def __init__(self, responses: list[str]):
        self.responses = iter(responses)

    def chat(self, prompt: str) -> str:
        return next(self.responses)


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


def test_retrieve_returns_three_chunks_for_food_question_in_distance_order():
    adapter = PgAdapter()
    run(adapter)
    rows = search("How much can I spend on food each day?", adapter)
    assert len(rows) == 3
    assert rows[0]["section"] == "1"
    assert rows[0]["section_title"] == "Meals"
    assert [row["distance"] for row in rows] == sorted(
        row["distance"] for row in rows
    )


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
    assert len(rows) == 3
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


def test_employee_can_ask_about_meals_and_receive_grounded_json(capsys):
    adapter = PgAdapter()
    run(adapter)
    model = FakeLanguageModel(
        json.dumps(
            {
                "answer": "You may claim up to $65 per day for meals while traveling overnight.",
                "section": "1. Meals",
            }
        )
    )

    exit_code = main(
        ["ask", "How much can I spend on food each day?"],
        database_adapter=adapter,
        language_model=model,
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == (
        "You may claim up to $65 per day for meals while traveling overnight."
    )
    assert output["citation"] == {
        "document": "Employee Expense Policy",
        "version": "2.0",
        "section": "1. Meals",
    }
    assert len(output["retrieved_chunks"]) == 3
    assert all(
        isinstance(chunk["distance"], float)
        for chunk in output["retrieved_chunks"]
    )


def test_ask_omits_citation_when_model_names_a_different_section(capsys):
    adapter = PgAdapter()
    run(adapter)
    model = FakeLanguageModel(
        json.dumps(
            {
                "answer": "You may claim up to $65 per day.",
                "section": "99. Missing",
            }
        )
    )

    main(
        ["ask", "How much can I spend on food each day?"],
        database_adapter=adapter,
        language_model=model,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "The provided policy does not answer this question."
    assert output["citation"] is None


def test_ask_uses_host_mistral_through_ollama(capsys, monkeypatch):
    adapter = PgAdapter()
    run(adapter)
    request_sent = {}

    class FakeHTTPResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "answer": "You may claim up to $65 per day.",
                                "section": "1. Meals",
                            }
                        )
                    }
                }
            ).encode()

    def fake_urlopen(request):
        request_sent["url"] = request.full_url
        request_sent["body"] = json.loads(request.data)
        return FakeHTTPResponse()

    monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    main(
        ["ask", "How much can I spend on food each day?"],
        database_adapter=adapter,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["citation"]["section"] == "1. Meals"
    assert request_sent == {
        "url": "http://host.docker.internal:11434/api/chat",
        "body": {
            "model": "mistral",
            "messages": [
                {
                    "role": "user",
                    "content": request_sent["body"]["messages"][0]["content"],
                }
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0, "seed": 42},
        },
    }


@pytest.mark.parametrize(
    ("question", "answer", "section"),
    [
        (
            "How much can I spend on food each day?",
            "Meals are reimbursable up to $65 per day while traveling overnight.",
            "1. Meals",
        ),
        (
            "Can I book first-class airfare?",
            "You must buy economy airfare; business class requires written VP approval.",
            "3. Airfare",
        ),
        (
            "My hotel costs $250. What do I need?",
            "A manager must approve a rate over $225 before booking.",
            "2. Hotels",
        ),
        (
            "Do I need a receipt for a $20 taxi?",
            "No. Receipts are required for expenses of $25 or more.",
            "5. Receipts",
        ),
        (
            "Can I claim a limousine upgrade?",
            "No. Luxury vehicle upgrades are not reimbursable.",
            "4. Ground Transportation",
        ),
    ],
)
def test_employee_receives_expected_policy_answer_and_citation(
    question, answer, section, capsys
):
    adapter = PgAdapter()
    run(adapter)
    model = FakeLanguageModel(json.dumps({"answer": answer, "section": section}))

    main(
        ["ask", question],
        database_adapter=adapter,
        language_model=model,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == answer
    assert output["citation"] == {
        "document": "Employee Expense Policy",
        "version": "2.0",
        "section": section,
    }
    assert len(output["retrieved_chunks"]) == 3


def test_generator_receives_all_three_retrieved_policy_excerpts(capsys):
    adapter = PgAdapter()
    run(adapter)
    model = FakeLanguageModel(
        json.dumps(
            {
                "answer": "You must buy economy airfare.",
                "section": "3. Airfare",
            }
        )
    )

    main(
        ["ask", "Can I book first-class airfare?"],
        database_adapter=adapter,
        language_model=model,
    )
    output = json.loads(capsys.readouterr().out)

    assert len(model.prompts) == 1
    assert "Employees must purchase economy airfare." in model.prompts[0]
    assert "using only the policy excerpts" in model.prompts[0]
    assert "requires one option" in model.prompts[0]
    assert "specific item fits a broader prohibited category" in model.prompts[0]
    assert "Cite the section containing the rule" in model.prompts[0]
    assert "every claim in the answer must be supported" in model.prompts[0]
    assert len(output["retrieved_chunks"]) == 3
    assert all(
        chunk["text"] in model.prompts[0] for chunk in output["retrieved_chunks"]
    )


def test_ask_rejects_invalid_model_json_without_printing_partial_output(capsys):
    adapter = PgAdapter()
    run(adapter)
    model = FakeLanguageModel('{"answer": "Use economy airfare."}')

    with pytest.raises(ValidationError):
        main(
            ["ask", "Can I book first-class airfare?"],
            database_adapter=adapter,
            language_model=model,
        )

    assert capsys.readouterr().out == ""


def test_employee_receives_exact_refusal_without_citation_for_gym_memberships(capsys):
    adapter = PgAdapter()
    run(adapter)
    model = FakeLanguageModel(
        json.dumps(
            {
                "answer": "The provided policy does not answer this question.",
                "section": "3. Airfare",
            }
        )
    )

    main(
        ["ask", "Does the company reimburse gym memberships?"],
        database_adapter=adapter,
        language_model=model,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "The provided policy does not answer this question."
    assert output["citation"] is None
    assert len(output["retrieved_chunks"]) == 3
    assert all(
        isinstance(chunk["distance"], float)
        for chunk in output["retrieved_chunks"]
    )


def test_gym_question_instructs_model_to_use_the_exact_refusal(capsys):
    adapter = PgAdapter()
    run(adapter)
    refusal = "The provided policy does not answer this question."
    model = FakeLanguageModel(
        json.dumps({"answer": refusal, "section": "3. Airfare"})
    )

    main(
        ["ask", "Does the company reimburse gym memberships?"],
        database_adapter=adapter,
        language_model=model,
    )
    capsys.readouterr()

    assert f"answer exactly: {refusal}" in model.prompts[0]


def test_eval_writes_a_replayable_record_of_all_six_questions(tmp_path):
    adapter = PgAdapter()
    run(adapter)
    refusal = "The provided policy does not answer this question."
    model = SequenceLanguageModel(
        [
            json.dumps({"answer": "$65 per day.", "section": "1. Meals"}),
            json.dumps({"answer": "Buy economy airfare.", "section": "3. Airfare"}),
            json.dumps({"answer": "Get manager approval.", "section": "2. Hotels"}),
            json.dumps({"answer": "No receipt is needed.", "section": "5. Receipts"}),
            json.dumps(
                {
                    "answer": "Luxury upgrades are not reimbursable.",
                    "section": "4. Ground Transportation",
                }
            ),
            json.dumps({"answer": refusal, "section": "3. Airfare"}),
        ]
    )
    output_path = tmp_path / "output.json"

    exit_code = main(
        ["eval", "--output", str(output_path)],
        database_adapter=adapter,
        language_model=model,
    )

    assert exit_code == 0
    results = json.loads(output_path.read_text())
    assert [result["question"] for result in results] == [
        "How much can I spend on food each day?",
        "Can I book first-class airfare?",
        "My hotel costs $250. What do I need?",
        "Do I need a receipt for a $20 taxi?",
        "Can I claim a limousine upgrade?",
        "Does the company reimburse gym memberships?",
    ]
    assert [result["citation"]["section"] if result["citation"] else None for result in results] == [
        "1. Meals",
        "3. Airfare",
        "2. Hotels",
        "5. Receipts",
        "4. Ground Transportation",
        None,
    ]
    assert results[-1]["answer"] == refusal
    assert all(len(result["retrieved_chunks"]) == 3 for result in results)
    assert all(
        isinstance(result["retrieved_chunks"][0]["distance"], float)
        for result in results
    )
