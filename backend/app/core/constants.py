"""Platform limits shared by API validation and adapters."""
from app.models.enums import SocialPlatform

PLATFORM_CHAR_LIMITS: dict[str, int] = {
    SocialPlatform.INSTAGRAM.value: 2_200,
    SocialPlatform.FACEBOOK.value: 63_206,
    SocialPlatform.LINKEDIN.value: 3_000,
}

IG_ASPECT_RATIO_MIN = 4 / 5   # 0.8 portrait
IG_ASPECT_RATIO_MAX = 1.91    # landscape

ALLOWED_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
ALLOWED_VIDEO_MIMES = frozenset({"video/mp4", "video/quicktime", "video/webm"})
ALLOWED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
ALLOWED_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm"})
