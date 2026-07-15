import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CandidateChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    syllabus_item: str
    chunk_id: uuid.UUID
    score: float = Field(ge=0, le=1)
    chapter: str | None
    section: str | None
    page_start: int
    page_end: int
    text: str


class ItemMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    syllabus_item: str
    syllabus_item_original: str
    matching_query: str
    chapter: str | None
    matched: bool
    candidates: list[CandidateChunk]
    recall_stage: Literal["primary", "secondary"] | None = None
    reason: str | None = None
