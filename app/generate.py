# Internal and Confidential - Not for External Distribution.
"""Generate grounded answers and validated citations from retrieved chunks."""

from pathlib import Path
from typing import Protocol

from app.schemas import AskResponse, ModelAnswer, RetrievedChunk
from app.validate import REFUSAL, gate

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompt_v3.md"


class LanguageModel(Protocol):
    """Generate a structured answer from a complete text prompt."""

    def chat(self, prompt: str) -> str:
        """Submit a prompt and return the model's raw response.

        Args:
            prompt: Fully rendered instructions and policy context.

        Returns:
            Raw response text expected to contain a JSON model answer.
        """
        ...


def section_label(chunk: dict) -> str:
    return f"{chunk['source_doc']} {chunk['section']}. {chunk['section_title']}"


def generate(question: str, chunks: list[dict], model: LanguageModel) -> AskResponse:
    """Generate and validate an answer grounded in retrieved policy chunks.

    Args:
        question: Employee question to answer.
        chunks: Retrieved policy records, including metadata and distances.
        model: Language-model adapter used to produce structured JSON.

    Returns:
        A validated response containing the answer, an optional trusted
        citation, and the retrieved chunks. Empty retrieval yields the
        standard refusal.
    """
    if not chunks:
        return AskResponse(answer=REFUSAL, citation=None, retrieved_chunks=[])
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
    answer, citation = gate(generated, sections)
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
