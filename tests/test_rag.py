"""Ingest/store and Ask CLI behavior at their public seams."""

import json

import pytest
from pydantic import ValidationError

from app.cli import EVAL_QUESTIONS, main
from app.generate import PROMPT_PATH, section_label
from app.ingest import run
from app.pgadapter import PgAdapter
from app.retrieve import search


@pytest.fixture(autouse=True)
def empty_question_cache():
    adapter = PgAdapter()
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS question_cache")
    yield


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


def test_ingest_stores_2024_purpose_as_a_768_child():
    adapter = PgAdapter()
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS policy_chunks")
        conn.execute(
            """
            CREATE TABLE policy_chunks (
                chunk_id TEXT PRIMARY KEY,
                document TEXT NOT NULL,
                version TEXT NOT NULL,
                section TEXT NOT NULL,
                section_title TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding vector(384) NOT NULL
            )
            """
        )
    run(adapter)
    with adapter.connect() as conn:
        row = conn.execute(
            """
            SELECT chunk_id, source_doc, section, section_title, effective_date,
                   superseded_by, parent_text, text, embedding
            FROM policy_chunks
            WHERE chunk_id = %s
            """,
            ("minion_expense_policy_2024:s1:c01",),
        ).fetchone()
    assert row is not None
    assert row[0] == "minion_expense_policy_2024:s1:c01"
    assert row[1] == "minion_expense_policy_2024.md"
    assert row[2] == "1"
    assert row[3] == "Purpose"
    assert row[4] == "January 15, 2024"
    assert row[5] is None
    purpose = (
        "This policy explains what lair will pay Minion back for, and what lair "
        "will absolutely not, no matter how good the reason sounded at 2 AM when "
        "Minion thought of it."
    )
    assert row[6] == purpose
    assert row[7] == purpose
    assert len(row[8].to_list()) == 768


def test_ingest_stores_every_policy_in_the_folder():
    adapter = PgAdapter()
    run(adapter)
    with adapter.connect() as conn:
        sources = {
            row[0]
            for row in conn.execute("SELECT DISTINCT source_doc FROM policy_chunks")
        }
    assert sources == {
        "minion_expense_policy_2021.md",
        "minion_expense_policy_2024.md",
        "minion_pto_policy.md",
        "minion_remote_work_policy.md",
    }


def test_ingest_marks_the_2021_expense_policy_superseded():
    adapter = PgAdapter()
    run(adapter)
    with adapter.connect() as conn:
        pointers = {
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT superseded_by
                FROM policy_chunks
                WHERE source_doc = %s
                """,
                ("minion_expense_policy_2021.md",),
            )
        }
        other_pointers = conn.execute(
            """
            SELECT COUNT(*) FROM policy_chunks
            WHERE source_doc <> %s AND superseded_by IS NOT NULL
            """,
            ("minion_expense_policy_2021.md",),
        ).fetchone()[0]
    assert pointers == {"minion_expense_policy_2024.md"}
    assert other_pointers == 0


def test_ingest_keeps_a_prose_effective_date():
    adapter = PgAdapter()
    run(adapter)
    with adapter.connect() as conn:
        dates = {
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT effective_date
                FROM policy_chunks
                WHERE source_doc = %s
                """,
                ("minion_remote_work_policy.md",),
            )
        }
    assert dates == {"the day after Kevin flooded the sub-basement"}


def test_reingest_drops_rows_it_did_not_see():
    adapter = PgAdapter()
    run(adapter)
    with adapter.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM policy_chunks").fetchone()[0]
        conn.execute(
            """
            INSERT INTO policy_chunks (
                chunk_id, source_doc, section, section_title, effective_date,
                superseded_by, parent_text, text, embedding
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "orphan:s0:c01",
                "orphan.md",
                "0",
                "Orphan",
                None,
                None,
                "parent",
                "child",
                [0.0] * 768,
            ),
        )
    run(adapter)
    with adapter.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM policy_chunks").fetchone()[0]
        orphan = conn.execute(
            "SELECT 1 FROM policy_chunks WHERE chunk_id = %s",
            ("orphan:s0:c01",),
        ).fetchone()
    assert orphan is None
    assert count == before


def test_ingest_command_prints_how_many_children_it_wrote(capsys):
    adapter = PgAdapter()
    exit_code = main(["ingest"], database_adapter=adapter)
    assert exit_code == 0
    with adapter.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM policy_chunks").fetchone()[0]
    assert capsys.readouterr().err.strip() == f"ingested {count} policy chunks"


def test_retrieve_returns_three_current_sections_in_distance_order():
    adapter = PgAdapter()
    run(adapter)
    rows = search("How much can I spend on food each day?", adapter)
    assert len(rows) == 3
    assert rows[0]["source_doc"] == "minion_expense_policy_2024.md"
    assert rows[0]["section"] == "5"
    assert rows[0]["section_title"] == "Travel Expenses"
    assert "$75/day" in rows[0]["text"]
    assert all(row["source_doc"] != "minion_expense_policy_2021.md" for row in rows)
    assert [row["distance"] for row in rows] == sorted(
        row["distance"] for row in rows
    )


def test_search_returns_one_row_when_two_children_share_a_section():
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    with adapter.connect() as conn:
        embedding = conn.execute(
            "SELECT embedding FROM policy_chunks WHERE chunk_id = %s",
            ("minion_expense_policy_2024:s5:c01",),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO policy_chunks (
                chunk_id, source_doc, section, section_title, effective_date,
                superseded_by, parent_text, text, embedding
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "minion_expense_policy_2024:s5:c02",
                "minion_expense_policy_2024.md",
                "5",
                "Travel Expenses",
                "January 15, 2024",
                None,
                "whole travel section",
                "second child window",
                embedding,
            ),
        )
    rows = search(question, adapter)
    travel = [
        row
        for row in rows
        if row["source_doc"] == "minion_expense_policy_2024.md" and row["section"] == "5"
    ]
    assert len(rows) == 3
    assert len(travel) == 1
    assert len({(row["source_doc"], row["section"]) for row in rows}) == 3


def test_employee_can_ask_about_meals_and_receive_grounded_json(capsys):
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

    exit_code = main(
        ["ask", question],
        database_adapter=adapter,
        language_model=model,
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "Domestic travel meals are $75 per day."
    assert output["citation"] == {
        "source_doc": "minion_expense_policy_2024.md",
        "effective_date": "January 15, 2024",
        "section": label,
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
    question = "How much can I spend on food each day?"
    label = section_label(search(question, adapter)[0])
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
                                "answer": "You may claim up to $75 per day.",
                                "section": label,
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
        ["ask", question],
        database_adapter=adapter,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["citation"]["section"] == label
    assert output["citation"]["source_doc"] == "minion_expense_policy_2024.md"
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


@pytest.mark.parametrize("question", EVAL_QUESTIONS[:-1])
def test_ask_cites_the_current_section_the_model_names(question, capsys):
    adapter = PgAdapter()
    run(adapter)
    top = search(question, adapter)[0]
    label = section_label(top)
    model = FakeLanguageModel(
        json.dumps({"answer": "Cited from the policy.", "section": label})
    )

    main(
        ["ask", question],
        database_adapter=adapter,
        language_model=model,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "Cited from the policy."
    assert output["citation"] == {
        "source_doc": top["source_doc"],
        "effective_date": top["effective_date"],
        "section": label,
    }
    assert len(output["retrieved_chunks"]) == 3
    assert all(
        chunk["source_doc"] != "minion_expense_policy_2021.md"
        for chunk in output["retrieved_chunks"]
    )


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


def test_repeat_ask_returns_the_stored_answer_without_calling_the_model(capsys):
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    label = section_label(search(question, adapter)[0])
    model = FakeLanguageModel(
        json.dumps(
            {
                "answer": "Domestic travel meals are $75 per day.",
                "section": label,
            }
        )
    )

    first = main(["ask", question], database_adapter=adapter, language_model=model)
    first_output = json.loads(capsys.readouterr().out)
    second = main(["ask", question], database_adapter=adapter, language_model=model)
    second_output = json.loads(capsys.readouterr().out)

    assert first == 0
    assert second == 0
    assert first_output["answer"] == "Domestic travel meals are $75 per day."
    assert first_output["citation"]["source_doc"] == "minion_expense_policy_2024.md"
    assert second_output == first_output
    assert len(model.prompts) == 1


def test_ask_treats_case_and_whitespace_as_the_same_question(capsys):
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    padded = "  How   MUCH\tcan I spend\non food each day?  "
    label = section_label(search(question, adapter)[0])
    model = FakeLanguageModel(
        json.dumps(
            {
                "answer": "Domestic travel meals are $75 per day.",
                "section": label,
            }
        )
    )

    main(["ask", padded], database_adapter=adapter, language_model=model)
    first_output = json.loads(capsys.readouterr().out)
    main(["ask", question], database_adapter=adapter, language_model=model)
    second_output = json.loads(capsys.readouterr().out)

    assert first_output["answer"] == "Domestic travel meals are $75 per day."
    assert second_output == first_output
    assert len(model.prompts) == 1


def test_miss_sends_the_typed_question_to_the_model(capsys):
    adapter = PgAdapter()
    run(adapter)
    padded = "  How   MUCH\tcan I spend\non food each day?  "
    label = section_label(search("How much can I spend on food each day?", adapter)[0])
    model = FakeLanguageModel(
        json.dumps(
            {
                "answer": "Domestic travel meals are $75 per day.",
                "section": label,
            }
        )
    )

    main(["ask", padded], database_adapter=adapter, language_model=model)
    capsys.readouterr()

    assert padded in model.prompts[0]


def test_repeat_ask_returns_the_stored_answer_after_chunks_are_removed(capsys):
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    label = section_label(search(question, adapter)[0])
    model = FakeLanguageModel(
        json.dumps(
            {
                "answer": "Domestic travel meals are $75 per day.",
                "section": label,
            }
        )
    )

    main(["ask", question], database_adapter=adapter, language_model=model)
    stored = json.loads(capsys.readouterr().out)
    with adapter.connect() as conn:
        conn.execute("DROP TABLE policy_chunks")
    main(["ask", question], database_adapter=adapter, language_model=model)
    replay = json.loads(capsys.readouterr().out)

    assert replay == stored
    assert replay["answer"] == "Domestic travel meals are $75 per day."
    assert len(model.prompts) == 1


def test_a_different_question_does_not_reuse_the_stored_answer(capsys):
    adapter = PgAdapter()
    run(adapter)
    meals = "How much can I spend on food each day?"
    airfare = "Can I book first-class airfare?"
    meals_label = section_label(search(meals, adapter)[0])
    airfare_label = section_label(search(airfare, adapter)[0])
    model = SequenceLanguageModel(
        [
            json.dumps(
                {"answer": "Meals are $75 per day.", "section": meals_label}
            ),
            json.dumps({"answer": "Economy only.", "section": airfare_label}),
        ]
    )

    main(["ask", meals], database_adapter=adapter, language_model=model)
    capsys.readouterr()
    main(["ask", airfare], database_adapter=adapter, language_model=model)
    airfare_output = json.loads(capsys.readouterr().out)
    main(["ask", meals], database_adapter=adapter, language_model=model)
    replay = json.loads(capsys.readouterr().out)

    assert airfare_output["answer"] == "Economy only."
    assert replay["answer"] == "Meals are $75 per day."


def test_invalid_ask_stores_nothing_and_the_next_valid_ask_calls_the_model(capsys):
    adapter = PgAdapter()
    run(adapter)
    question = "Can I book first-class airfare?"
    label = section_label(search(question, adapter)[0])
    model = SequenceLanguageModel(
        [
            '{"answer": "Use economy airfare."}',
            json.dumps({"answer": "Economy only.", "section": label}),
        ]
    )

    with pytest.raises(ValidationError):
        main(["ask", question], database_adapter=adapter, language_model=model)
    assert capsys.readouterr().out == ""

    main(["ask", question], database_adapter=adapter, language_model=model)
    output = json.loads(capsys.readouterr().out)

    assert output["answer"] == "Economy only."
    assert output["citation"]["section"] == label


def test_ask_misses_when_the_generation_model_changes(capsys, monkeypatch):
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    label = section_label(search(question, adapter)[0])
    model = SequenceLanguageModel(
        [
            json.dumps({"answer": "Meals are $75 per day.", "section": label}),
            json.dumps({"answer": "A different model says $75.", "section": label}),
        ]
    )

    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    main(["ask", question], database_adapter=adapter, language_model=model)
    capsys.readouterr()
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    main(["ask", question], database_adapter=adapter, language_model=model)
    output = json.loads(capsys.readouterr().out)

    assert output["answer"] == "A different model says $75."


def test_ask_returns_the_earlier_answer_when_the_generation_model_returns(
    capsys, monkeypatch
):
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    label = section_label(search(question, adapter)[0])
    model = SequenceLanguageModel(
        [
            json.dumps({"answer": "Meals are $75 per day.", "section": label}),
            json.dumps({"answer": "A different model says $75.", "section": label}),
        ]
    )

    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    main(["ask", question], database_adapter=adapter, language_model=model)
    capsys.readouterr()
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    main(["ask", question], database_adapter=adapter, language_model=model)
    capsys.readouterr()
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    main(["ask", question], database_adapter=adapter, language_model=model)
    replay = json.loads(capsys.readouterr().out)

    assert replay["answer"] == "Meals are $75 per day."


def test_ask_misses_when_the_embedder_changes(capsys, monkeypatch):
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    label = section_label(search(question, adapter)[0])
    with adapter.connect() as conn:
        stored_vector = conn.execute(
            "SELECT embedding FROM policy_chunks WHERE chunk_id = %s",
            ("minion_expense_policy_2024:s5:c01",),
        ).fetchone()[0]
        vector = stored_vector.to_list()
    model = SequenceLanguageModel(
        [
            json.dumps({"answer": "Meals are $75 per day.", "section": label}),
            json.dumps(
                {"answer": "A different embedder says $75.", "section": label}
            ),
        ]
    )

    monkeypatch.setenv("EMBEDDING_MODEL", "all-mpnet-base-v2")
    main(["ask", question], database_adapter=adapter, language_model=model)
    capsys.readouterr()
    monkeypatch.setenv("EMBEDDING_MODEL", "other-embedder")
    monkeypatch.setattr(
        "app.retrieve.embed_texts", lambda texts: [vector for _ in texts]
    )
    main(["ask", question], database_adapter=adapter, language_model=model)
    output = json.loads(capsys.readouterr().out)

    assert output["answer"] == "A different embedder says $75."


def test_ask_misses_when_the_prompt_version_changes(capsys, monkeypatch):
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    label = section_label(search(question, adapter)[0])
    model = SequenceLanguageModel(
        [
            json.dumps({"answer": "Meals are $75 per day.", "section": label}),
            json.dumps({"answer": "Prompt v2 says $75.", "section": label}),
        ]
    )

    main(["ask", question], database_adapter=adapter, language_model=model)
    capsys.readouterr()

    class PromptV2:
        stem = "prompt_v2"

    monkeypatch.setattr("app.cache.PROMPT_PATH", PromptV2())
    main(["ask", question], database_adapter=adapter, language_model=model)
    output = json.loads(capsys.readouterr().out)

    assert output["answer"] == "Prompt v2 says $75."


def test_each_embedder_keeps_its_own_stored_answer(capsys, monkeypatch):
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    label = section_label(search(question, adapter)[0])
    with adapter.connect() as conn:
        stored_vector = conn.execute(
            "SELECT embedding FROM policy_chunks WHERE chunk_id = %s",
            ("minion_expense_policy_2024:s5:c01",),
        ).fetchone()[0]
        vector = stored_vector.to_list()
    model = SequenceLanguageModel(
        [
            json.dumps({"answer": "Meals are $75 per day.", "section": label}),
            json.dumps(
                {"answer": "A different embedder says $75.", "section": label}
            ),
        ]
    )

    monkeypatch.setenv("EMBEDDING_MODEL", "all-mpnet-base-v2")
    main(["ask", question], database_adapter=adapter, language_model=model)
    capsys.readouterr()
    monkeypatch.setenv("EMBEDDING_MODEL", "other-embedder")
    monkeypatch.setattr(
        "app.retrieve.embed_texts", lambda texts: [vector for _ in texts]
    )
    main(["ask", question], database_adapter=adapter, language_model=model)
    capsys.readouterr()
    monkeypatch.setenv("EMBEDDING_MODEL", "all-mpnet-base-v2")
    main(["ask", question], database_adapter=adapter, language_model=model)
    replay = json.loads(capsys.readouterr().out)

    assert replay["answer"] == "Meals are $75 per day."


def test_each_prompt_version_keeps_its_own_stored_answer(capsys, monkeypatch):
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    label = section_label(search(question, adapter)[0])
    model = SequenceLanguageModel(
        [
            json.dumps({"answer": "Meals are $75 per day.", "section": label}),
            json.dumps({"answer": "Prompt v2 says $75.", "section": label}),
        ]
    )

    main(["ask", question], database_adapter=adapter, language_model=model)
    capsys.readouterr()

    class PromptV2:
        stem = "prompt_v2"

    monkeypatch.setattr("app.cache.PROMPT_PATH", PromptV2())
    main(["ask", question], database_adapter=adapter, language_model=model)
    capsys.readouterr()
    monkeypatch.setattr("app.cache.PROMPT_PATH", PROMPT_PATH)
    main(["ask", question], database_adapter=adapter, language_model=model)
    replay = json.loads(capsys.readouterr().out)

    assert replay["answer"] == "Meals are $75 per day."


def test_eval_measures_a_question_ask_already_stored(tmp_path):
    adapter = PgAdapter()
    run(adapter)
    meals = EVAL_QUESTIONS[0]
    meals_label = section_label(search(meals, adapter)[0])
    stored = FakeLanguageModel(
        json.dumps({"answer": "Stored meals answer.", "section": meals_label})
    )
    main(["ask", meals], database_adapter=adapter, language_model=stored)

    labels = [
        section_label(search(question, adapter)[0]) for question in EVAL_QUESTIONS[:-1]
    ]
    refusal = "The provided policy does not answer this question."
    model = SequenceLanguageModel(
        [
            json.dumps({"answer": "From the policy.", "section": label})
            for label in labels
        ]
        + [json.dumps({"answer": refusal, "section": ""})]
    )
    output_path = tmp_path / "output.json"

    exit_code = main(
        ["eval", "--output", str(output_path)],
        database_adapter=adapter,
        language_model=model,
    )
    results = json.loads(output_path.read_text())

    assert exit_code == 0
    assert [result["question"] for result in results] == list(EVAL_QUESTIONS)
    assert [result["answer"] for result in results] == ["From the policy."] * 5 + [
        refusal
    ]


def test_ask_after_eval_still_runs_the_pipeline(capsys, tmp_path):
    adapter = PgAdapter()
    run(adapter)
    labels = [
        section_label(search(question, adapter)[0]) for question in EVAL_QUESTIONS[:-1]
    ]
    refusal = "The provided policy does not answer this question."
    eval_model = SequenceLanguageModel(
        [
            json.dumps({"answer": "From the policy.", "section": label})
            for label in labels
        ]
        + [json.dumps({"answer": refusal, "section": ""})]
    )
    main(
        ["eval", "--output", str(tmp_path / "output.json")],
        database_adapter=adapter,
        language_model=eval_model,
    )

    question = EVAL_QUESTIONS[0]
    ask_model = FakeLanguageModel(
        json.dumps({"answer": "Asked after eval.", "section": labels[0]})
    )
    main(["ask", question], database_adapter=adapter, language_model=ask_model)
    output = json.loads(capsys.readouterr().out)

    assert output["answer"] == "Asked after eval."
    assert len(ask_model.prompts) == 1


def test_eval_writes_a_replayable_record_of_all_six_questions(tmp_path):
    adapter = PgAdapter()
    run(adapter)
    refusal = "The provided policy does not answer this question."
    labels = [
        section_label(search(question, adapter)[0]) for question in EVAL_QUESTIONS[:-1]
    ]
    model = SequenceLanguageModel(
        [
            json.dumps({"answer": "From the policy.", "section": label})
            for label in labels
        ]
        + [json.dumps({"answer": refusal, "section": ""})]
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
        *labels,
        None,
    ]
    assert results[0]["citation"]["source_doc"] == "minion_expense_policy_2024.md"
    assert results[-1]["answer"] == refusal
    assert all(len(result["retrieved_chunks"]) == 3 for result in results)
    assert all(
        isinstance(result["retrieved_chunks"][0]["distance"], float)
        for result in results
    )
