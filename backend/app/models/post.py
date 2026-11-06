import uuid

from sqlalchemy import (
    CheckConstraint, Column, DateTime, ForeignKey,
    Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.models.enums import ApprovalAction, MediaType, PostStatus, pg_enum


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        # State invariants enforced BELOW the application — the last line of
        # defense against bugs in any layer above.
        CheckConstraint(
            "NOT (status = 'scheduled' AND scheduled_at IS NULL)",
            name="ck_scheduled_requires_time",
        ),
        CheckConstraint(
            "NOT (status IN ('publishing', 'published', 'partially_published') AND content_hash IS NULL)",
            name="ck_publishing_requires_hash",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    brand_id = Column(UUID(as_uuid=True), ForeignKey("brands.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=True)
    caption = Column(Text, nullable=False, default="")
    hashtags = Column(JSONB, nullable=False, default=list)
    cta = Column(String(255), nullable=True)
    status = Column(pg_enum(PostStatus, "post_status"), nullable=False, default=PostStatus.DRAFT, index=True)
    # SHA-256 over caption + sorted media IDs. Media IDs are immutable, so the
    # hash never changes because a URL was re-signed or a CDN domain rotated.
    content_hash = Column(String(64), nullable=True)
    version_number = Column(Integer, nullable=False, default=1)
    platform_targets = Column(JSONB, nullable=False, default=dict)  # {platform: {caption, hashtags}}
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    timezone = Column(String(50), nullable=True)  # IANA identifier, display only
    published_at = Column(DateTime(timezone=True), nullable=True)
    # FILTERED result only: {platform: {platform_post_id, url, published_at}}.
    # Raw provider responses are never persisted (may carry tokens/PII).
    publish_result = Column(JSONB, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    brand = relationship("Brand", back_populates="posts")
    media = relationship(
        "PostMedia", back_populates="post", cascade="all, delete-orphan", order_by="PostMedia.sort_order"
    )
    approval_logs = relationship(
        "ApprovalLog", back_populates="post", cascade="all, delete-orphan",
        order_by="ApprovalLog.created_at.desc()",
    )
    publish_jobs = relationship("PublishJob", back_populates="post", cascade="all, delete-orphan")


class PostMedia(Base):
    __tablename__ = "post_media"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    # Storage KEY (e.g. "media/ab12….jpg"), not a signed URL. Public/presigned
    # URLs are resolved at read/publish time by the storage service.
    storage_key = Column(String(1000), nullable=False)
    media_type = Column(pg_enum(MediaType, "media_type"), nullable=False, default=MediaType.IMAGE)
    sort_order = Column(Integer, nullable=False, default=0)
    alt_text = Column(String(500), nullable=True)
    meta = Column("metadata", JSONB, nullable=False, default=dict)  # size, original name, dimensions
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    post = relationship("Post", back_populates="media")


class ApprovalLog(Base):
    """Append-only approval ledger. NEVER updated or deleted."""
    __tablename__ = "approval_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    action = Column(pg_enum(ApprovalAction, "approval_action"), nullable=False)
    content_hash_at_action = Column(String(64), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    post = relationship("Post", back_populates="approval_logs")
