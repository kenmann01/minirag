# Internal and Confidential - Not for External Distribution.
"""Generate grounded answers and validated citations from retrieved chunks."""

from pathlib import Path
from typing import Protocol

from app.generation.schemas import AskResponse, ModelAnswer, RetrievedChunk
from app.generation.validate import REFUSAL, gate

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "policy_answer_v3.md"


class LanguageModel(Protocol):
    """Generate a structured answer from a complete text prompt."""

    def chat(self, prompt: str, *, seed: int | None = None) -> str:
        """Submit a prompt and return the model's raw response.

        Args:
            prompt: Fully rendered instructions and policy context.
            seed: Optional sampling seed. Adapters that cannot accept a seed
                may ignore it; the default adapter pins 77.

        Returns:
            Raw response text expected to contain a JSON model answer. An
            adapter that reports usage returns a ChatReply, which is a str
            carrying prompt_tokens and completion_tokens.
        """
        ...


def section_label(chunk: dict) -> str:
    """Format a chunk's document, section, and title into the label the model cites."""
    return f"{chunk['source_doc']} {chunk['section']}. {chunk['section_title']}"


def generate(
    question: str,
    chunks: list[dict],
    model: LanguageModel,
    *,
    prompt_text: str | None = None,
    seed: int | None = None,
) -> AskResponse:
    """Generate and validate an answer grounded in retrieved policy chunks.

    Args:
        question: Employee question to answer.
        chunks: Retrieved policy records, including metadata and distances.
        model: Language-model adapter used to produce structured JSON.
        prompt_text: Optional prompt variant. It replaces the versioned prompt
            template and accepts the same {excerpts} and {question}
            placeholders. None keeps the committed v3 prompt.
        seed: Optional sampling seed forwarded to the adapter only when set.
            The adapter's own default (77) is used when None.

    Returns:
        A validated response containing the answer, an optional trusted
        citation, the retrieved chunks, and the provider-reported token
        counts (zero when the adapter reports none). Empty retrieval yields
        the standard refusal without a model call.
    """
    if not chunks:
        return AskResponse(answer=REFUSAL, citation=None, retrieved_chunks=[])
    sections = {section_label(chunk): chunk for chunk in chunks}
    excerpts = "\n\n".join(
        f"Section: {section}\nExcerpt:\n{chunk['text']}" for section, chunk in sections.items()
    )
    template = prompt_text if prompt_text is not None else PROMPT_PATH.read_text(encoding="utf-8")
    prompt = template.replace("{excerpts}", excerpts).replace("{question}", question)
    reply = model.chat(prompt) if seed is None else model.chat(prompt, seed=seed)
    generated = ModelAnswer.model_validate_json(reply)
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
        prompt_tokens=int(getattr(reply, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(reply, "completion_tokens", 0) or 0),
    )
