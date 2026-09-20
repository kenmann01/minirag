from pydantic import BaseModel


class ModelAnswer(BaseModel):
    answer: str
    section: str


class Citation(BaseModel):
    document: str
    version: str
    section: str


class RetrievedChunk(BaseModel):
    document: str
    version: str
    section: str
    text: str
    distance: float


class AskResponse(BaseModel):
    answer: str
    citation: Citation | None
    retrieved_chunks: list[RetrievedChunk]
