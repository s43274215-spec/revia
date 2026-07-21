from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.models.enums import ExtractionMethod


class GitHubOCRClaimRequest(BaseModel):
    attempt_id: uuid.UUID


class GitHubOCRClaimRead(BaseModel):
    document_id: uuid.UUID
    attempt_id: uuid.UUID
    original_name: str
    download_url: str
    size_bytes: int
    total_pages: int
    completed_pages: list[int]
    ocr_dpi: int
    minimum_text_length: int
    max_pdf_pages: int
    heartbeat_seconds: int


class GitHubOCRHeartbeatRequest(BaseModel):
    attempt_id: uuid.UUID
    current_page: int | None = Field(default=None, ge=1)


class GitHubOCRPageResult(BaseModel):
    attempt_id: uuid.UUID
    text: str = Field(max_length=1_000_000)
    extraction_method: ExtractionMethod


class GitHubOCROutlineEntry(BaseModel):
    level: int = Field(ge=1, le=20)
    title: str = Field(min_length=1, max_length=300)
    page_number: int = Field(ge=1)


class GitHubOCRFinishRequest(BaseModel):
    attempt_id: uuid.UUID
    outline: list[GitHubOCROutlineEntry] = Field(default_factory=list, max_length=5000)


class GitHubOCRFailureRequest(BaseModel):
    attempt_id: uuid.UUID
    page_number: int | None = Field(default=None, ge=1)
    error_message: str = Field(min_length=1, max_length=1000)


class GitHubOCRProgressRead(BaseModel):
    document_id: uuid.UUID
    processing_status: str
    processing_phase: str
    processed_pages: int
    total_pages: int
    current_page: int
