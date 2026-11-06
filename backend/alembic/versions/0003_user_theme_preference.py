"""Per-user theme preference (light / dark / system).

Stored on the user so the same colour scheme comes back on every login,
on any device. Existing users get 'light', the product default.

Revision ID: 0003_user_theme_preference
Revises: 0002_workspace_self_approval
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_user_theme_preference"
down_revision = "0002_workspace_self_approval"
branch_labels = None
depends_on = None

theme_preference = postgresql.ENUM("light", "dark", "system", name="theme_preference")


def upgrade() -> None:
    theme_preference.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "users",
        sa.Column(
            "theme_preference",
            postgresql.ENUM(name="theme_preference", create_type=False),
            nullable=False,
            server_default="light",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "theme_preference")
    theme_preference.drop(op.get_bind(), checkfirst=True)
