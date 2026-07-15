"""Add owner workspace, runtime site settings and queue priority.

Revision ID: 20260716_0004
Revises: 20260716_0003
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260716_0004"
down_revision: str | None = "20260716_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("role", sa.String(length=16), server_default="public", nullable=False),
    )
    op.add_column("workspaces", sa.Column("owner_slot", sa.Integer(), nullable=True))
    op.create_index("ix_workspaces_role", "workspaces", ["role"])
    op.create_index("ix_workspaces_owner_slot", "workspaces", ["owner_slot"], unique=True)
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.create_check_constraint(
            "ck_workspace_role_owner_slot",
            "(role = 'owner' AND owner_slot = 1) OR (role = 'public' AND owner_slot IS NULL)",
        )

    op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_access_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("updated_by_workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by_workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_site_settings_updated_by_workspace_id",
        "site_settings",
        ["updated_by_workspace_id"],
    )

    op.add_column(
        "documents",
        sa.Column("queue_priority", sa.Integer(), server_default="10", nullable=False),
    )
    op.drop_index("ix_documents_queue_order", table_name="documents")
    op.create_index(
        "ix_documents_queue_order",
        "documents",
        ["processing_status", "queue_priority", "accepted_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_queue_order", table_name="documents")
    op.create_index(
        "ix_documents_queue_order",
        "documents",
        ["processing_status", "accepted_at", "created_at"],
    )
    op.drop_column("documents", "queue_priority")
    op.drop_index("ix_site_settings_updated_by_workspace_id", table_name="site_settings")
    op.drop_table("site_settings")
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.drop_constraint("ck_workspace_role_owner_slot", type_="check")
    op.drop_index("ix_workspaces_owner_slot", table_name="workspaces")
    op.drop_index("ix_workspaces_role", table_name="workspaces")
    op.drop_column("workspaces", "owner_slot")
    op.drop_column("workspaces", "role")
