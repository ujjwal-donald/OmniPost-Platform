from app.models.enums import SocialPlatform
from app.services.publishing.base import SocialPublishAdapter
from app.services.publishing.facebook import FacebookAdapter
from app.services.publishing.instagram import InstagramAdapter
from app.services.publishing.linkedin import LinkedInAdapter

_ADAPTERS: dict[SocialPlatform, type[SocialPublishAdapter]] = {
    SocialPlatform.FACEBOOK: FacebookAdapter,
    SocialPlatform.INSTAGRAM: InstagramAdapter,
    SocialPlatform.LINKEDIN: LinkedInAdapter,
}


def get_adapter(platform: SocialPlatform | str) -> SocialPublishAdapter:
    if isinstance(platform, str):
        platform = SocialPlatform(platform.lower())
    try:
        return _ADAPTERS[platform]()
    except KeyError:
        raise ValueError(f"No adapter registered for platform: {platform}") from None
