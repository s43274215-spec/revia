"""Add durable generation item checkpoints and job activity fields.

Revision ID: 20260718_0007
Revises: 20260717_0006
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260718_0007"
down_revision: str | None = "20260717_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("generation_jobs", sa.Column("successful_items", sa.Integer(), nullable=True))
    op.add_column("generation_jobs", sa.Column("failed_items", sa.Integer(), nullable=True))
    op.add_column("generation_jobs", sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_generation_jobs_last_activity_at", "generation_jobs", ["last_activity_at"])

    op.create_table(
        "generation_job_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("syllabus_chapter", sa.Text(), nullable=True),
        sa.Column("syllabus_item", sa.Text(), nullable=False),
        sa.Column("parent_syllabus_item", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("failure_type", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("candidates_payload", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "position", name="uq_generation_job_item_position"),
    )
    op.create_index("ix_generation_job_items_job_id", "generation_job_items", ["job_id"])
    op.create_index("ix_generation_job_items_status", "generation_job_items", ["status"])
    op.create_index("ix_generation_job_items_updated_at", "generation_job_items", ["updated_at"])


def downgrade() -> None:
    op.drop_table("generation_job_items")
    op.drop_index("ix_generation_jobs_last_activity_at", table_name="generation_jobs")
    op.drop_column("generation_jobs", "last_activity_at")
    op.drop_column("generation_jobs", "failed_items")
    op.drop_column("generation_jobs", "successful_items")
