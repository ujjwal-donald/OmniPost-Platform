"""Posts: CRUD, approval workflow, scheduling and publish-now.

Invariants enforced here (and re-verified in the worker):
- Tenancy: every access goes through get_post_access / get_brand_access.
- Approval: pending-only, admin/owner role, approver != creator, and the
  submitted hash is VERIFIED — never recomputed-and-blessed.
- Immutability: publishing/published posts cannot be edited.
- Pending posts cannot be edited either: what the approver reads must be
  what gets approved. Withdraw (or get a rejection) first.
- Scheduling: target social accounts must belong to the post's workspace.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import (
    APPROVER_ROLES, client_ip, get_brand_access, get_current_user,
    get_post_access, get_post_membership, get_workspace_member,
)
from app.core.rate_limit import workspace_rate_limit
from app.core.security import compute_content_hash, post_content_hash, resolve_platform_content
from app.db.session import get_db
from app.models import (
    ApprovalAction, ApprovalLog, AuditAction, Brand, JobStatus, OperationType,
    Post, PostStatus, PublishJob, SocialAccount, UsageLog, User, Workspace,
)
from app.schemas.schemas import (
    AccountValidation, ApprovalLogResponse, JobResponse, MessageResponse, PostCreate,
    PostListResponse, PostMediaResponse, PostResponse, PostUpdate,
    PublishNowRequest, RejectRequest, RetryRequest, ScheduleRequest, ScheduleResponse,
    ValidateRequest, ValidateResponse,
)
from app.services.audit import record_audit_event
from app.services.publishing.factory import get_adapter
from app.services.storage import get_storage
from app.core.constants import PLATFORM_CHAR_LIMITS

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Posts"], dependencies=[Depends(workspace_rate_limit)])

EDITABLE_BLOCKED = (PostStatus.PUBLISHING, PostStatus.PUBLISHED, PostStatus.PARTIALLY_PUBLISHED)


def assert_editable(post: Post) -> None:
    """Shared by PATCH and media add/remove."""
    if post.status in EDITABLE_BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Published or publishing posts cannot be edited. Duplicate the post instead.",
        )
    if post.status == PostStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This post is awaiting approval and is locked. Withdraw it to edit.",
        )


def can_manage(post: Post, member) -> bool:
    """Destructive actions: the author, or an admin/owner."""
    return post.created_by == member.user_id or member.role in APPROVER_ROLES


# ── Serialization (media display URLs resolved via storage service) ───
def media_schema(m) -> PostMediaResponse:
    return PostMediaResponse(
        id=m.id,
        media_url=get_storage().display_url(m.storage_key),
        media_type=m.media_type.value,
        sort_order=m.sort_order,
        alt_text=m.alt_text,
    )


def post_schema(post: Post) -> PostResponse:
    return PostResponse(
        id=post.id, brand_id=post.brand_id, created_by=post.created_by,
        title=post.title, caption=post.caption, hashtags=post.hashtags or [],
        cta=post.cta, status=post.status.value, content_hash=post.content_hash,
        version_number=post.version_number, platform_targets=post.platform_targets,
        scheduled_at=post.scheduled_at, timezone=post.timezone,
        published_at=post.published_at, publish_result=post.publish_result,
        retry_count=post.retry_count, created_at=post.created_at,
        updated_at=post.updated_at, media=[media_schema(m) for m in post.media],
    )


def current_hash(post: Post) -> str:
    return post_content_hash(post)


async def cancel_open_jobs(post: Post, db: AsyncSession, reason: str) -> None:
    """Cancel queued jobs for a post. With Beat-scan scheduling, cancellation
    is purely a status update — no Celery revoke races."""
    result = await db.execute(
        select(PublishJob).where(
            PublishJob.post_id == post.id,
            PublishJob.status == JobStatus.QUEUED,
        )
    )
    for job in result.scalars().all():
        job.status = JobStatus.CANCELLED
        job.error_message = reason


# ── CRUD ──────────────────────────────────────────────────────────────
@router.get("/brands/{brand_id}/posts", response_model=PostListResponse)
async def list_posts(
    brand_id: UUID,
    status_filter: Optional[PostStatus] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await get_brand_access(brand_id, db, user)

    query = select(Post).options(selectinload(Post.media)).where(Post.brand_id == brand_id)
    count_query = select(func.count(Post.id)).where(Post.brand_id == brand_id)
    if status_filter:
        query = query.where(Post.status == status_filter)
        count_query = count_query.where(Post.status == status_filter)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(Post.created_at.desc()).offset((page - 1) * limit).limit(limit)
    )
    return PostListResponse(
        items=[post_schema(p) for p in result.scalars().all()],
        total=total, page=page, limit=limit,
    )


@router.get("/workspaces/{workspace_id}/posts", response_model=PostListResponse)
async def list_workspace_posts(
    workspace_id: UUID,
    status_filter: Optional[PostStatus] = Query(None, alias="status"),
    brand_id: Optional[UUID] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """All posts across the workspace's brands — the dashboard and the
    "needs approval" queue. Tenancy: membership in the PATH workspace."""
    await get_workspace_member(workspace_id, db, user)

    base = select(Post).join(Brand, Brand.id == Post.brand_id).where(Brand.workspace_id == workspace_id)
    count_q = select(func.count(Post.id)).join(Brand, Brand.id == Post.brand_id).where(Brand.workspace_id == workspace_id)
    if status_filter:
        base = base.where(Post.status == status_filter)
        count_q = count_q.where(Post.status == status_filter)
    if brand_id:
        base = base.where(Post.brand_id == brand_id)
        count_q = count_q.where(Post.brand_id == brand_id)

    total = (await db.execute(count_q)).scalar() or 0
    result = await db.execute(
        base.options(selectinload(Post.media)).order_by(Post.updated_at.desc())
        .offset((page - 1) * limit).limit(limit)
    )
    return PostListResponse(items=[post_schema(p) for p in result.scalars().all()],
                            total=total, page=page, limit=limit)


@router.post("/brands/{brand_id}/posts", response_model=PostResponse,
             status_code=status.HTTP_201_CREATED)
async def create_post(brand_id: UUID, req: PostCreate, request: Request,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    brand = await get_brand_access(brand_id, db, user)

    post = Post(
        brand_id=brand_id,
        created_by=user.id,
        title=req.title,
        caption=req.caption or "",
        hashtags=req.hashtags or [],
        cta=req.cta,
        platform_targets=req.platform_targets or {},
        status=PostStatus.DRAFT,
        version_number=1,
    )
    post.content_hash = compute_content_hash(post.caption, [], post.hashtags, post.cta, post.platform_targets)
    db.add(post)
    await db.flush()

    record_audit_event(
        db, action=AuditAction.CREATE, entity_type="Post", entity_id=post.id,
        workspace_id=brand.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name, changes={"after": {"title": post.title}},
        ip_address=client_ip(request),
    )
    db.add(UsageLog(workspace_id=brand.workspace_id, user_id=user.id,
                    operation=OperationType.POST_CREATED, meta={"post_id": str(post.id)}))
    await db.commit()

    loaded = await db.scalar(select(Post).options(selectinload(Post.media)).where(Post.id == post.id))
    return post_schema(loaded)


@router.get("/posts/{post_id}", response_model=PostResponse)
async def get_post(post_id: UUID, db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_user)):
    post = await get_post_access(post_id, db, user)
    return post_schema(post)


@router.patch("/posts/{post_id}", response_model=PostResponse)
async def update_post(post_id: UUID, req: PostUpdate, request: Request,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    post = await get_post_access(post_id, db, user)
    assert_editable(post)

    prev_status = post.status
    changed = req.model_dump(exclude_unset=True)
    for field, value in changed.items():
        setattr(post, field, value)

    new_hash = current_hash(post)
    if new_hash != post.content_hash:
        post.content_hash = new_hash
        post.version_number += 1

        # Editing approved/scheduled content INVALIDATES the approval. A
        # failed post goes back to draft too — otherwise it stays "failed"
        # with content nobody approved and no way to resubmit it.
        if prev_status in (PostStatus.APPROVED, PostStatus.SCHEDULED, PostStatus.FAILED):
            post.status = PostStatus.DRAFT
            db.add(ApprovalLog(
                post_id=post.id, user_id=user.id,
                action=ApprovalAction.INVALIDATED,
                content_hash_at_action=new_hash,
                rejection_reason="Content modified after approval; reset to draft.",
            ))
            if prev_status == PostStatus.SCHEDULED:
                await cancel_open_jobs(post, db, "Cancelled: content edited after scheduling.")
                post.scheduled_at = None

    member = await get_post_membership(post, db, user)
    record_audit_event(
        db, action=AuditAction.UPDATE, entity_type="Post", entity_id=post.id,
        workspace_id=member.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name, changes={"after": {k: str(v)[:200] for k, v in changed.items()}},
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(post)
    return post_schema(post)


@router.delete("/posts/{post_id}", response_model=MessageResponse)
async def delete_post(post_id: UUID, request: Request,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    post = await get_post_access(post_id, db, user)
    member = await get_post_membership(post, db, user)
    if not can_manage(post, member):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the author or a workspace admin can delete this post.")
    if post.status == PostStatus.PUBLISHING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The post is being published right now. Wait for it to finish.")

    await cancel_open_jobs(post, db, "Cancelled: post deleted.")
    record_audit_event(
        db, action=AuditAction.DELETE, entity_type="Post", entity_id=post.id,
        workspace_id=member.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name, changes={"before": {"title": post.title, "status": post.status.value}},
        ip_address=client_ip(request),
    )
    await db.delete(post)
    await db.commit()
    return MessageResponse(message="Post deleted.")


# ── Approval workflow ─────────────────────────────────────────────────
@router.post("/posts/{post_id}/submit", response_model=PostResponse)
async def submit_for_approval(post_id: UUID, request: Request,
                              db: AsyncSession = Depends(get_db),
                              user: User = Depends(get_current_user)):
    post = await get_post_access(post_id, db, user)

    if post.status != PostStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only drafts can be submitted. Current status: {post.status.value}",
        )
    if not (post.caption or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Add a caption before submitting for approval.")

    post.status = PostStatus.PENDING_APPROVAL
    post.content_hash = current_hash(post)
    db.add(ApprovalLog(post_id=post.id, user_id=user.id,
                       action=ApprovalAction.SUBMITTED,
                       content_hash_at_action=post.content_hash))

    member = await get_post_membership(post, db, user)
    record_audit_event(
        db, action=AuditAction.SUBMIT, entity_type="Post", entity_id=post.id,
        workspace_id=member.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name, ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(post)
    return post_schema(post)


@router.post("/posts/{post_id}/withdraw", response_model=PostResponse)
async def withdraw_submission(post_id: UUID, request: Request,
                              db: AsyncSession = Depends(get_db),
                              user: User = Depends(get_current_user)):
    """Pull a pending post back to draft so it can be edited. Author or admin."""
    post = await get_post_access(post_id, db, user)
    member = await get_post_membership(post, db, user)
    if post.status != PostStatus.PENDING_APPROVAL:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Only pending posts can be withdrawn. Current status: {post.status.value}")
    if not can_manage(post, member):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the author or a workspace admin can withdraw this post.")

    post.status = PostStatus.DRAFT
    db.add(ApprovalLog(post_id=post.id, user_id=user.id,
                       action=ApprovalAction.INVALIDATED,
                       content_hash_at_action=post.content_hash,
                       rejection_reason="Withdrawn by the author before review."))
    record_audit_event(
        db, action=AuditAction.UPDATE, entity_type="Post", entity_id=post.id,
        workspace_id=member.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name, changes={"after": {"status": "draft", "reason": "withdrawn"}},
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(post)
    return post_schema(post)


@router.post("/posts/{post_id}/approve", response_model=PostResponse)
async def approve_post(post_id: UUID, request: Request,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    post = await get_post_access(post_id, db, user)
    member = await get_post_membership(post, db, user)

    # Guard 1: state machine
    if post.status != PostStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only pending posts can be approved. Current status: {post.status.value}",
        )
    # Guard 2: RBAC
    if member.role not in APPROVER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only workspace owners and admins can approve posts.")
    # Guard 3: separation of duties — per-workspace. A one-person business
    # approves its own work; an agency switches this off.
    if post.created_by == user.id:
        workspace = await db.get(Workspace, member.workspace_id)
        if workspace is None or not workspace.allow_self_approval:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot approve a post you created. Another admin or the owner must review it.",
            )
    # Guard 4: VERIFY the submitted content — never recompute-and-bless.
    # The reference is the hash recorded at SUBMIT time (an append-only
    # row), not posts.content_hash, which any edit path could move.
    submitted = await db.scalar(
        select(ApprovalLog)
        .where(ApprovalLog.post_id == post.id, ApprovalLog.action == ApprovalAction.SUBMITTED)
        .order_by(ApprovalLog.created_at.desc()).limit(1)
    )
    live_hash = current_hash(post)
    if submitted is None or submitted.content_hash_at_action != live_hash or live_hash != post.content_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Post content changed since submission. It must be resubmitted for approval.",
        )

    post.status = PostStatus.APPROVED
    db.add(ApprovalLog(post_id=post.id, user_id=user.id,
                       action=ApprovalAction.APPROVED,
                       content_hash_at_action=post.content_hash))
    record_audit_event(
        db, action=AuditAction.APPROVE, entity_type="Post", entity_id=post.id,
        workspace_id=member.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name, ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(post)
    return post_schema(post)


@router.post("/posts/{post_id}/reject", response_model=PostResponse)
async def reject_post(post_id: UUID, req: RejectRequest, request: Request,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    post = await get_post_access(post_id, db, user)
    member = await get_post_membership(post, db, user)

    if post.status != PostStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only pending posts can be rejected. Current status: {post.status.value}",
        )
    if member.role not in APPROVER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only workspace owners and admins can reject posts.")

    post.status = PostStatus.DRAFT
    db.add(ApprovalLog(post_id=post.id, user_id=user.id,
                       action=ApprovalAction.REJECTED,
                       content_hash_at_action=post.content_hash,
                       rejection_reason=req.reason))
    record_audit_event(
        db, action=AuditAction.REJECT, entity_type="Post", entity_id=post.id,
        workspace_id=member.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name, changes={"after": {"reason": req.reason[:200]}},
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(post)
    return post_schema(post)


@router.get("/posts/{post_id}/history", response_model=List[ApprovalLogResponse])
async def approval_history(post_id: UUID, db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)):
    await get_post_access(post_id, db, user)
    result = await db.execute(
        select(ApprovalLog).where(ApprovalLog.post_id == post_id)
        .order_by(ApprovalLog.created_at.desc())
    )
    return [ApprovalLogResponse.model_validate(l) for l in result.scalars().all()]


# ── Scheduling & publishing ───────────────────────────────────────────
async def _validated_accounts(post: Post, account_ids: list[UUID], db: AsyncSession,
                              require_active: bool = True) -> list[SocialAccount]:
    """TENANT ISOLATION: every target account must belong to the post's
    workspace (and be active, unless this is a validation dry run). Without
    this, a caller could publish through another tenant's connected pages."""
    brand = await db.scalar(select(Brand).where(Brand.id == post.brand_id))
    stmt = select(SocialAccount).where(
        SocialAccount.id.in_(set(account_ids)),
        SocialAccount.workspace_id == brand.workspace_id,
    )
    if require_active:
        stmt = stmt.where(SocialAccount.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    accounts = result.scalars().all()
    if len(accounts) != len(set(account_ids)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more social accounts were not found in this workspace or are inactive.",
        )
    return accounts


def _make_jobs(post: Post, accounts: list[SocialAccount], when: datetime, db: AsyncSession) -> list[PublishJob]:
    jobs = []
    for account in accounts:
        job = PublishJob(
            post_id=post.id,
            social_account_id=account.id,
            status=JobStatus.QUEUED,
            scheduled_for=when,
        )
        db.add(job)
        jobs.append(job)
    return jobs
    # NOTE: no Celery dispatch here. The Beat task dispatch_due_jobs scans
    # every minute and dispatches due jobs as immediate tasks. Long-ETA
    # dispatch on Redis is re-delivered (visibility_timeout) and lost on
    # restarts — and a down broker can't produce a "scheduled" post with no
    # task behind it.


@router.post("/posts/{post_id}/validate", response_model=ValidateResponse)
async def validate_for_accounts(post_id: UUID, req: ValidateRequest,
                                db: AsyncSession = Depends(get_db),
                                user: User = Depends(get_current_user)):
    """Dry run: what each platform would receive and whether it would be
    accepted — character limits, media requirements, account health. Read
    only; call it before schedule / publish-now so the user fixes problems
    instead of discovering them in a failed job."""
    post = await get_post_access(post_id, db, user)
    accounts = await _validated_accounts(post, req.social_account_ids, db, require_active=False)

    results = []
    # Adapters only need the media COUNT for validation; no URLs are signed.
    media_placeholders = [f"media:{m.id}" for m in post.media]
    for account in accounts:
        adapter = get_adapter(account.platform)
        caption, hashtags, cta = resolve_platform_content(post, account.platform.value)
        text = adapter.render_text(caption, hashtags, cta)
        errors = adapter.validate_text(text) + adapter.validate_media(media_placeholders)
        warnings = []
        if not account.is_active:
            errors.append(f"'{account.account_name}' is disconnected. Reconnect it first.")
        elif account.needs_reauth:
            warnings.append(f"'{account.account_name}' token expires soon; reconnect to avoid a failed publish.")
        if account.platform.value == "linkedin" and post.media:
            warnings.append("LinkedIn publishes text only in this release; media will be skipped.")
        if post.status != PostStatus.APPROVED:
            warnings.append(f"Post is '{post.status.value}'; it must be approved before scheduling.")
        results.append(AccountValidation(
            social_account_id=account.id, platform=account.platform.value,
            account_name=account.account_name, ok=not errors, errors=errors, warnings=warnings,
            rendered_text=text, character_count=len(text),
            character_limit=PLATFORM_CHAR_LIMITS.get(account.platform.value, 0),
        ))
    return ValidateResponse(post_id=post.id, ok=all(r.ok for r in results), results=results)


@router.post("/posts/{post_id}/schedule", response_model=ScheduleResponse)
async def schedule_post(post_id: UUID, req: ScheduleRequest, request: Request,
                        db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    post = await get_post_access(post_id, db, user)
    member = await get_post_membership(post, db, user)

    if post.status != PostStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only approved posts can be scheduled. Current status: {post.status.value}",
        )
    if req.scheduled_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Scheduled time must be in the future.")

    accounts = await _validated_accounts(post, req.social_account_ids, db)

    post.scheduled_at = req.scheduled_at
    post.timezone = req.timezone
    post.status = PostStatus.SCHEDULED
    jobs = _make_jobs(post, accounts, req.scheduled_at, db)
    await db.flush()

    record_audit_event(
        db, action=AuditAction.SCHEDULE, entity_type="Post", entity_id=post.id,
        workspace_id=member.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name,
        changes={"after": {"scheduled_at": req.scheduled_at.isoformat(),
                           "accounts": [str(a.id) for a in accounts]}},
        ip_address=client_ip(request),
    )
    await db.commit()
    return ScheduleResponse(
        post_id=post.id, scheduled_at=post.scheduled_at,
        jobs=[JobResponse.model_validate(j) for j in jobs],
    )


@router.post("/posts/{post_id}/publish-now", response_model=ScheduleResponse)
async def publish_now(post_id: UUID, req: PublishNowRequest, request: Request,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    post = await get_post_access(post_id, db, user)
    member = await get_post_membership(post, db, user)

    if post.status != PostStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only approved posts can be published. Current status: {post.status.value}",
        )

    accounts = await _validated_accounts(post, req.social_account_ids, db)

    now = datetime.now(timezone.utc)
    post.status = PostStatus.SCHEDULED  # worker transitions to publishing
    post.scheduled_at = now
    jobs = _make_jobs(post, accounts, now, db)
    await db.flush()

    record_audit_event(
        db, action=AuditAction.SCHEDULE, entity_type="Post", entity_id=post.id,
        workspace_id=member.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name, changes={"after": {"mode": "publish_now"}},
        ip_address=client_ip(request),
    )
    await db.commit()

    await _dispatch_now(jobs, db, post.id)
    return ScheduleResponse(
        post_id=post.id, scheduled_at=now,
        jobs=[JobResponse.model_validate(j) for j in jobs],
    )


async def _dispatch_now(jobs: list[PublishJob], db: AsyncSession, post_id: UUID) -> None:
    """Best-effort immediate dispatch (no ETA — safe). If the broker is down,
    the Beat scan picks these jobs up within a minute."""
    try:
        from app.workers.tasks.publish import publish_post_task
        for job in jobs:
            task = publish_post_task.apply_async(kwargs={"job_id": str(job.id)})
            job.celery_task_id = task.id
        await db.commit()
    except Exception:  # noqa: BLE001 — Beat scan is the safety net
        logger.warning("immediate dispatch failed for post %s; Beat will pick it up", post_id, exc_info=True)


RETRYABLE_POST_STATUSES = (PostStatus.FAILED, PostStatus.PARTIALLY_PUBLISHED)


@router.post("/posts/{post_id}/retry", response_model=ScheduleResponse)
async def retry_post(post_id: UUID, req: RetryRequest, request: Request,
                     db: AsyncSession = Depends(get_db),
                     user: User = Depends(get_current_user)):
    """Send failed jobs again, now.

    Safety rules (the "never double-post" promise):
    - Only FAILED jobs retry freely: the platform rejected them, nothing was posted.
    - NEEDS_REVIEW jobs (outcome unknown) retry only with confirm_not_posted.
    - COMPLETED jobs are never resent.
    - Content must still be exactly what was approved.
    The same job rows are re-queued, so the post's aggregate status is derived
    correctly when they finish; the previous error is kept in the audit trail.
    """
    post = await get_post_access(post_id, db, user)
    member = await get_post_membership(post, db, user)

    if post.status not in RETRYABLE_POST_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only posts that failed or partly published can be retried. Current status: {post.status.value}",
        )

    # Approval integrity: the content must still be what was approved.
    latest_approval = await db.scalar(
        select(ApprovalLog)
        .where(ApprovalLog.post_id == post.id, ApprovalLog.action == ApprovalAction.APPROVED)
        .order_by(ApprovalLog.created_at.desc()).limit(1)
    )
    if latest_approval is None or latest_approval.content_hash_at_action != current_hash(post):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This post changed after it was approved. Send it for approval again before posting.",
        )

    result = await db.execute(select(PublishJob).where(PublishJob.post_id == post.id).with_for_update())
    all_jobs = result.scalars().all()
    candidates = [j for j in all_jobs if j.status in (JobStatus.FAILED, JobStatus.NEEDS_REVIEW)]
    if req.job_ids is not None:
        wanted = set(req.job_ids)
        if wanted - {j.id for j in all_jobs}:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found for this post.")
        if any(j.id in wanted and j not in candidates for j in all_jobs):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only failed jobs can be retried. Posts that already went out are never sent twice.",
            )
        candidates = [j for j in candidates if j.id in wanted]
    if not candidates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="There is nothing to retry.")

    if any(j.status == JobStatus.NEEDS_REVIEW for j in candidates) and not req.confirm_not_posted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="We couldn't confirm whether this was posted. Check the page first, then confirm it "
                   "wasn't posted to try again.",
        )

    account_ids = {j.social_account_id for j in candidates}
    accounts = (await db.execute(select(SocialAccount).where(SocialAccount.id.in_(account_ids)))).scalars().all()
    inactive = [a.account_name for a in accounts if not a.is_active]
    if inactive or len(accounts) != len(account_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Reconnect {', '.join(inactive) or 'the account'} first, then try again.",
        )

    now = datetime.now(timezone.utc)
    previous = []
    for job in candidates:
        previous.append({"job_id": str(job.id), "status": job.status.value, "error": (job.error_message or "")[:300]})
        job.status = JobStatus.QUEUED
        job.celery_task_id = None
        job.error_message = None
        job.result = None
        job.started_at = None
        job.processed_at = None
        job.scheduled_for = now

    post.status = PostStatus.SCHEDULED  # worker moves it to publishing, then finalises
    post.scheduled_at = now
    post.retry_count += 1
    record_audit_event(
        db, action=AuditAction.SCHEDULE, entity_type="Post", entity_id=post.id,
        workspace_id=member.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name,
        changes={"after": {"mode": "retry", "confirmed_not_posted": req.confirm_not_posted,
                           "previous": previous}},
        ip_address=client_ip(request),
    )
    await db.commit()

    await _dispatch_now(candidates, db, post.id)
    return ScheduleResponse(
        post_id=post.id, scheduled_at=now,
        jobs=[JobResponse.model_validate(j) for j in candidates],
    )


@router.delete("/posts/{post_id}/schedule", response_model=MessageResponse)
async def cancel_schedule(post_id: UUID, db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_user)):
    post = await get_post_access(post_id, db, user)
    member = await get_post_membership(post, db, user)
    if post.status != PostStatus.SCHEDULED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Only scheduled posts can be unscheduled.")
    if not can_manage(post, member):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the author or a workspace admin can unschedule this post.")

    await cancel_open_jobs(post, db, "Cancelled by user.")
    post.status = PostStatus.APPROVED  # approval stays valid — content unchanged
    post.scheduled_at = None
    await db.commit()
    return MessageResponse(message="Schedule cancelled. The post remains approved.")


@router.get("/posts/{post_id}/jobs", response_model=List[JobResponse])
async def list_jobs(post_id: UUID, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    await get_post_access(post_id, db, user)
    result = await db.execute(
        select(PublishJob).where(PublishJob.post_id == post_id)
        .order_by(PublishJob.created_at.desc())
    )
    return [JobResponse.model_validate(j) for j in result.scalars().all()]
