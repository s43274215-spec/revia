import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.matching.query_planning import QueryPlan


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
    matched_queries: list[str] = Field(default_factory=list)


class ItemMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    syllabus_item: str
    syllabus_item_original: str
    matching_query: str
    chapter: str | None
    matched: bool
    candidates: list[CandidateChunk]
    query_plan: QueryPlan
    query_top_scores: dict[str, float] = Field(default_factory=dict)
    used_ai_fallback: bool = False
    unmatched_reason_category: str | None = None
    recall_stage: Literal["primary", "secondary"] | None = None
    reason: str | None = None
