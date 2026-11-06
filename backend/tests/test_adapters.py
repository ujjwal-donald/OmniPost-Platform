import pytest

from app.core.exceptions import PermanentPublishError
from app.models.enums import SocialPlatform
from app.services.publishing.facebook import FacebookAdapter
from app.services.publishing.factory import get_adapter
from app.services.publishing.instagram import InstagramAdapter
from app.services.publishing.linkedin import LinkedInAdapter


def test_factory_returns_correct_adapters():
    assert isinstance(get_adapter(SocialPlatform.FACEBOOK), FacebookAdapter)
    assert isinstance(get_adapter("instagram"), InstagramAdapter)
    assert isinstance(get_adapter(SocialPlatform.LINKEDIN), LinkedInAdapter)


def test_facebook_mock_publish():
    result = FacebookAdapter().publish(
        caption="Fresh roast is here",
        media_urls=["https://example.invalid/mock/img1"],
        access_token="mock-facebook-123",
        account_metadata={"page_id": "123"},
        hashtags=["CoffeeLove"],
        cta="Visit us",
    )
    assert result["platform_post_id"].startswith("fb_mock_")
    assert result["mode"] == "mock_simulation"
    # The adapter contract: FILTERED results only — no raw provider payloads.
    assert "provider_response" not in result


def test_instagram_requires_media():
    with pytest.raises(PermanentPublishError, match="at least one image"):
        InstagramAdapter().publish(
            caption="no media", media_urls=[], access_token="mock-ig",
            account_metadata={"ig_user_id": "9"},
        )


def test_instagram_caption_limit():
    with pytest.raises(PermanentPublishError, match="2,200"):
        InstagramAdapter().publish(
            caption="a" * 2201, media_urls=["https://example.invalid/i.jpg"],
            access_token="mock-ig", account_metadata={"ig_user_id": "9"},
        )


def test_linkedin_mock_text_only_flags_skipped_media():
    result = LinkedInAdapter().publish(
        caption="Q4 roadmap update", media_urls=["https://example.invalid/i.jpg"],
        access_token="mock-li", account_metadata={"author_urn": "urn:li:organization:1"},
    )
    assert result["platform_post_id"].startswith("urn:li:share:mock_")
    assert result["media_skipped"] is True


def test_linkedin_caption_limit():
    with pytest.raises(PermanentPublishError, match="3,000"):
        LinkedInAdapter().publish(
            caption="x" * 3001, media_urls=[], access_token="mock-li",
            account_metadata={"author_urn": "urn:li:organization:1"},
        )
