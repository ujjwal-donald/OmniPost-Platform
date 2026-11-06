from abc import ABC, abstractmethod
from typing import Any

from app.core.constants import PLATFORM_CHAR_LIMITS
from app.core.exceptions import PermanentPublishError, RetryablePublishError  # noqa: F401 (re-export)


class SocialPublishAdapter(ABC):
    """Base for platform publishers.

    publish() receives media as PUBLICLY FETCHABLE URLs already resolved by
    the storage service (presigned S3 GETs). Adapters never see storage keys.

    Returns a FILTERED result only — {"platform_post_id", "url",
    "published_at"} — raw provider responses are never persisted upstream.

    Raises RetryablePublishError (rate limit, timeout, 5xx) or
    PermanentPublishError (auth, permissions, content policy).
    """

    @abstractmethod
    def publish(
        self,
        caption: str,
        media_urls: list[str],
        access_token: str,
        account_metadata: dict[str, Any],
        hashtags: list[str] | None = None,
        cta: str | None = None,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def validate_media(self, media_urls: list[str]) -> list[str]:
        """Platform constraint check. Returns error strings (empty = valid)."""
        ...

    platform: str = ""        # set by each adapter; keys PLATFORM_CHAR_LIMITS
    cta_emoji: str = "👉"

    def render_text(self, caption: str, hashtags: list[str] | None, cta: str | None) -> str:
        return self.compose_text(caption, hashtags, cta, emoji=self.cta_emoji)

    def validate_text(self, text: str) -> list[str]:
        limit = PLATFORM_CHAR_LIMITS.get(self.platform)
        if limit and len(text) > limit:
            return [f"Text is {len(text):,} characters; the {self.platform} limit is {limit:,}."]
        if not text.strip():
            return ["Post text is empty."]
        return []

    @staticmethod
    def compose_text(caption: str, hashtags: list[str] | None, cta: str | None, emoji: str = "👉") -> str:
        text = caption or ""
        if hashtags:
            tags = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags)
            text = f"{text}\n\n{tags}"
        if cta:
            text = f"{text}\n\n{emoji} {cta}"
        return text
