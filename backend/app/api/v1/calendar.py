from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import get_current_user, get_workspace_member
from app.core.rate_limit import workspace_rate_limit
from app.db.session import get_db
from app.models import Brand, Post, User
from app.schemas.schemas import CalendarPost
from app.api.v1.posts import media_schema

router = APIRouter(tags=["Calendar"], dependencies=[Depends(workspace_rate_limit)])


@router.get("/workspaces/{workspace_id}/calendar", response_model=List[CalendarPost])
async def calendar(
    workspace_id: UUID,
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    brand_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Tenancy comes from membership in the PATH workspace — the parameter
    # only selects which of the caller's workspaces to show.
    await get_workspace_member(workspace_id, db, user)

    stmt = (
        select(Post)
        .options(selectinload(Post.media))
        .join(Brand, Brand.id == Post.brand_id)
        .where(Brand.workspace_id == workspace_id)
    )
    if brand_id:
        stmt = stmt.where(Post.brand_id == brand_id)
    if start_date and end_date:
        stmt = stmt.where(or_(
            Post.scheduled_at.between(start_date, end_date),
            Post.published_at.between(start_date, end_date),
        ))
    elif start_date:
        stmt = stmt.where(or_(Post.scheduled_at >= start_date, Post.published_at >= start_date))
    elif end_date:
        stmt = stmt.where(or_(Post.scheduled_at <= end_date, Post.published_at <= end_date))

    stmt = stmt.order_by(Post.scheduled_at.asc().nulls_last(), Post.created_at.desc())
    result = await db.execute(stmt)
    return [
        CalendarPost(
            id=p.id, title=p.title, caption=p.caption, status=p.status.value,
            scheduled_at=p.scheduled_at, published_at=p.published_at,
            brand_id=p.brand_id, media=[media_schema(m) for m in p.media],
        )
        for p in result.scalars().all()
    ]
