import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import client_ip, get_current_user, get_workspace_member, require_role
from app.core.rate_limit import workspace_rate_limit
from app.db.session import get_db
from app.models import AuditAction, OperationType, UsageLog, User, Workspace, WorkspaceMember, WorkspaceRole
from app.schemas.schemas import (
    AddMemberRequest, MemberResponse, MessageResponse, UsageSummaryResponse,
    WorkspaceCreate, WorkspaceResponse, WorkspaceUpdate,
)
from app.services.audit import record_audit_event

router = APIRouter(prefix="/workspaces", tags=["Workspaces"], dependencies=[Depends(workspace_rate_limit)])


def generate_slug(name: str) -> str:
    """URL-safe, unique workspace slug: "aura-coffee-3f9c1a".

    A random suffix (not a timestamp): two people with the same name signing
    up in the same second used to collide on the unique index and get a 409.
    """
    base = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")[:80] or "workspace"
    return f"{base}-{secrets.token_hex(3)}"

@router.get("", response_model=List[WorkspaceResponse])
async def list_my_workspaces(db: AsyncSession = Depends(get_db),
                             user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at.desc())
    )
    return [WorkspaceResponse.model_validate(w) for w in result.scalars().all()]


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(req: WorkspaceCreate, request: Request,
                           db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)):
    workspace = Workspace(name=req.name.strip(), slug=generate_slug(req.name), owner_id=user.id)
    db.add(workspace)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=WorkspaceRole.OWNER))
    record_audit_event(
        db, action=AuditAction.CREATE, entity_type="Workspace", entity_id=workspace.id,
        workspace_id=workspace.id, user_id=user.id, user_email=user.email, user_name=user.full_name,
        changes={"after": {"name": workspace.name}}, ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(workspace_id: UUID, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    await get_workspace_member(workspace_id, db, user)
    workspace = await db.scalar(select(Workspace).where(Workspace.id == workspace_id))
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")
    return WorkspaceResponse.model_validate(workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(workspace_id: UUID, req: WorkspaceUpdate, request: Request,
                           db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)):
    member = await get_workspace_member(workspace_id, db, user)
    require_role(member, WorkspaceRole.OWNER, WorkspaceRole.ADMIN)

    workspace = await db.scalar(select(Workspace).where(Workspace.id == workspace_id))
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")

    before: dict = {}
    after: dict = {}
    if req.name:
        before["name"], workspace.name = workspace.name, req.name.strip()
        after["name"] = workspace.name
    if req.allow_self_approval is not None:
        # Governance switch — owner only (an admin must not relax the rule for themselves).
        require_role(member, WorkspaceRole.OWNER)
        before["allow_self_approval"] = workspace.allow_self_approval
        workspace.allow_self_approval = req.allow_self_approval
        after["allow_self_approval"] = workspace.allow_self_approval
    if after:
        record_audit_event(
            db, action=AuditAction.UPDATE, entity_type="Workspace", entity_id=workspace.id,
            workspace_id=workspace.id, user_id=user.id, user_email=user.email, user_name=user.full_name,
            changes={"before": before, "after": after}, ip_address=client_ip(request),
        )
    await db.commit()
    await db.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)


@router.get("/{workspace_id}/usage", response_model=UsageSummaryResponse)
async def usage_summary(workspace_id: UUID, days: int = Query(30, ge=1, le=365),
                        db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Operation counts for the last N days — the seed of the Phase 3 credit
    system. ai_generation is reserved and always 0 in Phase 1."""
    await get_workspace_member(workspace_id, db, user)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = await db.execute(
        select(UsageLog.operation, func.count(UsageLog.id))
        .where(UsageLog.workspace_id == workspace_id, UsageLog.created_at >= since)
        .group_by(UsageLog.operation)
    )
    counts = {op.value: 0 for op in OperationType}
    for operation, n in rows.all():
        counts[operation.value] = n

    media_bytes = await db.scalar(
        select(func.coalesce(func.sum((UsageLog.meta["bytes"]).as_integer()), 0))
        .where(UsageLog.workspace_id == workspace_id, UsageLog.created_at >= since,
               UsageLog.operation == OperationType.MEDIA_UPLOADED)
    )
    return UsageSummaryResponse(
        workspace_id=workspace_id, days=days, counts=counts,
        media_bytes=int(media_bytes or 0), ai_generations=counts[OperationType.AI_GENERATION.value],
    )


@router.get("/{workspace_id}/members", response_model=List[MemberResponse])
async def list_members(workspace_id: UUID, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    await get_workspace_member(workspace_id, db, user)
    result = await db.execute(
        select(WorkspaceMember)
        .options(selectinload(WorkspaceMember.user))
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.joined_at.asc())
    )
    return [
        MemberResponse(
            id=m.id, user_id=m.user_id, role=m.role.value, joined_at=m.joined_at,
            email=m.user.email, full_name=m.user.full_name, avatar_url=m.user.avatar_url,
        )
        for m in result.scalars().all()
    ]


@router.post("/{workspace_id}/members", response_model=MessageResponse)
async def add_member(workspace_id: UUID, req: AddMemberRequest, request: Request,
                     db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_user)):
    caller = await get_workspace_member(workspace_id, db, user)
    require_role(caller, WorkspaceRole.OWNER, WorkspaceRole.ADMIN)

    # ── Privilege escalation guards ────────────────────────────────────
    # OWNER is never granted here: ownership transfer is its own flow.
    if req.role == WorkspaceRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The owner role cannot be assigned here.",
        )
    # Only the owner can grant ADMIN; admins may add members only.
    if req.role == WorkspaceRole.ADMIN and caller.role != WorkspaceRole.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the workspace owner can grant the admin role.",
        )

    target = await db.scalar(select(User).where(User.email == req.email.lower()))
    if not target:
        # Neutral wording — never confirm whether an email has an account.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This user could not be added. Check the email address or ask them to sign up first.",
        )

    existing = await db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == target.id,
        )
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="User is already a member of this workspace.")

    # Locked so two concurrent invites can't both see "one member" and race.
    workspace = await db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
    members_before = await db.scalar(
        select(func.count(WorkspaceMember.id)).where(WorkspaceMember.workspace_id == workspace_id)
    )

    new_member = WorkspaceMember(workspace_id=workspace_id, user_id=target.id, role=req.role)
    db.add(new_member)
    record_audit_event(
        db, action=AuditAction.INVITE, entity_type="WorkspaceMember", entity_id=target.id,
        workspace_id=workspace_id, user_id=user.id, user_email=user.email, user_name=user.full_name,
        changes={"after": {"member_email": target.email, "role": req.role.value}},
        ip_address=client_ip(request),
    )

    # Self-approval exists so a one-person business can finish the journey
    # alone. The moment a second person joins there is someone else to
    # approve, so author != approver is switched on — once, on the 1 -> 2
    # transition only. If the owner later re-enables it deliberately, later
    # invites leave that choice alone.
    message = f"Added {target.full_name} to the workspace."
    if members_before == 1 and workspace.allow_self_approval:
        workspace.allow_self_approval = False
        record_audit_event(
            db, action=AuditAction.UPDATE, entity_type="Workspace", entity_id=workspace.id,
            workspace_id=workspace.id, user_id=user.id, user_email=user.email, user_name=user.full_name,
            changes={"before": {"allow_self_approval": True}, "after": {"allow_self_approval": False},
                     "reason": "automatic: second member joined"},
            ip_address=client_ip(request),
        )
        message += (" Posts now need approval from someone other than their author;"
                    " the owner can change this in workspace settings.")
    await db.commit()
    return MessageResponse(message=message)
