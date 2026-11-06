"""Initial schema — 13 tables per the approved Database Architecture document.

Hand-authored against the SQLAlchemy models so the first migration is
reviewable and does not depend on autogenerate against a live database.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum types — created on first use, dropped in downgrade().
workspace_role = postgresql.ENUM("owner", "admin", "member", name="workspace_role", create_type=False)
post_status = postgresql.ENUM(
    "draft", "pending_approval", "approved", "scheduled", "publishing",
    "published", "partially_published", "failed", name="post_status", create_type=False,
)
approval_action = postgresql.ENUM("submitted", "approved", "rejected", "invalidated",
                                  name="approval_action", create_type=False)
social_platform = postgresql.ENUM("facebook", "instagram", "linkedin",
                                  name="social_platform", create_type=False)
media_type = postgresql.ENUM("image", "video", "carousel_item", name="media_type", create_type=False)
job_status = postgresql.ENUM("queued", "processing", "completed", "failed", "cancelled",
                             "needs_review", name="job_status", create_type=False)
operation_type = postgresql.ENUM("post_created", "post_published", "media_uploaded",
                                 "ai_generation", name="operation_type", create_type=False)
template_category = postgresql.ENUM("promotional", "educational", "engagement", "seasonal",
                                    name="template_category", create_type=False)
audit_action = postgresql.ENUM(
    "CREATE", "UPDATE", "DELETE", "PUBLISH", "SUBMIT", "APPROVE", "REJECT",
    "SCHEDULE", "CONNECT", "DISCONNECT", "INVITE", "ROLE_CHANGE",
    name="audit_action", create_type=False,
)

_ALL_ENUMS = [workspace_role, post_status, approval_action, social_platform, media_type,
              job_status, operation_type, template_category, audit_action]

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_EMPTY_OBJ = sa.text("'{}'::jsonb")
_EMPTY_ARR = sa.text("'[]'::jsonb")


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ALL_ENUMS:
        enum.create(bind, checkfirst=True)

    # ── Identity & auth ───────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)

    # ── Multi-tenancy ─────────────────────────────────────────────────
    op.create_table(
        "workspaces",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("owner_id", _UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("ix_workspaces_slug", "workspaces", ["slug"], unique=True)

    op.create_table(
        "workspace_members",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("workspace_id", _UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", workspace_role, nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )
    op.create_index("ix_workspace_members_workspace_id", "workspace_members", ["workspace_id"])
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])

    # ── Brands ────────────────────────────────────────────────────────
    op.create_table(
        "brands",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("workspace_id", _UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("target_audience", sa.Text(), nullable=True),
        sa.Column("tone_of_voice", sa.String(100), nullable=True),
        sa.Column("language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("logo_url", sa.String(500), nullable=True),
        sa.Column("brand_guidelines", postgresql.JSONB(), nullable=False, server_default=_EMPTY_OBJ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("ix_brands_workspace_id", "brands", ["workspace_id"])

    # ── Posts & content ───────────────────────────────────────────────
    op.create_table(
        "posts",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("brand_id", _UUID, sa.ForeignKey("brands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", _UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("caption", sa.Text(), nullable=False, server_default=""),
        sa.Column("hashtags", postgresql.JSONB(), nullable=False, server_default=_EMPTY_ARR),
        sa.Column("cta", sa.String(255), nullable=True),
        sa.Column("status", post_status, nullable=False, server_default="draft"),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("platform_targets", postgresql.JSONB(), nullable=False, server_default=_EMPTY_OBJ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(50), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_result", postgresql.JSONB(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        # State invariants — the last line of defense below the application.
        sa.CheckConstraint(
            "NOT (status = 'scheduled' AND scheduled_at IS NULL)",
            name="ck_scheduled_requires_time",
        ),
        sa.CheckConstraint(
            "NOT (status IN ('publishing', 'published', 'partially_published') AND content_hash IS NULL)",
            name="ck_publishing_requires_hash",
        ),
    )
    op.create_index("ix_posts_brand_id", "posts", ["brand_id"])
    op.create_index("ix_posts_status", "posts", ["status"])

    op.create_table(
        "post_media",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("post_id", _UUID, sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False),
        sa.Column("media_type", media_type, nullable=False, server_default="image"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alt_text", sa.String(500), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=_EMPTY_OBJ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("ix_post_media_post_id", "post_media", ["post_id"])

    op.create_table(
        "approval_logs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("post_id", _UUID, sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", approval_action, nullable=False),
        sa.Column("content_hash_at_action", sa.String(64), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("ix_approval_logs_post_id", "approval_logs", ["post_id"])

    # ── Integrations & workers ────────────────────────────────────────
    op.create_table(
        "social_accounts",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("workspace_id", _UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", social_platform, nullable=False),
        sa.Column("platform_account_id", sa.String(255), nullable=False),
        sa.Column("account_name", sa.String(255), nullable=False),
        sa.Column("account_type", sa.String(50), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("platform_metadata", postgresql.JSONB(), nullable=False, server_default=_EMPTY_OBJ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("needs_reauth", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        # The same page can only be connected once per workspace.
        sa.UniqueConstraint("workspace_id", "platform", "platform_account_id",
                            name="uq_social_account_per_workspace"),
    )
    op.create_index("ix_social_accounts_workspace_id", "social_accounts", ["workspace_id"])

    op.create_table(
        "publish_jobs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("post_id", _UUID, sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("social_account_id", _UUID, sa.ForeignKey("social_accounts.id"), nullable=False),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("status", job_status, nullable=False, server_default="queued"),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("ix_publish_jobs_post_id", "publish_jobs", ["post_id"])
    op.create_index("ix_publish_jobs_social_account_id", "publish_jobs", ["social_account_id"])
    op.create_index("ix_publish_jobs_status", "publish_jobs", ["status"])
    op.create_index("ix_publish_jobs_scheduled_for", "publish_jobs", ["scheduled_for"])

    # ── Operational ───────────────────────────────────────────────────
    op.create_table(
        "usage_logs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("workspace_id", _UUID, sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("operation", operation_type, nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=_EMPTY_OBJ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("ix_usage_logs_workspace_id", "usage_logs", ["workspace_id"])

    op.create_table(
        "caption_templates",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("industry", sa.String(100), nullable=False),
        sa.Column("category", template_category, nullable=False, server_default="promotional"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column("suggested_hashtags", postgresql.JSONB(), nullable=False, server_default=_EMPTY_ARR),
        sa.Column("suggested_cta", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("ix_caption_templates_industry", "caption_templates", ["industry"])
    op.create_index("ix_caption_templates_category", "caption_templates", ["category"])

    # ── Audit ledger: ZERO foreign keys by design. Deleting a workspace,
    #    user or post must never erase the evidence of what happened. ────
    op.create_table(
        "audit_events",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("workspace_id", _UUID, nullable=True),   # plain value — NO FK
        sa.Column("user_id", _UUID, nullable=True),        # plain value — NO FK
        sa.Column("user_email", sa.String(255), nullable=False),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", _UUID, nullable=False),     # plain value — NO FK
        sa.Column("action", audit_action, nullable=False),
        sa.Column("changes", postgresql.JSONB(), nullable=False, server_default=_EMPTY_OBJ),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
    )
    op.create_index("ix_audit_events_workspace_id", "audit_events", ["workspace_id"])
    op.create_index("ix_audit_events_user_id", "audit_events", ["user_id"])
    op.create_index("ix_audit_events_entity_type", "audit_events", ["entity_type"])
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])


def downgrade() -> None:
    for table in (
        "audit_events", "caption_templates", "usage_logs", "publish_jobs",
        "social_accounts", "approval_logs", "post_media", "posts", "brands",
        "workspace_members", "workspaces", "refresh_tokens", "users",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    for enum in _ALL_ENUMS:
        enum.drop(bind, checkfirst=True)
