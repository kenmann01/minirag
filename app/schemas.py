# Internal and Confidential — Not for External Distribution.
"""Define validated models for generated answers, citations, and responses."""

from pydantic import BaseModel


class ModelAnswer(BaseModel):
    """Represent the answer and supporting section proposed by the model."""

    answer: str
    section: str


class Citation(BaseModel):
    """Identify the policy document, version, and section supporting an answer."""

    document: str
    version: str
    section: str


class RetrievedChunk(BaseModel):
    """Represent a policy excerpt returned by vector similarity search."""

    document: str
    version: str
    section: str
    text: str
    distance: float


class AskResponse(BaseModel):
    """Return a grounded answer together with its retrieval evidence."""

    answer: str
    citation: Citation | None
    retrieved_chunks: list[RetrievedChunk]
