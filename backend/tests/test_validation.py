"""Request-schema and client-IP hardening: bad input must be a 4xx, never a 500."""
import pytest
from pydantic import ValidationError

from app.schemas.schemas import BrandUpdate, PostCreate, PostUpdate, WorkspaceCreate


def test_null_caption_rejected_on_patch():
    with pytest.raises(ValidationError):
        PostUpdate.model_validate({"caption": None})


def test_null_hashtags_rejected_on_patch():
    with pytest.raises(ValidationError):
        PostUpdate.model_validate({"hashtags": None})


def test_omitted_fields_still_allowed_on_patch():
    assert PostUpdate.model_validate({}).model_dump(exclude_unset=True) == {}


def test_title_length_capped():
    with pytest.raises(ValidationError):
        PostCreate.model_validate({"title": "t" * 256})


def test_hashtags_normalised_and_capped():
    p = PostCreate.model_validate({"hashtags": ["#Sale", " promo ", ""]})
    assert p.hashtags == ["Sale", "promo"]
    with pytest.raises(ValidationError):
        PostCreate.model_validate({"hashtags": [f"t{i}" for i in range(31)]})
    with pytest.raises(ValidationError):
        PostCreate.model_validate({"hashtags": ["two words"]})


def test_brand_language_null_rejected():
    with pytest.raises(ValidationError):
        BrandUpdate.model_validate({"language": None})


def test_blank_workspace_name_rejected():
    with pytest.raises(ValidationError):
        WorkspaceCreate.model_validate({"name": "   "})


# ── client_ip ─────────────────────────────────────────────────────────
class _Req:
    def __init__(self, xff=None, peer="10.0.0.9"):
        self.headers = {"X-Forwarded-For": xff} if xff else {}
        self.client = type("c", (), {"host": peer})()


def test_xff_ignored_by_default(monkeypatch):
    from app.config import settings
    from app.core.dependencies import client_ip

    monkeypatch.setattr(settings, "TRUST_X_FORWARDED_FOR", False)
    assert client_ip(_Req(xff="1.2.3.4")) == "10.0.0.9"


def test_xff_honoured_behind_trusted_proxy(monkeypatch):
    from app.config import settings
    from app.core.dependencies import client_ip

    monkeypatch.setattr(settings, "TRUST_X_FORWARDED_FOR", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
    # client -> (spoofed hop) -> our proxy appends the real peer last
    assert client_ip(_Req(xff="6.6.6.6, 1.2.3.4")) == "1.2.3.4"


def test_garbage_xff_never_exceeds_column(monkeypatch):
    from app.config import settings
    from app.core.dependencies import client_ip

    monkeypatch.setattr(settings, "TRUST_X_FORWARDED_FOR", True)
    assert len(client_ip(_Req(xff="a" * 300))) <= 45
    assert client_ip(_Req(xff="a" * 300)) == "10.0.0.9"


def test_platform_targets_keys_validated():
    with pytest.raises(ValidationError):
        PostCreate.model_validate({"platform_targets": {"tiktok": {"caption": "x"}}})
    p = PostCreate.model_validate({"platform_targets": {"instagram": {"hashtags": ["#A", "b"]}, "facebook": {}}})
    assert p.platform_targets == {"instagram": {"hashtags": ["A", "b"]}}  # empty override dropped


def test_platform_override_length_capped():
    with pytest.raises(ValidationError):
        PostCreate.model_validate({"platform_targets": {"linkedin": {"cta": "x" * 300}}})


def test_platform_override_explicit_null_is_kept():
    """null = clear for this platform; omitted = inherit. They must differ."""
    p = PostCreate.model_validate({"platform_targets": {"linkedin": {"caption": "x", "cta": None}}})
    assert p.platform_targets == {"linkedin": {"caption": "x", "cta": None}}


def test_theme_preference_accepts_known_values():
    from app.models.enums import ThemePreference
    from app.schemas.schemas import UserPreferencesUpdate

    for v in ("light", "dark", "system"):
        assert UserPreferencesUpdate.model_validate({"theme_preference": v}).theme_preference == ThemePreference(v)


def test_theme_preference_rejects_unknown_null_and_extra_fields():
    from app.schemas.schemas import UserPreferencesUpdate

    for bad in ({"theme_preference": "blue"}, {"theme_preference": None}, {"email": "x@example.com"}):
        with pytest.raises(ValidationError):
            UserPreferencesUpdate.model_validate(bad)
