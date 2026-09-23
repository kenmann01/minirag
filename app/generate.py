from pathlib import Path
from typing import Protocol

from app.schemas import AskResponse, Citation, ModelAnswer, RetrievedChunk

REFUSAL = "The provided policy does not answer this question."
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompt_v1.md"


class LanguageModel(Protocol):
    def chat(self, prompt: str) -> str: ...


def section_label(chunk: dict) -> str:
    return f"{chunk['source_doc']} {chunk['section']}. {chunk['section_title']}"


def generate(question: str, chunks: list[dict], model: LanguageModel) -> AskResponse:
    sections = {section_label(chunk): chunk for chunk in chunks}
    excerpts = "\n\n".join(
        f"Section: {section}\nExcerpt:\n{chunk['text']}"
        for section, chunk in sections.items()
    )
    prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{excerpts}", excerpts)
        .replace("{question}", question)
    )
    generated = ModelAnswer.model_validate_json(model.chat(prompt))
    citation = None
    answer = generated.answer
    supporting_chunk = sections.get(generated.section)
    if answer != REFUSAL and supporting_chunk is not None:
        citation = Citation(
            source_doc=supporting_chunk["source_doc"],
            effective_date=supporting_chunk["effective_date"],
            section=generated.section,
        )
    elif answer != REFUSAL:
        answer = REFUSAL
    retrieved = [
        RetrievedChunk(
            source_doc=chunk["source_doc"],
            effective_date=chunk["effective_date"],
            section=section,
            text=chunk["text"],
            distance=chunk["distance"],
        )
        for section, chunk in sections.items()
    ]
    return AskResponse(
        answer=answer,
        citation=citation,
        retrieved_chunks=retrieved,
    )
