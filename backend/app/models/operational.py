import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base
from app.models.enums import AuditAction, OperationType, TemplateCategory, pg_enum


class UsageLog(Base):
    """Operational telemetry for quotas, rate limiting and future billing."""
    __tablename__ = "usage_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)  # NULL for system events
    operation = Column(pg_enum(OperationType, "operation_type"), nullable=False)
    meta = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CaptionTemplate(Base):
    """Smart Templates — the MVP's stand-in for AI generation. Global seed data."""
    __tablename__ = "caption_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    industry = Column(String(100), nullable=False, index=True)
    category = Column(pg_enum(TemplateCategory, "template_category"), nullable=False,
                      default=TemplateCategory.PROMOTIONAL, index=True)
    title = Column(String(255), nullable=False)
    template_text = Column(Text, nullable=False)  # body with {{placeholder}} tags
    suggested_hashtags = Column(JSONB, nullable=False, default=list)
    suggested_cta = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AuditEvent(Base):
    """Universal tamper-evident ledger — ZERO foreign keys by design.

    FK-linked rows get cascade-deleted with their parent; this table cannot
    be. Deleting a workspace, user or post never erases the evidence of what
    happened (the pattern Stripe/GitHub/Linear use for compliance trails).
    Rows are append-only: no UPDATE or DELETE, ever.
    """
    __tablename__ = "audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), nullable=True, index=True)   # plain value — NO FK
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)        # plain value — NO FK
    user_email = Column(String(255), nullable=False)                        # snapshot at action time
    user_name = Column(String(255), nullable=False)                         # snapshot at action time
    entity_type = Column(String(50), nullable=False, index=True)            # Post | Brand | Workspace | ...
    entity_id = Column(UUID(as_uuid=True), nullable=False, index=True)      # plain value — NO FK
    action = Column(pg_enum(AuditAction, "audit_action"), nullable=False, index=True)
    changes = Column(JSONB, nullable=False, default=dict)                   # {"before": {...}, "after": {...}}
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
