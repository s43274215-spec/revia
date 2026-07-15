"""Initial production schema with anonymous workspaces.

Revision ID: 20260715_0001
Revises:
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260715_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)
    op.create_table(
        "workspaces",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "projects",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("NOT_UPLOADED", "PROCESSING", "COMPLETED", "FAILED", name="projectstatus"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_workspace_id", "projects", ["workspace_id"])
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_table(
        "documents",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("project_id", uuid_type, nullable=False),
        sa.Column("kind", sa.Enum("COURSE_MATERIAL", "SYLLABUS", name="documentkind"), nullable=False),
        sa.Column("original_name", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=1000), nullable=True),
        sa.Column("processing_status", sa.Enum("UPLOADED", "PARSING", "PARSED", "FAILED", name="documentprocessingstatus"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_documents_project_id", "documents", ["project_id"])
    op.create_index("ix_documents_processing_status", "documents", ["processing_status"])
    op.create_table(
        "generation_jobs",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("project_id", uuid_type, nullable=False),
        sa.Column("status", sa.Enum("PENDING", "PARSING", "MATCHING", "GENERATING", "VALIDATING", "COMPLETED", "PARTIAL_FAILED", "FAILED", name="generationstatus"), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("processed_items", sa.Integer(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("item_failures", sa.JSON(), nullable=False),
        sa.Column("status_history", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generation_jobs_project_id", "generation_jobs", ["project_id"])
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])
    op.create_table(
        "parsed_documents",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("document_id", uuid_type, nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("parser_name", sa.String(length=100), nullable=False),
        sa.Column("parser_version", sa.String(length=50), nullable=False),
        sa.Column("is_scanned", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("ocr_executed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("ocr_page_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("ocr_error", sa.Text(), nullable=True),
        sa.Column("parsed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_parsed_documents_document_id", "parsed_documents", ["document_id"], unique=True)
    op.create_table(
        "parsed_pages",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("parsed_document_id", uuid_type, nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["parsed_document_id"], ["parsed_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_parsed_pages_parsed_document_id", "parsed_pages", ["parsed_document_id"])
    op.create_table(
        "text_chunks",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("parsed_document_id", uuid_type, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("chapter_title", sa.Text(), nullable=True),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["parsed_document_id"], ["parsed_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_text_chunks_parsed_document_id", "text_chunks", ["parsed_document_id"])
    op.create_table(
        "syllabi",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("project_id", uuid_type, nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("document_id", uuid_type, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_syllabi_project_id", "syllabi", ["project_id"], unique=True)
    op.create_table(
        "chapters",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("project_id", uuid_type, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chapters_project_id", "chapters", ["project_id"])
    op.create_table(
        "knowledge_points",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("chapter_id", uuid_type, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_points_chapter_id", "knowledge_points", ["chapter_id"])
    op.create_table(
        "bullet_points",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("knowledge_point_id", uuid_type, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_point_id"], ["knowledge_points.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bullet_points_knowledge_point_id", "bullet_points", ["knowledge_point_id"])
    op.create_table(
        "content_versions",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("bullet_point_id", uuid_type, nullable=False),
        sa.Column("kind", sa.Enum("ORIGINAL", "RECITATION", "KEYWORDS", name="contentversionkind"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["bullet_point_id"], ["bullet_points.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bullet_point_id", "kind", name="uq_bullet_version_kind"),
    )
    op.create_index("ix_content_versions_bullet_point_id", "content_versions", ["bullet_point_id"])
    op.create_table(
        "bullet_point_sources",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("bullet_point_id", uuid_type, nullable=False),
        sa.Column("text_chunk_id", uuid_type, nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["bullet_point_id"], ["bullet_points.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["text_chunk_id"], ["text_chunks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bullet_point_id", "text_chunk_id", name="uq_bullet_source_chunk"),
    )
    op.create_index("ix_bullet_point_sources_bullet_point_id", "bullet_point_sources", ["bullet_point_id"])
    op.create_index("ix_bullet_point_sources_text_chunk_id", "bullet_point_sources", ["text_chunk_id"])
    op.create_table(
        "deepseek_credentials",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("workspace_id", uuid_type, nullable=False),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=False),
        sa.Column("masked_hint", sa.String(length=16), nullable=True),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deepseek_credentials_workspace_id", "deepseek_credentials", ["workspace_id"], unique=True)


def downgrade() -> None:
    for table in (
        "deepseek_credentials",
        "bullet_point_sources",
        "content_versions",
        "bullet_points",
        "knowledge_points",
        "chapters",
        "syllabi",
        "text_chunks",
        "parsed_pages",
        "parsed_documents",
        "generation_jobs",
        "documents",
        "projects",
        "workspaces",
    ):
        op.drop_table(table)
