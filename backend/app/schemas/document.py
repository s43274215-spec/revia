import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.project import DocumentRead


class ParsedPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    text: str


class TextChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    page_start: int
    page_end: int
    chapter_title: str | None
    section_title: str | None
    content: str


class ParsedDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    page_count: int
    parser_name: str
    parser_version: str
    is_scanned: bool
    ocr_executed: bool
    ocr_page_count: int
    ocr_error: str | None
    parsed_at: datetime
    pages: list[ParsedPageRead]
    chunks: list[TextChunkRead]


class DocumentProcessingRead(BaseModel):
    document: DocumentRead
    parsed_document: ParsedDocumentRead
