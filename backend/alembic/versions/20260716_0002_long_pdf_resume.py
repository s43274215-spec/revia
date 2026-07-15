"""Add object storage, per-page persistence, progress and leases.

Revision ID: 20260716_0002
Revises: 20260715_0001
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0002"
down_revision: str | None = "20260715_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        with op.get_context().autocommit_block():
            for value in ("QUEUED", "PROCESSING", "INTERRUPTED"):
                op.execute(f"ALTER TYPE documentprocessingstatus ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column("documents", sa.Column("storage_backend", sa.String(length=20), server_default="local", nullable=False))
    op.add_column("documents", sa.Column("total_pages", sa.Integer(), server_default="0", nullable=False))
    op.add_column("documents", sa.Column("processed_pages", sa.Integer(), server_default="0", nullable=False))
    op.add_column("documents", sa.Column("failed_pages", sa.Integer(), server_default="0", nullable=False))
    op.add_column("documents", sa.Column("ocr_page_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("documents", sa.Column("current_page", sa.Integer(), server_default="0", nullable=False))
    op.add_column("documents", sa.Column("processing_phase", sa.String(length=32), server_default="uploading", nullable=False))
    op.add_column("documents", sa.Column("lease_owner", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column(
        "documents",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_documents_lease_owner", "documents", ["lease_owner"])
    op.create_index("ix_documents_lease_expires_at", "documents", ["lease_expires_at"])

    uuid_type = postgresql.UUID(as_uuid=True)
    op.create_table(
        "document_pages",
        sa.Column("id", uuid_type, nullable=False),
        sa.Column("document_id", uuid_type, nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "PROCESSING", "COMPLETED", "FAILED", name="documentpagestatus"),
            nullable=False,
        ),
        sa.Column("extraction_method", sa.Enum("TEXT", "OCR", name="extractionmethod"), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("character_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "page_number", name="uq_document_page_number"),
    )
    op.create_index("ix_document_pages_document_id", "document_pages", ["document_id"])
    op.create_index("ix_document_pages_status", "document_pages", ["status"])


def downgrade() -> None:
    op.drop_index("ix_document_pages_status", table_name="document_pages")
    op.drop_index("ix_document_pages_document_id", table_name="document_pages")
    op.drop_table("document_pages")
    op.drop_index("ix_documents_lease_expires_at", table_name="documents")
    op.drop_index("ix_documents_lease_owner", table_name="documents")
    for column in (
        "updated_at",
        "retry_count",
        "lease_expires_at",
        "lease_owner",
        "processing_phase",
        "current_page",
        "ocr_page_count",
        "failed_pages",
        "processed_pages",
        "total_pages",
        "storage_backend",
    ):
        op.drop_column("documents", column)

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS extractionmethod")
        op.execute("DROP TYPE IF EXISTS documentpagestatus")
