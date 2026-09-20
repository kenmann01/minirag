from typing import Protocol

from app.schemas import AskResponse, Citation, ModelAnswer, RetrievedChunk

REFUSAL = "The provided policy does not answer this question."


class LanguageModel(Protocol):
    def chat(self, prompt: str) -> str: ...


def generate(question: str, chunks: list[dict], model: LanguageModel) -> AskResponse:
    chunk = chunks[0]
    section = f"{chunk['section']}. {chunk['section_title']}"
    prompt = (
        "Answer the question using only the policy excerpt below. "
        f"If the excerpt does not contain the answer, answer exactly: {REFUSAL} "
        "Return JSON with string fields answer and section. "
        "Set section to the excerpt section exactly.\n\n"
        f"Section: {section}\n"
        f"Excerpt:\n{chunk['text']}\n\n"
        f"Question: {question}"
    )
    generated = ModelAnswer.model_validate_json(model.chat(prompt))
    citation = None
    if generated.answer != REFUSAL and generated.section == section:
        citation = Citation(
            document=chunk["document"],
            version=chunk["version"],
            section=section,
        )
    retrieved = RetrievedChunk(
        document=chunk["document"],
        version=chunk["version"],
        section=section,
        text=chunk["text"],
        distance=chunk["distance"],
    )
    return AskResponse(
        answer=generated.answer,
        citation=citation,
        retrieved_chunks=[retrieved],
    )
