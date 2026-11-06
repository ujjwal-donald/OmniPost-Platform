"""Meta compliance webhooks — REQUIRED for Meta App Review.

Both endpoints are public (Meta calls them) and authenticate via Meta's
signed_request: base64url payload + HMAC-SHA256 signature over the app
secret, verified constant-time.
"""
import base64
import hashlib
import hmac
import json
import logging
import uuid

from fastapi import APIRouter, Form, HTTPException, status
from sqlalchemy import select

from app.config import settings
from app.db.session import get_async_session_factory
from app.models import AuditAction, SocialAccount, SocialPlatform
from app.services.audit import record_audit_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _parse_signed_request(signed_request: str) -> dict:
    # An empty secret would make HMAC("") trivially forgeable — refuse to
    # process anything rather than let anyone disconnect customer accounts.
    if not settings.META_APP_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Meta integration is not configured on this server.")
    try:
        encoded_sig, payload = signed_request.split(".", 1)
        sig = base64.urlsafe_b64decode(encoded_sig + "=" * (-len(encoded_sig) % 4))
        expected = hmac.new(settings.META_APP_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("bad signature")
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return data
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Invalid signed_request") from None


async def _deactivate_user_accounts(platform_user_id: str, reason: str) -> int:
    count = 0
    async with get_async_session_factory()() as db:
        result = await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform.in_([SocialPlatform.FACEBOOK, SocialPlatform.INSTAGRAM]),
                SocialAccount.is_active == True,  # noqa: E712
            )
        )
        for account in result.scalars().all():
            meta = account.platform_metadata or {}
            if platform_user_id in (account.platform_account_id, meta.get("fb_user_id")):
                account.is_active = False
                account.needs_reauth = True
                record_audit_event(
                    db, action=AuditAction.DISCONNECT, entity_type="SocialAccount",
                    entity_id=account.id, workspace_id=account.workspace_id,
                    user_id=None, user_email="(meta-webhook)", user_name="(meta-webhook)",
                    changes={"after": {"reason": reason}},
                )
                count += 1
        await db.commit()
    return count


@router.post("/meta/deauthorize")
async def meta_deauthorize(signed_request: str = Form(...)):
    """A page admin removed the app — disable the connection immediately."""
    data = _parse_signed_request(signed_request)
    user_id = str(data.get("user_id", ""))
    count = await _deactivate_user_accounts(user_id, "User deauthorized the app on Facebook.")
    logger.info(f"[Meta webhook] Deauthorize for {user_id}: {count} account(s) disabled")
    return {"status": "ok"}


@router.post("/meta/data-deletion")
async def meta_data_deletion(signed_request: str = Form(...)):
    """Meta data-deletion request — must return a status URL + code."""
    data = _parse_signed_request(signed_request)
    user_id = str(data.get("user_id", ""))
    await _deactivate_user_accounts(user_id, "Meta data deletion request.")
    confirmation_code = uuid.uuid4().hex
    logger.info(f"[Meta webhook] Data deletion for {user_id}, code={confirmation_code}")
    return {
        "url": f"{settings.FRONTEND_URL}/data-deletion?code={confirmation_code}",
        "confirmation_code": confirmation_code,
    }
