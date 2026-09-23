from pydantic import BaseModel


class ModelAnswer(BaseModel):
    answer: str
    section: str


class Citation(BaseModel):
    source_doc: str
    effective_date: str | None
    section: str


class RetrievedChunk(BaseModel):
    source_doc: str
    effective_date: str | None
    section: str
    text: str
    distance: float


class AskResponse(BaseModel):
    answer: str
    citation: Citation | None
    retrieved_chunks: list[RetrievedChunk]
