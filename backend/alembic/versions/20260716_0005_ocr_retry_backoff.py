"""Add persisted OCR retry backoff.

Revision ID: 20260716_0005
Revises: 20260716_0004
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0005"
down_revision: str | None = "20260716_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("retry_not_before", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_documents_retry_not_before",
        "documents",
        ["retry_not_before"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_retry_not_before", table_name="documents")
    op.drop_column("documents", "retry_not_before")
