"""Add rolling page quotas and persistent queue ordering.

Revision ID: 20260716_0003
Revises: 20260716_0002
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260716_0003"
down_revision: str | None = "20260716_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quota_guards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(sa.text("INSERT INTO quota_guards (id) VALUES (1)"))

    op.add_column("documents", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("quota_pages", sa.Integer(), server_default="0", nullable=False))
    op.add_column("documents", sa.Column("quota_released_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_documents_accepted_at", "documents", ["accepted_at"])
    op.create_index("ix_documents_queued_at", "documents", ["queued_at"])
    op.create_index("ix_documents_quota_released_at", "documents", ["quota_released_at"])
    op.create_index(
        "ix_documents_queue_order",
        "documents",
        ["processing_status", "accepted_at", "created_at"],
    )
    op.create_index(
        "ix_documents_project_quota_window",
        "documents",
        ["project_id", "accepted_at", "quota_released_at"],
    )
    op.execute(sa.text("UPDATE documents SET storage_backend = 's3' WHERE storage_backend = 'r2'"))


def downgrade() -> None:
    op.drop_index("ix_documents_project_quota_window", table_name="documents")
    op.drop_index("ix_documents_queue_order", table_name="documents")
    op.drop_index("ix_documents_quota_released_at", table_name="documents")
    op.drop_index("ix_documents_queued_at", table_name="documents")
    op.drop_index("ix_documents_accepted_at", table_name="documents")
    for column in (
        "quota_released_at",
        "quota_pages",
        "processing_started_at",
        "queued_at",
        "accepted_at",
    ):
        op.drop_column("documents", column)
    op.drop_table("quota_guards")
