"""Smart Templates — the MVP's stand-in for AI generation.

READ-ONLY through the API: caption_templates is a GLOBAL table shared by
every tenant, so an open write endpoint would let any customer inject
content into every other customer's Post Studio. Content is managed
through seed data (seeds/seed.py).
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models import CaptionTemplate, TemplateCategory, User
from app.schemas.schemas import CaptionTemplateListResponse, CaptionTemplateResponse

router = APIRouter(prefix="/templates", tags=["Templates"])


@router.get("", response_model=CaptionTemplateListResponse)
async def list_templates(
    industry: Optional[str] = Query(None),
    category: Optional[TemplateCategory] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(CaptionTemplate).where(CaptionTemplate.is_active == True)  # noqa: E712
    if industry:
        stmt = stmt.where(func.lower(CaptionTemplate.industry) == industry.lower().strip())
    if category:
        stmt = stmt.where(CaptionTemplate.category == category)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            CaptionTemplate.title.ilike(pattern) | CaptionTemplate.template_text.ilike(pattern)
        )

    result = await db.execute(stmt.order_by(CaptionTemplate.industry, CaptionTemplate.category))
    templates = result.scalars().all()

    industries = await db.execute(
        select(distinct(CaptionTemplate.industry)).where(CaptionTemplate.is_active == True)  # noqa: E712
    )
    return CaptionTemplateListResponse(
        items=[CaptionTemplateResponse.model_validate(t) for t in templates],
        total=len(templates),
        industries=sorted(row[0] for row in industries.all() if row[0]),
        categories=[c.value for c in TemplateCategory],
    )


@router.get("/{template_id}", response_model=CaptionTemplateResponse)
async def get_template(template_id: UUID, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    template = await db.scalar(select(CaptionTemplate).where(CaptionTemplate.id == template_id))
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found.")
    return CaptionTemplateResponse.model_validate(template)
