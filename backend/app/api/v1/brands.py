from typing import List
from uuid import UUID

import uuid as uuid_mod

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    client_ip, get_brand_access, get_current_user, get_workspace_member, require_role,
)
from app.core.rate_limit import workspace_rate_limit
from app.db.session import get_db
from app.models import AuditAction, Brand, User, WorkspaceRole
from app.schemas.schemas import BrandCreate, BrandLogoResponse, BrandResponse, BrandUpdate, MessageResponse
from app.services.audit import record_audit_event
from app.services.storage import get_storage

router = APIRouter(tags=["Brands"], dependencies=[Depends(workspace_rate_limit)])


@router.get("/workspaces/{workspace_id}/brands", response_model=List[BrandResponse])
async def list_brands(workspace_id: UUID, db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    await get_workspace_member(workspace_id, db, user)
    result = await db.execute(
        select(Brand)
        .where(Brand.workspace_id == workspace_id, Brand.is_active == True)  # noqa: E712
        .order_by(Brand.created_at.desc())
    )
    return [BrandResponse.model_validate(b) for b in result.scalars().all()]


@router.post("/workspaces/{workspace_id}/brands", response_model=BrandResponse,
             status_code=status.HTTP_201_CREATED)
async def create_brand(workspace_id: UUID, req: BrandCreate, request: Request,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    await get_workspace_member(workspace_id, db, user)  # any member can create brands

    brand = Brand(
        workspace_id=workspace_id,
        name=req.name.strip(),
        industry=req.industry,
        target_audience=req.target_audience,
        tone_of_voice=req.tone_of_voice,
        language=req.language or "en",
        logo_url=req.logo_url,
        brand_guidelines=req.brand_guidelines or {},
        is_active=True,
    )
    db.add(brand)
    await db.flush()
    record_audit_event(
        db, action=AuditAction.CREATE, entity_type="Brand", entity_id=brand.id,
        workspace_id=workspace_id, user_id=user.id, user_email=user.email, user_name=user.full_name,
        changes={"after": {"name": brand.name}}, ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(brand)
    return BrandResponse.model_validate(brand)


@router.get("/brands/{brand_id}", response_model=BrandResponse)
async def get_brand(brand_id: UUID, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    brand = await get_brand_access(brand_id, db, user)
    return BrandResponse.model_validate(brand)


@router.patch("/brands/{brand_id}", response_model=BrandResponse)
async def update_brand(brand_id: UUID, req: BrandUpdate, request: Request,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    brand = await get_brand_access(brand_id, db, user)

    changed = req.model_dump(exclude_unset=True)
    before = {k: getattr(brand, k) for k in changed}
    for field, value in changed.items():
        setattr(brand, field, value)

    if changed:
        record_audit_event(
            db, action=AuditAction.UPDATE, entity_type="Brand", entity_id=brand.id,
            workspace_id=brand.workspace_id, user_id=user.id, user_email=user.email,
            user_name=user.full_name, changes={"before": before, "after": changed},
            ip_address=client_ip(request),
        )
    await db.commit()
    await db.refresh(brand)
    return BrandResponse.model_validate(brand)


@router.post("/brands/{brand_id}/logo", response_model=BrandLogoResponse)
async def upload_brand_logo(brand_id: UUID, request: Request, file: UploadFile = File(...),
                            db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_user)):
    """Images only, same validation pipeline as post media. Replaces the
    previous logo file."""
    from app.api.v1.media import read_validated_upload  # local import avoids cycle at module load
    brand = await get_brand_access(brand_id, db, user)

    data, ext, _mime, _type = await read_validated_upload(file, images_only=True)
    storage_key = f"brands/{brand.id}/logo-{uuid_mod.uuid4().hex}{ext}"
    storage = get_storage()
    storage.save(storage_key, data)

    old_key = (brand.brand_guidelines or {}).get("_logo_storage_key")
    brand.logo_url = storage.display_url(storage_key)
    brand.brand_guidelines = {**(brand.brand_guidelines or {}), "_logo_storage_key": storage_key}
    record_audit_event(
        db, action=AuditAction.UPDATE, entity_type="Brand", entity_id=brand.id,
        workspace_id=brand.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name, changes={"after": {"logo_url": brand.logo_url}},
        ip_address=client_ip(request),
    )
    await db.commit()
    if old_key:
        storage.delete(old_key)
    return BrandLogoResponse(brand_id=brand.id, logo_url=brand.logo_url)


@router.delete("/brands/{brand_id}", response_model=MessageResponse)
async def deactivate_brand(brand_id: UUID, request: Request,
                           db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)):
    brand = await get_brand_access(brand_id, db, user)
    # Deactivation removes a client's whole content library from view —
    # restricted to admin/owner.
    member = await get_workspace_member(brand.workspace_id, db, user)
    require_role(member, WorkspaceRole.OWNER, WorkspaceRole.ADMIN)

    brand.is_active = False
    record_audit_event(
        db, action=AuditAction.DELETE, entity_type="Brand", entity_id=brand.id,
        workspace_id=brand.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name, changes={"before": {"is_active": True}},
        ip_address=client_ip(request),
    )
    await db.commit()
    return MessageResponse(message="Brand deactivated.")
