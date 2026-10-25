"""The candidate knobs: prompt override, seed, and provider usage."""

import json

from app.generation.generate import generate
from app.generation.ollama import ChatReply
from app.generation.validate import REFUSAL

CHUNK = {
    "chunk_id": "bank-1",
    "source_doc": "fixture-policy.md",
    "section": "1.1",
    "section_title": "Rule bank-1",
    "effective_date": "2026-01-01",
    "text": "Rule bank-1 states the fixture obligation.",
    "distance": 0.1,
}
LABEL = "fixture-policy.md 1.1. Rule bank-1"


class RecordingModel:
    def __init__(self, reply: str | ChatReply | None = None) -> None:
        self.prompts: list[str] = []
        self.seeds: list[int | None] = []
        self.reply = reply if reply is not None else json.dumps({"answer": "A", "section": LABEL})

    def chat(self, prompt: str, *, seed: int | None = None) -> str | ChatReply:
        self.prompts.append(prompt)
        self.seeds.append(seed)
        return self.reply


def test_generate_uses_the_committed_prompt_by_default():
    model = RecordingModel()
    response = generate("What is the rule?", [CHUNK], model)

    assert "Version 3" in model.prompts[0]
    assert "What is the rule?" in model.prompts[0]
    assert CHUNK["text"] in model.prompts[0]
    assert response.answer == "A"
    assert response.citation is not None
    assert response.citation.section == LABEL
    assert response.prompt_tokens == 0
    assert response.completion_tokens == 0
    assert model.seeds == [None]


def test_generate_uses_a_prompt_override_template():
    model = RecordingModel()
    response = generate(
        "What is the rule?",
        [CHUNK],
        model,
        prompt_text="VARIANT {excerpts} || {question}",
    )

    assert model.prompts[0].startswith("VARIANT ")
    assert "What is the rule?" in model.prompts[0]
    assert CHUNK["text"] in model.prompts[0]
    assert response.answer == "A"


def test_generate_threads_the_seed_only_when_it_is_set():
    model = RecordingModel()
    generate("What is the rule?", [CHUNK], model, seed=7)
    assert model.seeds == [7]


def test_generate_keeps_a_legacy_model_working_without_a_seed():
    seen = {}

    class LegacyModel:
        def chat(self, prompt: str) -> str:
            seen["prompt"] = prompt
            return json.dumps({"answer": "A", "section": LABEL})

    response = generate("What is the rule?", [CHUNK], LegacyModel())
    assert response.answer == "A"
    assert "What is the rule?" in seen["prompt"]


def test_generate_records_provider_usage():
    reply = ChatReply(
        json.dumps({"answer": "A", "section": LABEL}),
        prompt_tokens=11,
        completion_tokens=7,
        model="fixture-model",
    )
    response = generate("What is the rule?", [CHUNK], RecordingModel(reply))
    assert response.prompt_tokens == 11
    assert response.completion_tokens == 7


def test_an_empty_retrieval_refuses_without_calling_the_model():
    class ExplodingModel:
        def chat(self, prompt: str) -> str:
            raise AssertionError("the model must not run on an empty retrieval")

    response = generate("What is the rule?", [], ExplodingModel())
    assert response.answer == REFUSAL
    assert response.citation is None
    assert response.prompt_tokens == 0
    assert response.completion_tokens == 0


def test_an_ungrounded_section_is_refused():
    model = RecordingModel(json.dumps({"answer": "A", "section": "not-a-section"}))
    response = generate("What is the rule?", [CHUNK], model)
    assert response.answer == REFUSAL
    assert response.citation is None
