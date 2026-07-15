import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, false, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import DocumentPageStatus, ExtractionMethod


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number", name="uq_document_page_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DocumentPageStatus] = mapped_column(
        Enum(DocumentPageStatus), nullable=False, default=DocumentPageStatus.PENDING, index=True
    )
    extraction_method: Mapped[ExtractionMethod | None] = mapped_column(Enum(ExtractionMethod))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped["Document"] = relationship(back_populates="document_pages")


class ParsedDocument(Base):
    __tablename__ = "parsed_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), unique=True, index=True
    )
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    parser_name: Mapped[str] = mapped_column(String(100), nullable=False, default="pymupdf")
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False)
    is_scanned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    ocr_executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    ocr_page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    ocr_error: Mapped[str | None] = mapped_column(Text)
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped["Document"] = relationship(back_populates="parsed_document")
    pages: Mapped[list["ParsedPage"]] = relationship(
        back_populates="parsed_document", cascade="all, delete-orphan", order_by="ParsedPage.page_number"
    )
    chunks: Mapped[list["TextChunk"]] = relationship(
        back_populates="parsed_document", cascade="all, delete-orphan", order_by="TextChunk.position"
    )


class ParsedPage(Base):
    __tablename__ = "parsed_pages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parsed_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parsed_documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    parsed_document: Mapped["ParsedDocument"] = relationship(back_populates="pages")


class TextChunk(Base):
    __tablename__ = "text_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parsed_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parsed_documents.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_title: Mapped[str | None] = mapped_column(Text)
    section_title: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    parsed_document: Mapped["ParsedDocument"] = relationship(back_populates="chunks")
