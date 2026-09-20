from typing import Protocol

from app.schemas import AskResponse, Citation, ModelAnswer, RetrievedChunk

REFUSAL = "The provided policy does not answer this question."


class LanguageModel(Protocol):
    def chat(self, prompt: str) -> str: ...


def generate(question: str, chunks: list[dict], model: LanguageModel) -> AskResponse:
    sections = {
        f"{chunk['section']}. {chunk['section_title']}": chunk for chunk in chunks
    }
    excerpts = "\n\n".join(
        f"Section: {section}\nExcerpt:\n{chunk['text']}"
        for section, chunk in sections.items()
    )
    prompt = (
        "Answer the question using only the policy excerpts below. "
        "Do not add unsupported information. "
        f"If the excerpts do not contain the answer, answer exactly: {REFUSAL} "
        "Return JSON with string fields answer and section. "
        "For a supported answer, set section to its supporting excerpt section "
        "exactly. For the refusal, set section to an empty string.\n\n"
        f"{excerpts}\n\n"
        f"Question: {question}"
    )
    generated = ModelAnswer.model_validate_json(model.chat(prompt))
    citation = None
    answer = generated.answer
    supporting_chunk = sections.get(generated.section)
    if answer != REFUSAL and supporting_chunk is not None:
        citation = Citation(
            document=supporting_chunk["document"],
            version=supporting_chunk["version"],
            section=generated.section,
        )
    elif answer != REFUSAL:
        answer = REFUSAL
    retrieved = [
        RetrievedChunk(
            document=chunk["document"],
            version=chunk["version"],
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
