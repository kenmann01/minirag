# Internal and Confidential - Not for External Distribution.
"""Define validated models for generated answers, citations, and responses."""

from pydantic import BaseModel


class ModelAnswer(BaseModel):
    """Represent the answer and supporting section proposed by the model."""

    answer: str
    section: str


class Citation(BaseModel):
    """Identify the policy document, effective date, and section behind an answer."""

    source_doc: str
    effective_date: str | None
    section: str


class RetrievedChunk(BaseModel):
    """Represent a policy excerpt returned by hybrid retrieval."""

    source_doc: str
    effective_date: str | None
    section: str
    text: str
    distance: float


class AskResponse(BaseModel):
    """Return a grounded answer together with its retrieval evidence.

    The token counts are what the model adapter reported for the call that
    produced the answer. They stay zero when no model call happened (an empty
    retrieval refuses before generation) or when an adapter cannot report
    usage; the candidate forwards them to the harness as provider usage.
    """

    answer: str
    citation: Citation | None
    retrieved_chunks: list[RetrievedChunk]
    prompt_tokens: int = 0
    completion_tokens: int = 0
