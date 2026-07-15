import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ContentVersionKind


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    project: Mapped["Project"] = relationship(back_populates="chapters")
    knowledge_points: Mapped[list["KnowledgePoint"]] = relationship(back_populates="chapter", cascade="all, delete-orphan", order_by="KnowledgePoint.position")


class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    chapter: Mapped["Chapter"] = relationship(back_populates="knowledge_points")
    bullet_points: Mapped[list["BulletPoint"]] = relationship(back_populates="knowledge_point", cascade="all, delete-orphan", order_by="BulletPoint.position")


class BulletPoint(Base):
    __tablename__ = "bullet_points"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_point_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_points.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    knowledge_point: Mapped["KnowledgePoint"] = relationship(back_populates="bullet_points")
    versions: Mapped[list["ContentVersion"]] = relationship(back_populates="bullet_point", cascade="all, delete-orphan")
    sources: Mapped[list["BulletPointSource"]] = relationship(
        back_populates="bullet_point", cascade="all, delete-orphan"
    )


class ContentVersion(Base):
    __tablename__ = "content_versions"
    __table_args__ = (UniqueConstraint("bullet_point_id", "kind", name="uq_bullet_version_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bullet_point_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bullet_points.id", ondelete="CASCADE"), index=True)
    kind: Mapped[ContentVersionKind] = mapped_column(Enum(ContentVersionKind), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    bullet_point: Mapped["BulletPoint"] = relationship(back_populates="versions")


class BulletPointSource(Base):
    __tablename__ = "bullet_point_sources"
    __table_args__ = (UniqueConstraint("bullet_point_id", "text_chunk_id", name="uq_bullet_source_chunk"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bullet_point_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bullet_points.id", ondelete="CASCADE"), index=True
    )
    text_chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("text_chunks.id", ondelete="RESTRICT"), index=True
    )
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)

    bullet_point: Mapped["BulletPoint"] = relationship(back_populates="sources")
    text_chunk: Mapped["TextChunk"] = relationship()
