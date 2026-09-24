"""Ingest/store and Ask CLI behavior at their public seams."""

import json

import pytest
from pydantic import ValidationError

from app.cli import EVAL_QUESTIONS, main
from app.embeddings import embed_texts
from app.generate import PROMPT_PATH, section_label
from app.ingest import run
from app.pgadapter import PgAdapter
from app.reranker import MODEL_NAME, CrossEncoderReranker
from app.retrieve import fuse, search
from app.schemas import Citation, ModelAnswer
from app.validate import REFUSAL, gate


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


class TopFiveReranker:
    """Mimics rank semantics on already-fused candidates: first five win."""

    def __init__(self):
        self.calls = []

    def rank(self, question: str, chunks: list[dict]) -> list[dict]:
        self.calls.append((question, len(chunks)))
        return chunks[:5]


class OrderingReranker:
    """Floats the pinned chunk ids first, best first; keeps the rest behind."""

    def __init__(self, ordered_chunk_ids: list[str]):
        self.ordered_chunk_ids = ordered_chunk_ids
        self.calls = []

    def rank(self, question: str, chunks: list[dict]) -> list[dict]:
        self.calls.append((question, len(chunks)))
        pinned = [chunk for chunk in chunks if chunk["chunk_id"] in self.ordered_chunk_ids]
        rest = [chunk for chunk in chunks if chunk["chunk_id"] not in self.ordered_chunk_ids]
        return pinned + rest


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
        "minion_travel_security_policy.md",
        "minion_equipment_policy.md",
        "minion_dress_code_policy.md",
        "minion_training_policy.md",
        "minion_communications_policy.md",
        "minion_visitors_policy.md",
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


def test_the_villain_protocol_question_surfaces_the_travel_security_trigger_section():
    adapter = PgAdapter()
    run(adapter)
    rows = search("What triggers the Villain Protocol?", adapter)
    trigger_sections = {
        (row["source_doc"], row["section"])
        for row in rows
        if row["source_doc"] == "minion_travel_security_policy.md"
        and "Villain Protocol" in row["text"]
        and "trigger" in row["text"]
    }
    assert trigger_sections, [row["chunk_id"] for row in rows]


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


def test_ingest_creates_the_stored_tsvector_column_and_gin_index():
    adapter = PgAdapter()
    run(adapter)
    with adapter.connect() as conn:
        tsv = conn.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'policy_chunks' AND column_name = 'tsv'
            """
        ).fetchone()
        gin = conn.execute(
            """
            SELECT 1
            FROM pg_indexes
            WHERE tablename = 'policy_chunks' AND indexname = 'policy_chunks_tsv_gin'
            """
        ).fetchone()
    assert tsv is not None
    assert gin is not None


def test_ingest_recreates_a_table_missing_the_tsvector_column():
    adapter = PgAdapter()
    with adapter.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS policy_chunks")
        conn.execute(
            """
            CREATE TABLE policy_chunks (
                chunk_id TEXT PRIMARY KEY,
                source_doc TEXT NOT NULL,
                section TEXT NOT NULL,
                section_title TEXT NOT NULL,
                effective_date TEXT,
                superseded_by TEXT,
                parent_text TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding vector(768) NOT NULL
            )
            """
        )
    run(adapter)
    with adapter.connect() as conn:
        tsv = conn.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'policy_chunks' AND column_name = 'tsv'
            """
        ).fetchone()
    assert tsv is not None


def test_ingest_command_prints_how_many_children_it_wrote(capsys):
    adapter = PgAdapter()
    exit_code = main(["ingest"], database_adapter=adapter)
    assert exit_code == 0
    with adapter.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM policy_chunks").fetchone()[0]
    assert capsys.readouterr().err.strip() == f"ingested {count} policy chunks"


def test_fuse_ties_a_rank_1_and_4_pair_with_its_mirror_and_orders_deterministically():
    forward = fuse(
        [
            {"chunk_id": "a"},
            {"chunk_id": "v2"},
            {"chunk_id": "v3"},
            {"chunk_id": "b"},
        ],
        [
            {"chunk_id": "b"},
            {"chunk_id": "k2"},
            {"chunk_id": "k3"},
            {"chunk_id": "a"},
        ],
    )
    mirrored = fuse(
        [
            {"chunk_id": "b"},
            {"chunk_id": "k2"},
            {"chunk_id": "k3"},
            {"chunk_id": "a"},
        ],
        [
            {"chunk_id": "a"},
            {"chunk_id": "v2"},
            {"chunk_id": "v3"},
            {"chunk_id": "b"},
        ],
    )
    forward_scores = dict(forward)
    mirrored_scores = dict(mirrored)
    assert round(forward_scores["a"], 4) == 0.0320
    assert round(forward_scores["b"], 4) == 0.0320
    assert forward_scores["a"] == mirrored_scores["b"]
    assert forward_scores["b"] == mirrored_scores["a"]
    assert [chunk_id for chunk_id, _ in forward[:2]] == ["a", "b"]
    assert [chunk_id for chunk_id, _ in mirrored[:2]] == ["b", "a"]


def test_fuse_scores_a_single_lane_candidate_one_share_and_it_loses_to_double():
    fused = fuse(
        [{"chunk_id": "single"}, {"chunk_id": "both"}],
        [{"chunk_id": "both"}],
    )
    scores = dict(fused)
    assert scores["single"] == 1 / 61
    assert round(scores["single"], 4) == 0.0164
    assert fused[0][0] == "both"
    assert fused[1][0] == "single"


def test_fuse_of_two_empty_lanes_is_empty():
    assert fuse([], []) == []


def test_gate_returns_the_answer_with_a_citation_when_the_model_names_a_retrieved_section():
    sections = {
        "minion_expense_policy_2024.md 5. Travel Expenses": {
            "source_doc": "minion_expense_policy_2024.md",
            "effective_date": "January 15, 2024",
        }
    }
    answer, citation = gate(
        ModelAnswer(
            answer="Meals are $75 per day.",
            section="minion_expense_policy_2024.md 5. Travel Expenses",
        ),
        sections,
    )
    assert answer == "Meals are $75 per day."
    assert citation == Citation(
        source_doc="minion_expense_policy_2024.md",
        effective_date="January 15, 2024",
        section="minion_expense_policy_2024.md 5. Travel Expenses",
    )


def test_gate_passes_the_exact_refusal_through_without_a_citation():
    sections = {
        "minion_expense_policy_2024.md 5. Travel Expenses": {
            "source_doc": "minion_expense_policy_2024.md",
            "effective_date": "January 15, 2024",
        }
    }
    answer, citation = gate(ModelAnswer(answer=REFUSAL, section=""), sections)
    assert answer == "The provided policy does not answer this question."
    assert citation is None


def test_gate_forces_the_refusal_when_the_model_names_an_unknown_section():
    sections = {
        "minion_expense_policy_2024.md 5. Travel Expenses": {
            "source_doc": "minion_expense_policy_2024.md",
            "effective_date": "January 15, 2024",
        }
    }
    answer, citation = gate(
        ModelAnswer(answer="You may claim up to $65 per day.", section="99. Missing"),
        sections,
    )
    assert answer == "The provided policy does not answer this question."
    assert citation is None


def ranked_chunk(chunk_id: str, source_doc: str, section: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "source_doc": source_doc,
        "section": section,
        "section_title": "Title",
        "effective_date": None,
        "text": text,
        "distance": 0.5,
        "rrf_score": 0.01,
    }


class StubbedReranker(CrossEncoderReranker):
    def __init__(self, scores: list[float]):
        self.scores = scores
        self.predicted_pairs = []

    def _predict(self, pairs):
        self.predicted_pairs.append(list(pairs))
        return self.scores


def test_rank_orders_the_best_scoring_child_first():
    chunks = [
        ranked_chunk("a", "a.md", "1", "weak"),
        ranked_chunk("b", "b.md", "2", "strong"),
        ranked_chunk("c", "c.md", "3", "middle"),
    ]
    reranker = StubbedReranker([0.1, 0.9, 0.5])
    ranked = reranker.rank("question", chunks)
    assert [chunk["chunk_id"] for chunk in ranked] == ["b", "c", "a"]
    assert reranker.predicted_pairs == [
        [
            ("question", "weak"),
            ("question", "strong"),
            ("question", "middle"),
        ]
    ]


def test_rank_breaks_score_ties_by_original_position():
    chunks = [
        ranked_chunk("a", "a.md", "1", "one"),
        ranked_chunk("b", "b.md", "2", "two"),
    ]
    reranker = StubbedReranker([0.4, 0.4])
    ranked = reranker.rank("question", chunks)
    assert [chunk["chunk_id"] for chunk in ranked] == ["a", "b"]


def test_rank_keeps_only_the_best_child_of_each_section():
    chunks = [
        ranked_chunk("a:c02", "a.md", "5", "weaker sibling"),
        ranked_chunk("b:c01", "b.md", "2", "other section"),
        ranked_chunk("a:c01", "a.md", "5", "stronger sibling"),
    ]
    reranker = StubbedReranker([0.3, 0.6, 0.9])
    ranked = reranker.rank("question", chunks)
    assert [chunk["chunk_id"] for chunk in ranked] == ["a:c01", "b:c01"]


def test_rank_caps_the_output_at_top_n():
    chunks = [
        ranked_chunk(chunk_id, "a.md", str(number), "text")
        for number, chunk_id in enumerate("abcdefg")
    ]
    reranker = StubbedReranker([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
    assert [chunk["chunk_id"] for chunk in reranker.rank("question", chunks)] == [
        "g",
        "f",
        "e",
        "d",
        "c",
    ]
    assert [
        chunk["chunk_id"] for chunk in reranker.rank("question", chunks, top_n=2)
    ] == ["g", "f"]


def test_rank_of_no_candidates_returns_empty_without_touching_the_model():
    reranker = StubbedReranker([])
    assert reranker.rank("question", []) == []
    assert reranker.predicted_pairs == []


def test_the_cross_encoder_model_name_is_ms_marco_minilm():
    assert MODEL_NAME == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_search_returns_up_to_twenty_fused_current_candidates():
    adapter = PgAdapter()
    run(adapter)
    rows = search("How much can I spend on food each day?", adapter)
    assert len(rows) == 20
    assert rows[0]["source_doc"] == "minion_expense_policy_2024.md"
    assert rows[0]["section"] == "5"
    assert rows[0]["section_title"] == "Travel Expenses"
    assert "$75/day" in rows[0]["text"]
    assert all(row["source_doc"] != "minion_expense_policy_2021.md" for row in rows)
    assert all(isinstance(row["distance"], float) for row in rows)
    assert isinstance(rows[0]["rrf_score"], float)
    assert [row["rrf_score"] for row in rows] == sorted(
        (row["rrf_score"] for row in rows), reverse=True
    )
    ranked = TopFiveReranker().rank("How much can I spend on food each day?", rows)
    assert len(ranked) == 5
    assert len({(row["source_doc"], row["section"]) for row in ranked}) == 5
    assert ranked[0]["source_doc"] == "minion_expense_policy_2024.md"
    assert ranked[0]["section"] == "5"


def test_search_can_return_two_children_of_a_section_and_rank_keeps_one():
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
    ranked = StubbedReranker([0.5] * len(rows)).rank(question, rows)
    ranked_travel = [
        row
        for row in ranked
        if row["source_doc"] == "minion_expense_policy_2024.md" and row["section"] == "5"
    ]
    assert len(travel) == 2
    assert len(ranked) == 5
    assert len(ranked_travel) == 1


def test_exact_term_reaches_the_fused_candidates_through_the_keyword_lane():
    adapter = PgAdapter()
    run(adapter)
    question = "What is the zagat-grade muffin budget of 250 dollars?"
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
                "test_zagat:s1:c01",
                "test_zagat.md",
                "1",
                "Muffin Budget",
                None,
                None,
                "The snack budget is fixed.",
                "The zagat-grade muffin budget of 250 dollars covers morning snacks.",
                opposite,
            ),
        )
    rows = search(question, adapter)
    rescued = [row for row in rows if row["chunk_id"] == "test_zagat:s1:c01"]
    ranked = TopFiveReranker().rank(question, rows)
    assert len(rescued) == 1
    assert len(ranked) == 5
    with adapter.connect() as conn:
        conn.execute(
            "DELETE FROM policy_chunks WHERE chunk_id = %s",
            ("test_zagat:s1:c01",),
        )


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
        reranker=TopFiveReranker(),
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "Domestic travel meals are $75 per day."
    assert output["citation"] == {
        "source_doc": "minion_expense_policy_2024.md",
        "effective_date": "January 15, 2024",
        "section": label,
    }
    assert len(output["retrieved_chunks"]) == 5
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
        reranker=TopFiveReranker(),
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
        reranker=TopFiveReranker(),
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
            "think": False,
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
        reranker=TopFiveReranker(),
    )

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "Cited from the policy."
    assert output["citation"] == {
        "source_doc": top["source_doc"],
        "effective_date": top["effective_date"],
        "section": label,
    }
    assert len(output["retrieved_chunks"]) == 5
    assert all(
        chunk["source_doc"] != "minion_expense_policy_2021.md"
        for chunk in output["retrieved_chunks"]
    )


def test_generator_receives_all_five_retrieved_policy_excerpts(capsys):
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
        reranker=TopFiveReranker(),
    )
    output = json.loads(capsys.readouterr().out)

    assert len(model.prompts) == 1
    assert "using only the policy excerpts" in model.prompts[0]
    assert "requires one option" in model.prompts[0]
    assert "specific item fits a broader prohibited category" in model.prompts[0]
    assert "Cite the section containing the rule" in model.prompts[0]
    assert "every claim in the answer must be supported" in model.prompts[0]
    assert len(output["retrieved_chunks"]) == 5
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
            reranker=TopFiveReranker(),
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
        reranker=TopFiveReranker(),
    )

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "The provided policy does not answer this question."
    assert output["citation"] is None
    assert len(output["retrieved_chunks"]) == 5
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
        reranker=TopFiveReranker(),
    )
    capsys.readouterr()

    assert f"answer exactly: {refusal}" in model.prompts[0]


def test_refusal_is_never_cached_and_recomputes_on_every_ask(capsys):
    adapter = PgAdapter()
    run(adapter)
    model = FakeLanguageModel(
        json.dumps(
            {
                "answer": "The provided policy does not answer this question.",
                "section": "",
            }
        )
    )

    first = main(
        ["ask", "Does the company reimburse gym memberships?"],
        database_adapter=adapter,
        language_model=model,
        reranker=TopFiveReranker(),
    )
    first_output = json.loads(capsys.readouterr().out)
    second = main(
        ["ask", "Does the company reimburse gym memberships?"],
        database_adapter=adapter,
        language_model=model,
        reranker=TopFiveReranker(),
    )
    second_output = json.loads(capsys.readouterr().out)

    assert first == 0
    assert second == 0
    assert len(model.prompts) == 2
    assert first_output["answer"] == "The provided policy does not answer this question."
    assert second_output["answer"] == "The provided policy does not answer this question."


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

    first = main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    first_output = json.loads(capsys.readouterr().out)
    second = main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
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

    main(["ask", padded], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    first_output = json.loads(capsys.readouterr().out)
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
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

    main(["ask", padded], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
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

    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    stored = json.loads(capsys.readouterr().out)
    with adapter.connect() as conn:
        conn.execute("DROP TABLE policy_chunks")
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    replay = json.loads(capsys.readouterr().out)

    assert replay == stored
    assert replay["answer"] == "Domestic travel meals are $75 per day."
    assert len(model.prompts) == 1


def test_empty_retrieval_refuses_without_calling_the_model_or_storing_a_cache_entry(
    capsys,
):
    adapter = PgAdapter()
    run(adapter)
    question = "How much can I spend on food each day?"
    with adapter.connect() as conn:
        conn.execute("DROP TABLE policy_chunks")
        conn.execute(
            """
            CREATE TABLE policy_chunks (
                chunk_id TEXT PRIMARY KEY,
                source_doc TEXT NOT NULL,
                section TEXT NOT NULL,
                section_title TEXT NOT NULL,
                effective_date TEXT,
                superseded_by TEXT,
                parent_text TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding vector(768) NOT NULL,
                tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
            )
            """
        )
    model = FakeLanguageModel(
        json.dumps({"answer": "Meals are $75 per day.", "section": "1. Meals"})
    )

    exit_code = main(
        ["ask", question],
        database_adapter=adapter,
        language_model=model,
        reranker=TopFiveReranker(),
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["answer"] == "The provided policy does not answer this question."
    assert output["citation"] is None
    assert output["retrieved_chunks"] == []
    assert model.prompts == []

    run(adapter)
    label = section_label(search(question, adapter)[0])
    good_model = FakeLanguageModel(
        json.dumps({"answer": "Meals are $75 per day.", "section": label})
    )
    main(["ask", question], database_adapter=adapter, language_model=good_model, reranker=TopFiveReranker())
    replay = json.loads(capsys.readouterr().out)

    assert replay["answer"] == "Meals are $75 per day."
    assert len(good_model.prompts) == 1


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

    main(["ask", meals], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    capsys.readouterr()
    main(["ask", airfare], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    airfare_output = json.loads(capsys.readouterr().out)
    main(["ask", meals], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
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
        main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    assert capsys.readouterr().out == ""

    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
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
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    capsys.readouterr()
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
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
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    capsys.readouterr()
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    capsys.readouterr()
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
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
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    capsys.readouterr()
    monkeypatch.setenv("EMBEDDING_MODEL", "other-embedder")
    monkeypatch.setattr(
        "app.retrieve.embed_texts", lambda texts: [vector for _ in texts]
    )
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
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

    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    capsys.readouterr()

    class PromptV9:
        stem = "prompt_v9"

    monkeypatch.setattr("app.cache.PROMPT_PATH", PromptV9())
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
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
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    capsys.readouterr()
    monkeypatch.setenv("EMBEDDING_MODEL", "other-embedder")
    monkeypatch.setattr(
        "app.retrieve.embed_texts", lambda texts: [vector for _ in texts]
    )
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    capsys.readouterr()
    monkeypatch.setenv("EMBEDDING_MODEL", "all-mpnet-base-v2")
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
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

    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    capsys.readouterr()

    class PromptV9:
        stem = "prompt_v9"

    monkeypatch.setattr("app.cache.PROMPT_PATH", PromptV9())
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
    capsys.readouterr()
    monkeypatch.setattr("app.cache.PROMPT_PATH", PROMPT_PATH)
    main(["ask", question], database_adapter=adapter, language_model=model, reranker=TopFiveReranker())
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
    main(["ask", meals], database_adapter=adapter, language_model=stored, reranker=TopFiveReranker())

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
        reranker=TopFiveReranker(),
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
        reranker=TopFiveReranker(),
    )

    question = EVAL_QUESTIONS[0]
    ask_model = FakeLanguageModel(
        json.dumps({"answer": "Asked after eval.", "section": labels[0]})
    )
    main(["ask", question], database_adapter=adapter, language_model=ask_model, reranker=TopFiveReranker())
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
        reranker=TopFiveReranker(),
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
    assert all(len(result["retrieved_chunks"]) == 5 for result in results)
    assert all(
        isinstance(result["retrieved_chunks"][0]["distance"], float)
        for result in results
    )
