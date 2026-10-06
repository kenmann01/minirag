from app.schemas import Citation, ModelAnswer

REFUSAL = "The provided policy does not answer this question."


def gate(
    model_answer: ModelAnswer, sections: dict[str, dict]
) -> tuple[str, Citation | None]:
    answer = model_answer.answer
    supporting_chunk = sections.get(model_answer.section)
    if answer != REFUSAL and supporting_chunk is not None:
        return answer, Citation(
            source_doc=supporting_chunk["source_doc"],
            effective_date=supporting_chunk["effective_date"],
            section=model_answer.section,
        )
    return REFUSAL, None
