import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.models.enums import JobStatus, SocialPlatform, pg_enum


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (
        # The same page/profile can only be connected once per workspace —
        # a duplicate connection would publish the same post twice.
        UniqueConstraint(
            "workspace_id", "platform", "platform_account_id",
            name="uq_social_account_per_workspace",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(pg_enum(SocialPlatform, "social_platform"), nullable=False)
    platform_account_id = Column(String(255), nullable=False)
    account_name = Column(String(255), nullable=False)
    account_type = Column(String(50), nullable=True)  # page | professional | company
    access_token_encrypted = Column(Text, nullable=False)   # Fernet (AES-128-CBC + HMAC)
    refresh_token_encrypted = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    platform_metadata = Column(JSONB, nullable=False, default=dict)  # page_id, ig_user_id, author_urn
    is_active = Column(Boolean, nullable=False, server_default="true")
    needs_reauth = Column(Boolean, nullable=False, server_default="false")
    connected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    workspace = relationship("Workspace", back_populates="social_accounts")
    publish_jobs = relationship("PublishJob", back_populates="social_account")


class PublishJob(Base):
    """One row per (post, platform account) dispatch.

    Jobs OWN their status; the post's aggregate status is derived by a
    row-locked finalizer after each job finishes — worker tasks never write
    post.status directly for success/failure (no last-write-wins races).
    """
    __tablename__ = "publish_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    social_account_id = Column(UUID(as_uuid=True), ForeignKey("social_accounts.id"), nullable=False, index=True)
    celery_task_id = Column(String(255), nullable=True)  # NULL = not yet dispatched by the Beat scan
    status = Column(pg_enum(JobStatus, "job_status"), nullable=False, default=JobStatus.QUEUED, index=True)
    result = Column(JSONB, nullable=True)  # filtered: platform_post_id, url, published_at
    error_message = Column(Text, nullable=True)
    attempt_number = Column(Integer, nullable=False, default=0)
    scheduled_for = Column(DateTime(timezone=True), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)   # set at PROCESSING — stale-job cleanup keys off this
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    post = relationship("Post", back_populates="publish_jobs")
    social_account = relationship("SocialAccount", back_populates="publish_jobs")
