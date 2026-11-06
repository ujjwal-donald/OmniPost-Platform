"""Shared FastAPI dependencies — the single tenancy choke-point.

Every workspace-scoped route resolves access through get_workspace_member /
get_post_access here, never through ad-hoc joins in route bodies.
"""
import ipaddress
from uuid import UUID

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import Brand, Post, User, WorkspaceMember, WorkspaceRole

bearer_scheme = HTTPBearer(auto_error=False)

APPROVER_ROLES = (WorkspaceRole.OWNER, WorkspaceRole.ADMIN)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate the Bearer JWT and load the active user."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = UUID(payload["sub"])
    except (pyjwt.PyJWTError, KeyError, ValueError):
        raise unauthorized from None

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise unauthorized
    return user


async def get_workspace_member(
    workspace_id: UUID,
    db: AsyncSession,
    user: User,
) -> WorkspaceMember:
    """Resolve the caller's membership in a workspace. 404 (not 403) when
    absent, so outsiders can't probe which workspace IDs exist."""
    member = await db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")
    return member


def require_role(member: WorkspaceMember, *roles: WorkspaceRole) -> None:
    if member.role not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient workspace permissions for this action.",
        )


async def get_brand_access(brand_id: UUID, db: AsyncSession, user: User) -> Brand:
    """Load a brand only if the caller is a member of its workspace."""
    brand = await db.scalar(
        select(Brand)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Brand.workspace_id)
        .where(Brand.id == brand_id, WorkspaceMember.user_id == user.id)
    )
    if brand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brand not found.")
    return brand


async def get_post_access(post_id: UUID, db: AsyncSession, user: User) -> Post:
    """Load a post (media eager-loaded) only if the caller is a member of its workspace."""
    post = await db.scalar(
        select(Post)
        .options(selectinload(Post.media))
        .join(Brand, Brand.id == Post.brand_id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Brand.workspace_id)
        .where(Post.id == post_id, WorkspaceMember.user_id == user.id)
    )
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")
    return post


async def get_post_membership(post: Post, db: AsyncSession, user: User) -> WorkspaceMember:
    """The caller's membership (role) in the post's workspace."""
    member = await db.scalar(
        select(WorkspaceMember)
        .join(Brand, Brand.workspace_id == WorkspaceMember.workspace_id)
        .where(Brand.id == post.brand_id, WorkspaceMember.user_id == user.id)
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found.")
    return member


def client_ip(request: Request) -> str:
    """Best-effort client address for rate limiting and audit rows.

    X-Forwarded-For is attacker-controlled unless the app sits behind a
    proxy that overwrites it, so it is honoured ONLY when
    TRUST_X_FORWARDED_FOR is set. The value is validated as an IP literal and
    always fits the 45-char ip_address columns — a junk header must never
    turn into a 500 on /login.
    """
    peer = request.client.host if request.client else "unknown"
    if not settings.TRUST_X_FORWARDED_FOR:
        return peer[:45]
    forwarded = request.headers.get("X-Forwarded-For", "")
    # With N trusted proxies the real client is the Nth address from the right.
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if len(hops) >= settings.TRUSTED_PROXY_COUNT >= 1:
        candidate = hops[-settings.TRUSTED_PROXY_COUNT]
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return peer[:45]
