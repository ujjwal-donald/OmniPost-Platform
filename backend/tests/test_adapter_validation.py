"""Adapter dry-run helpers used by POST /posts/{id}/validate."""
from app.services.publishing.factory import get_adapter


def test_text_limit_per_platform():
    ig = get_adapter("instagram")
    assert ig.validate_text("x" * 2_200) == []
    assert ig.validate_text("x" * 2_201)
    assert get_adapter("facebook").validate_text("x" * 2_201) == []
    assert get_adapter("linkedin").validate_text("x" * 3_001)


def test_empty_text_rejected():
    assert get_adapter("facebook").validate_text("   ")


def test_media_rules():
    assert get_adapter("instagram").validate_media([])            # IG needs an image
    assert get_adapter("instagram").validate_media(["a", "b"])    # single image in MVP
    assert get_adapter("facebook").validate_media(["a"]) == []
    assert get_adapter("linkedin").validate_media(["a", "b"]) == []  # skipped, not rejected


def test_render_text_uses_platform_emoji():
    assert "🔗 Go" in get_adapter("linkedin").render_text("c", [], "Go")
    assert "👉 Go" in get_adapter("facebook").render_text("c", [], "Go")
