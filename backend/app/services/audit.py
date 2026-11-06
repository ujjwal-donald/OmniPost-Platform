"""Audit trail writer — append-only rows into the FK-free audit_events table.

Call from routes and workers at every significant mutation. Never raises:
an audit failure must not fail the business operation (it is logged loudly
instead, so silence is still visible).
"""
import logging
from typing import Any
from uuid import UUID

from app.models import AuditAction, AuditEvent

logger = logging.getLogger(__name__)


def record_audit_event(
    db,  # AsyncSession or sync Session — add() is identical
    *,
    action: AuditAction,
    entity_type: str,
    entity_id: UUID,
    workspace_id: UUID | None,
    user_id: UUID | None,
    user_email: str,
    user_name: str,
    changes: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    try:
        db.add(AuditEvent(
            workspace_id=workspace_id,
            user_id=user_id,
            user_email=user_email,
            user_name=user_name,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            changes=changes or {},
            ip_address=ip_address,
            user_agent=user_agent,
        ))
    except Exception:  # noqa: BLE001
        logger.exception("AUDIT WRITE FAILED for %s %s on %s", action, entity_type, entity_id)
