"""Social account connections.

Real OAuth (Facebook): POST /connect returns the authorization URL as JSON
(a browser navigation can't carry a Bearer header, and an XHR can't follow
a cross-origin redirect to facebook.com — the SPA does window.location).
The callback is PUBLIC (Facebook calls it) and authenticates via the
one-time `state` parameter.

Mock connect exists for development only — in production the endpoint
does not exist (fake "Published" results would destroy customer trust).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import client_ip, get_current_user, get_workspace_member, require_role
from app.core.rate_limit import workspace_rate_limit
from app.db.session import get_db
from app.models import AuditAction, SocialAccount, SocialPlatform, User, WorkspaceRole
from app.schemas.schemas import ConnectStartResponse, MessageResponse, SocialAccountResponse
from app.services.audit import record_audit_event
from app.services.encryption import encrypt_token
from app.services import meta_oauth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/social", tags=["Social Accounts"], dependencies=[Depends(workspace_rate_limit)])


@router.get("/workspaces/{workspace_id}/accounts", response_model=List[SocialAccountResponse])
async def list_accounts(workspace_id: UUID, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    await get_workspace_member(workspace_id, db, user)
    result = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.workspace_id == workspace_id,
               SocialAccount.is_active == True)  # noqa: E712
        .order_by(SocialAccount.connected_at.desc())
    )
    return [SocialAccountResponse.model_validate(a) for a in result.scalars().all()]


@router.post("/workspaces/{workspace_id}/connect/facebook", response_model=ConnectStartResponse)
async def start_facebook_connect(workspace_id: UUID, db: AsyncSession = Depends(get_db),
                                 user: User = Depends(get_current_user)):
    member = await get_workspace_member(workspace_id, db, user)
    require_role(member, WorkspaceRole.OWNER, WorkspaceRole.ADMIN)

    if not settings.META_APP_ID or not settings.META_APP_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Facebook integration is not configured on this server.")

    # Cryptographic one-time state — CSRF protection on the OAuth flow.
    state = await meta_oauth.create_oauth_state(str(workspace_id), str(user.id))
    return ConnectStartResponse(authorization_url=meta_oauth.facebook_authorization_url(state))


@router.get("/callback/facebook")
async def facebook_callback(
    state: str = Query(...),
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """PUBLIC endpoint — Facebook redirects the browser here.
    Authentication = the one-time state parameter."""
    consumed = await meta_oauth.consume_oauth_state(state)
    if consumed is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Invalid or expired OAuth state.")
    workspace_id, user_id = consumed

    frontend = f"{settings.FRONTEND_URL}/social-accounts"
    if error or not code:
        return RedirectResponse(url=f"{frontend}?connected=0&reason=denied")

    try:
        token_data = await meta_oauth.exchange_code_for_token(code)
        user_token = token_data["access_token"]
        expires_in = int(token_data.get("expires_in") or 60 * 24 * 3600)
        fb_user_id = await meta_oauth.fetch_user_id(user_token)
        pages = await meta_oauth.fetch_user_pages(user_token)
    except Exception:  # noqa: BLE001
        logger.exception("Facebook OAuth exchange failed")
        return RedirectResponse(url=f"{frontend}?connected=0&reason=exchange_failed")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ws_uuid = UUID(workspace_id)

    async def upsert(platform: SocialPlatform, account_id: str, name: str, account_type: str,
                     token: str, metadata: dict) -> None:
        existing = await db.scalar(
            select(SocialAccount).where(
                SocialAccount.workspace_id == ws_uuid,
                SocialAccount.platform == platform,
                SocialAccount.platform_account_id == account_id,
            )
        )
        if existing:  # reconnect: refresh the token in place
            existing.access_token_encrypted = encrypt_token(token)
            existing.token_expires_at = expires_at
            existing.platform_metadata = metadata
            existing.account_name = name
            existing.is_active = True
            existing.needs_reauth = False
        else:
            db.add(SocialAccount(
                workspace_id=ws_uuid, platform=platform, platform_account_id=account_id,
                account_name=name, account_type=account_type,
                access_token_encrypted=encrypt_token(token), token_expires_at=expires_at,
                platform_metadata=metadata, is_active=True,
            ))

    connected = 0
    for page in pages:
        page_token = page.get("access_token")
        if not page_token:
            continue
        base_meta = {"page_id": page["id"], "fb_user_id": fb_user_id}
        ig = page.get("instagram_business_account") or {}

        await upsert(SocialPlatform.FACEBOOK, page["id"], page.get("name", "Facebook Page"), "page",
                     page_token, {**base_meta, "ig_user_id": ig.get("id")} if ig else base_meta)
        connected += 1

        # The IG business account publishes with the PAGE token, so it gets
        # its own row (own platform, own adapter) sharing that token.
        if ig.get("id"):
            await upsert(SocialPlatform.INSTAGRAM, ig["id"],
                         f"@{ig['username']}" if ig.get("username") else f"{page.get('name', 'Page')} (Instagram)",
                         "business", page_token, {**base_meta, "ig_user_id": ig["id"]})
            connected += 1

    record_audit_event(
        db, action=AuditAction.CONNECT, entity_type="SocialAccount",
        entity_id=UUID(workspace_id), workspace_id=UUID(workspace_id),
        user_id=UUID(user_id), user_email="(oauth-callback)", user_name="(oauth-callback)",
        changes={"after": {"pages_connected": connected}},
    )
    await db.commit()
    return RedirectResponse(url=f"{frontend}?connected={connected}")


# ── Development-only mock connect ─────────────────────────────────────
class MockConnectRequest(BaseModel):
    platform: SocialPlatform
    account_name: str
    platform_account_id: str
    account_type: Optional[str] = "page"


@router.post("/workspaces/{workspace_id}/connect/mock", response_model=SocialAccountResponse,
             status_code=status.HTTP_201_CREATED)
async def mock_connect(workspace_id: UUID, req: MockConnectRequest, request: Request,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """DEVELOPMENT/STAGING ONLY — enables end-to-end testing without Meta
    credentials. Hidden in production."""
    if settings.is_production:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

    member = await get_workspace_member(workspace_id, db, user)
    require_role(member, WorkspaceRole.OWNER, WorkspaceRole.ADMIN)

    existing = await db.scalar(
        select(SocialAccount).where(
            SocialAccount.workspace_id == workspace_id,
            SocialAccount.platform == req.platform,
            SocialAccount.platform_account_id == req.platform_account_id,
        )
    )
    if existing and existing.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="This account is already connected to the workspace.")
    if existing:  # previously disconnected: reactivate in place (unique constraint)
        existing.is_active = True
        existing.needs_reauth = False
        existing.account_name = req.account_name
        existing.token_expires_at = datetime.now(timezone.utc) + timedelta(days=60)
        record_audit_event(
            db, action=AuditAction.CONNECT, entity_type="SocialAccount", entity_id=existing.id,
            workspace_id=workspace_id, user_id=user.id, user_email=user.email, user_name=user.full_name,
            changes={"after": {"mock": True, "reconnected": True}}, ip_address=client_ip(request),
        )
        await db.commit()
        await db.refresh(existing)
        return SocialAccountResponse.model_validate(existing)

    account = SocialAccount(
        workspace_id=workspace_id,
        platform=req.platform,
        platform_account_id=req.platform_account_id,
        account_name=req.account_name,
        account_type=req.account_type,
        access_token_encrypted=encrypt_token(f"mock-{req.platform.value}-{req.platform_account_id}"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=60),
        platform_metadata={"page_id": req.platform_account_id, "ig_user_id": req.platform_account_id},
        is_active=True,
    )
    db.add(account)
    record_audit_event(
        db, action=AuditAction.CONNECT, entity_type="SocialAccount", entity_id=workspace_id,
        workspace_id=workspace_id, user_id=user.id, user_email=user.email, user_name=user.full_name,
        changes={"after": {"mock": True, "platform": req.platform.value}},
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(account)
    return SocialAccountResponse.model_validate(account)


@router.delete("/accounts/{account_id}", response_model=MessageResponse)
async def disconnect_account(account_id: UUID, request: Request,
                             db: AsyncSession = Depends(get_db),
                             user: User = Depends(get_current_user)):
    account = await db.scalar(select(SocialAccount).where(SocialAccount.id == account_id))
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")

    member = await get_workspace_member(account.workspace_id, db, user)
    require_role(member, WorkspaceRole.OWNER, WorkspaceRole.ADMIN)

    account.is_active = False
    record_audit_event(
        db, action=AuditAction.DISCONNECT, entity_type="SocialAccount", entity_id=account.id,
        workspace_id=account.workspace_id, user_id=user.id, user_email=user.email,
        user_name=user.full_name, ip_address=client_ip(request),
    )
    await db.commit()
    return MessageResponse(message=f"Disconnected {account.account_name}.")
