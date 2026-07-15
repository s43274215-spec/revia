import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DocumentKind, DocumentProcessingStatus, GenerationStatus, ProjectStatus


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    kind: DocumentKind
    original_name: str
    mime_type: str
    size_bytes: int
    storage_backend: str
    processing_status: DocumentProcessingStatus
    total_pages: int
    processed_pages: int
    failed_pages: int
    ocr_page_count: int
    current_page: int
    processing_phase: str
    retry_count: int
    queue_priority: int
    error_message: str | None
    created_at: datetime


class SyllabusUpsert(BaseModel):
    text: str | None = None
    document_id: uuid.UUID | None = None


class SyllabusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    text: str | None
    document_id: uuid.UUID | None
    updated_at: datetime


class GenerationJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    status: GenerationStatus
    provider: str
    progress: int
    processed_items: int
    total_items: int
    item_failures: list[dict[str, str]]
    status_history: list[str]
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
