"""Workspace self-approval setting.

A one-person business must be able to approve its own post; an agency wants
the author/approver separation. The rule is per workspace, default ON so a
fresh sign-up can complete the full journey alone.

Revision ID: 0002_workspace_self_approval
Revises: 0001_initial_schema
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_workspace_self_approval"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("allow_self_approval", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "allow_self_approval")
