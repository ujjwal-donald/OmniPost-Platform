import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.core.constants import PLATFORM_CHAR_LIMITS
from app.core.exceptions import PermanentPublishError, RetryablePublishError, UnknownOutcomePublishError
from app.services.publishing.base import SocialPublishAdapter

logger = logging.getLogger(__name__)


class LinkedInAdapter(SocialPublishAdapter):
    """MVP scope: TEXT-ONLY LinkedIn posts.

    Image posts require LinkedIn's registerUpload → binary upload → URN flow;
    declaring IMAGE without it is rejected by the API. Media is skipped with
    a visible flag until that flow lands in Phase 2.
    """
    BASE_URL = "https://api.linkedin.com/v2"
    platform = "linkedin"
    cta_emoji = "🔗"

    def validate_media(self, media_urls: list[str]) -> list[str]:
        return []  # media is skipped, not rejected — the text still publishes

    def publish(self, caption, media_urls, access_token, account_metadata, hashtags=None, cta=None) -> dict[str, Any]:
        text = self.compose_text(caption, hashtags, cta, emoji="🔗")
        if len(text) > PLATFORM_CHAR_LIMITS["linkedin"]:
            raise PermanentPublishError("Post exceeds LinkedIn's 3,000 character limit.")

        if media_urls:
            logger.warning("[LinkedInAdapter] Media attached but image upload is Phase 2 — publishing text only.")

        if not settings.is_production and access_token.startswith("mock-"):
            mock_id = f"urn:li:share:mock_{uuid.uuid4().hex[:10]}"
            logger.info(f"[LinkedInAdapter] MOCK publish: {mock_id}")
            return {
                "platform_post_id": mock_id,
                "url": f"https://linkedin.com/feed/update/{mock_id}",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "mode": "mock_simulation",
                "media_skipped": bool(media_urls),
            }

        author_urn = account_metadata.get("author_urn") or (
            f"urn:li:organization:{account_metadata.get('platform_account_id')}"
        )

        payload = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        # This single POST is the irreversible call: a timeout or dropped
        # connection means the share may already exist -> unknown outcome,
        # never an automatic retry.
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(f"{self.BASE_URL}/ugcPosts", headers=headers, json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise UnknownOutcomePublishError(f"LinkedIn publish request dropped: {exc}") from exc

        if resp.status_code in (200, 201):
            urn = resp.json().get("id")
            return {
                "platform_post_id": urn,
                "url": f"https://linkedin.com/feed/update/{urn}",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "mode": "live_api",
                "media_skipped": bool(media_urls),
            }
        if resp.status_code == 429:
            raise RetryablePublishError("LinkedIn rate limit (429); will retry.")
        if resp.status_code in (401, 403):
            raise PermanentPublishError(f"LinkedIn auth/permission error: {resp.text[:300]}")
        if resp.status_code >= 500:
            raise RetryablePublishError(f"LinkedIn server error: {resp.status_code}")
        raise PermanentPublishError(f"LinkedIn publish failed: {resp.text[:300]}")
