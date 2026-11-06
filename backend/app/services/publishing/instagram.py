import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.core.constants import PLATFORM_CHAR_LIMITS
from app.core.exceptions import PermanentPublishError, RetryablePublishError, UnknownOutcomePublishError
from app.services.meta_oauth import graph_url, meta_auth_params
from app.services.publishing.base import SocialPublishAdapter
from app.services.publishing.facebook import _classify_meta_error

logger = logging.getLogger(__name__)


def _safe_error(resp: httpx.Response) -> dict:
    """Meta error body, or an empty dict for a non-JSON (HTML 502) response —
    which _classify_meta_error then treats as transient on 5xx."""
    try:
        return resp.json().get("error", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


class InstagramAdapter(SocialPublishAdapter):
    platform = "instagram"
    CONTAINER_POLL_ATTEMPTS = 10
    CONTAINER_POLL_INTERVAL_SECONDS = 3.0

    def validate_media(self, media_urls: list[str]) -> list[str]:
        errors = []
        if not media_urls:
            errors.append("Instagram requires at least one image.")
        if len(media_urls) > 1:
            errors.append("MVP supports a single image per Instagram post; carousels arrive in Phase 2.")
        return errors

    def publish(self, caption, media_urls, access_token, account_metadata, hashtags=None, cta=None) -> dict[str, Any]:
        errors = self.validate_media(media_urls)
        if errors:
            raise PermanentPublishError("; ".join(errors))

        text = self.compose_text(caption, hashtags, cta)
        if len(text) > PLATFORM_CHAR_LIMITS["instagram"]:
            raise PermanentPublishError("Caption exceeds Instagram's 2,200 character limit.")

        if not settings.is_production and access_token.startswith("mock-"):
            mock_id = f"ig_mock_{uuid.uuid4().hex[:12]}"
            logger.info(f"[InstagramAdapter] MOCK publish: {mock_id}")
            return {
                "platform_post_id": mock_id,
                "url": f"https://instagram.com/p/{mock_id}",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "mode": "mock_simulation",
            }

        ig_user_id = account_metadata.get("ig_user_id") or account_metadata.get("platform_account_id")
        if not ig_user_id:
            raise PermanentPublishError("Missing ig_user_id in Instagram account metadata.")

        # Meta DOWNLOADS the image from this URL: it must be public/presigned.
        image_url = media_urls[0]
        if image_url.startswith("/"):
            raise PermanentPublishError(
                "Instagram requires a publicly reachable media URL; configure S3/R2 storage."
            )

        try:
            with httpx.Client(timeout=30.0) as client:
                # Step 1: create the media container
                c_resp = client.post(
                    graph_url(f"/{ig_user_id}/media"),
                    data={"image_url": image_url, "caption": text, **meta_auth_params(access_token)},
                )
                if c_resp.status_code != 200:
                    _classify_meta_error(c_resp.status_code, _safe_error(c_resp), "Instagram container")
                creation_id = c_resp.json()["id"]

                # Step 2: poll until Meta finishes ingesting the media.
                # Publishing an unfinished container fails intermittently for
                # images and always for video.
                ready = False
                for _ in range(self.CONTAINER_POLL_ATTEMPTS):
                    s_resp = client.get(
                        graph_url(f"/{creation_id}"),
                        params={"fields": "status_code", **meta_auth_params(access_token)},
                    )
                    if s_resp.status_code == 200:
                        status_code = s_resp.json().get("status_code")
                        if status_code == "FINISHED":
                            ready = True
                            break
                        if status_code == "ERROR":
                            raise PermanentPublishError("Instagram could not process the media (container ERROR).")
                    time.sleep(self.CONTAINER_POLL_INTERVAL_SECONDS)
                if not ready:
                    raise RetryablePublishError("Instagram media container not ready in time; will retry.")

                # Step 3: publish the container — the irreversible call.
                try:
                    p_resp = client.post(
                        graph_url(f"/{ig_user_id}/media_publish"),
                        data={"creation_id": creation_id, **meta_auth_params(access_token)},
                    )
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    raise UnknownOutcomePublishError(f"Instagram media_publish request dropped: {exc}") from exc
                if p_resp.status_code != 200:
                    _classify_meta_error(p_resp.status_code, _safe_error(p_resp), "Instagram publish")

                media_id = p_resp.json().get("id")
                return {
                    "platform_post_id": media_id,
                    "url": f"https://instagram.com/p/{media_id}",
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "live_api",
                }

        except httpx.TimeoutException as exc:
            raise RetryablePublishError(f"Instagram request timed out: {exc}") from exc
        except httpx.NetworkError as exc:
            raise RetryablePublishError(f"Instagram network error: {exc}") from exc
