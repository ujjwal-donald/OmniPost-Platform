from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Shared limits (mirror the VARCHAR widths in the migration) ────────
NAME_MAX = 255
SHORT_MAX = 100
URL_MAX = 500
CAPTION_MAX = 63_206          # Facebook's limit, the largest of the three platforms
HASHTAG_MAX_COUNT = 30        # Instagram's hard cap
HASHTAG_MAX_LEN = 100


def _reject_null(v, field: str):
    """Optional-for-PATCH fields may be OMITTED but never sent as null —
    the column is NOT NULL and a null would surface as a 500."""
    if v is None:
        raise ValueError(f"{field} cannot be null; omit the field to leave it unchanged")
    return v


class PlatformOverride(BaseModel):
    """Per-platform variant. Any field left out inherits from the post."""
    caption: Optional[str] = Field(None, max_length=CAPTION_MAX)
    hashtags: Optional[list[str]] = None
    cta: Optional[str] = Field(None, max_length=NAME_MAX)

    @field_validator("hashtags")
    @classmethod
    def clean(cls, v):
        return _clean_hashtags(v) if v is not None else v


def _clean_targets(v):
    """platform_targets: {"instagram": {caption?, hashtags?, cta?}, ...}.
    Keys must be known platforms; empty overrides are dropped."""
    if v is None:
        return None
    if not isinstance(v, dict):
        raise ValueError("platform_targets must be an object keyed by platform")
    out = {}
    valid = {p.value for p in SocialPlatform}
    for key, val in v.items():
        if key not in valid:
            raise ValueError(f"Unknown platform '{key}'. Use one of: {', '.join(sorted(valid))}")
        if val is None:
            continue
        # exclude_UNSET, not exclude_none: an explicit null means "clear this
        # field for this platform" and must survive; an omitted field inherits.
        cleaned = PlatformOverride.model_validate(val).model_dump(exclude_unset=True)
        if cleaned:
            out[key] = cleaned
    return out


def _clean_hashtags(v: list[str]) -> list[str]:
    out = []
    for tag in v:
        tag = tag.strip().lstrip("#")
        if not tag:
            continue
        if len(tag) > HASHTAG_MAX_LEN:
            raise ValueError(f"Hashtag too long (max {HASHTAG_MAX_LEN} characters)")
        if any(ch.isspace() for ch in tag):
            raise ValueError(f"Hashtag '{tag}' must not contain spaces")
        out.append(tag)
    if len(out) > HASHTAG_MAX_COUNT:
        raise ValueError(f"At most {HASHTAG_MAX_COUNT} hashtags are allowed")
    return out

from app.models.enums import OperationType, SocialPlatform, ThemePreference, WorkspaceRole


# ── Auth ──────────────────────────────────────────────────────────────
# NOTE: refresh tokens NEVER appear in response bodies. They travel in an
# HttpOnly, SameSite=strict cookie set by the auth endpoints.

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str = Field(..., max_length=NAME_MAX)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes")
        return v

    @field_validator("full_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Full name cannot be empty")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    avatar_url: Optional[str] = None
    is_active: bool
    theme_preference: ThemePreference
    created_at: datetime

    model_config = {"from_attributes": True}


class UserPreferencesUpdate(BaseModel):
    """PATCH /auth/me — the caller's own UI preferences. Omit a field to keep it."""
    theme_preference: Optional[ThemePreference] = None

    model_config = {"extra": "forbid"}

    @field_validator("theme_preference", mode="before")
    @classmethod
    def no_null(cls, v):
        return _reject_null(v, "theme_preference")


class AuthResponse(BaseModel):
    user: UserResponse
    access_token: str
    token_type: str = "bearer"


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Workspaces ────────────────────────────────────────────────────────
class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=NAME_MAX)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Workspace name cannot be empty")
        return v


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=NAME_MAX)
    # Owner-only. True lets a post's author approve it (one-person business);
    # false enforces author != approver (agency / compliance).
    allow_self_approval: Optional[bool] = None

    @field_validator("name", mode="before")
    @classmethod
    def no_null(cls, v):
        return _reject_null(v, "name")


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    owner_id: UUID
    allow_self_approval: bool = True
    created_at: datetime

    model_config = {"from_attributes": True}


class MemberResponse(BaseModel):
    id: UUID
    user_id: UUID
    role: str
    joined_at: datetime
    email: str
    full_name: str
    avatar_url: Optional[str] = None


class AddMemberRequest(BaseModel):
    email: EmailStr
    role: WorkspaceRole = WorkspaceRole.MEMBER


# ── Brands ────────────────────────────────────────────────────────────
class BrandCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=NAME_MAX)
    industry: Optional[str] = Field(None, max_length=SHORT_MAX)
    target_audience: Optional[str] = Field(None, max_length=5_000)
    tone_of_voice: Optional[str] = Field(None, max_length=SHORT_MAX)
    language: str = Field("en", min_length=2, max_length=10)
    logo_url: Optional[str] = Field(None, max_length=URL_MAX)
    brand_guidelines: Optional[dict] = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Brand name cannot be empty")
        return v


class BrandUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=NAME_MAX)
    industry: Optional[str] = Field(None, max_length=SHORT_MAX)
    target_audience: Optional[str] = Field(None, max_length=5_000)
    tone_of_voice: Optional[str] = Field(None, max_length=SHORT_MAX)
    language: Optional[str] = Field(None, min_length=2, max_length=10)
    logo_url: Optional[str] = Field(None, max_length=URL_MAX)
    brand_guidelines: Optional[dict] = None

    @field_validator("name", "language", "brand_guidelines", mode="before")
    @classmethod
    def no_null(cls, v, info):
        return _reject_null(v, info.field_name)


class BrandLogoResponse(BaseModel):
    brand_id: UUID
    logo_url: str


class BrandResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    industry: Optional[str] = None
    target_audience: Optional[str] = None
    tone_of_voice: Optional[str] = None
    language: str
    logo_url: Optional[str] = None
    brand_guidelines: Optional[dict] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Posts ─────────────────────────────────────────────────────────────
class PostMediaResponse(BaseModel):
    id: UUID
    media_url: str  # display URL resolved by the storage service
    media_type: str
    sort_order: int
    alt_text: Optional[str] = None


class PostCreate(BaseModel):
    title: Optional[str] = Field(None, max_length=NAME_MAX)
    caption: str = Field("", max_length=CAPTION_MAX)
    hashtags: list[str] = []
    cta: Optional[str] = Field(None, max_length=NAME_MAX)
    platform_targets: Optional[dict] = None

    @field_validator("hashtags")
    @classmethod
    def clean_hashtags(cls, v: list[str]) -> list[str]:
        return _clean_hashtags(v)

    @field_validator("platform_targets")
    @classmethod
    def clean_targets(cls, v):
        return _clean_targets(v)


class PostUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=NAME_MAX)
    caption: Optional[str] = Field(None, max_length=CAPTION_MAX)
    hashtags: Optional[list[str]] = None
    cta: Optional[str] = Field(None, max_length=NAME_MAX)
    platform_targets: Optional[dict] = None

    @field_validator("caption", "hashtags", "platform_targets", mode="before")
    @classmethod
    def no_null(cls, v, info):
        return _reject_null(v, info.field_name)

    @field_validator("hashtags")
    @classmethod
    def clean_hashtags(cls, v):
        return _clean_hashtags(v) if v is not None else v

    @field_validator("platform_targets")
    @classmethod
    def clean_targets(cls, v):
        return _clean_targets(v)


class PostResponse(BaseModel):
    id: UUID
    brand_id: UUID
    created_by: UUID
    title: Optional[str] = None
    caption: str
    hashtags: list[str]
    cta: Optional[str] = None
    status: str
    content_hash: Optional[str] = None
    version_number: int
    platform_targets: Optional[dict] = None
    scheduled_at: Optional[datetime] = None
    timezone: Optional[str] = None
    published_at: Optional[datetime] = None
    publish_result: Optional[dict] = None
    retry_count: int
    created_at: datetime
    updated_at: datetime
    media: list[PostMediaResponse] = []


class PostListResponse(BaseModel):
    items: list[PostResponse]
    total: int
    page: int
    limit: int


class RejectRequest(BaseModel):
    reason: str = Field(..., max_length=2_000)

    @field_validator("reason")
    @classmethod
    def reason_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("A rejection reason is required")
        return v


class ApprovalLogResponse(BaseModel):
    id: UUID
    post_id: UUID
    user_id: UUID
    action: str
    content_hash_at_action: Optional[str] = None
    rejection_reason: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Scheduling ────────────────────────────────────────────────────────
class ScheduleRequest(BaseModel):
    scheduled_at: datetime
    timezone: str = Field("UTC", max_length=50)
    social_account_ids: list[UUID] = Field(..., max_length=20)

    @field_validator("scheduled_at")
    @classmethod
    def must_be_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("scheduled_at must include a timezone offset (ISO 8601)")
        return v.astimezone(timezone.utc)

    @field_validator("social_account_ids")
    @classmethod
    def at_least_one(cls, v: list[UUID]) -> list[UUID]:
        if not v:
            raise ValueError("Select at least one social account")
        return v


class RetryRequest(BaseModel):
    """Retry a failed (or partly published) post.

    job_ids: which failed jobs to retry; omitted = every failed job.
    confirm_not_posted: required when any chosen job is needs_review — the
    platform may already have published it, so a human must have checked.
    """
    job_ids: Optional[list[UUID]] = Field(None, max_length=20)
    confirm_not_posted: bool = False


class PublishNowRequest(BaseModel):
    social_account_ids: list[UUID] = Field(..., max_length=20)

    @field_validator("social_account_ids")
    @classmethod
    def at_least_one(cls, v: list[UUID]) -> list[UUID]:
        if not v:
            raise ValueError("Select at least one social account")
        return v


class JobResponse(BaseModel):
    id: UUID
    social_account_id: UUID
    status: str
    error_message: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    result: Optional[dict] = None

    model_config = {"from_attributes": True}


class ScheduleResponse(BaseModel):
    post_id: UUID
    scheduled_at: Optional[datetime] = None
    jobs: list[JobResponse]


# ── Social accounts ───────────────────────────────────────────────────
class SocialAccountResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    platform: str
    account_name: str
    account_type: Optional[str] = None
    platform_account_id: str
    is_active: bool
    needs_reauth: bool
    token_expires_at: Optional[datetime] = None
    connected_at: datetime

    model_config = {"from_attributes": True}


class ConnectStartResponse(BaseModel):
    authorization_url: str


# ── Pre-publish validation ────────────────────────────────────────────
class ValidateRequest(BaseModel):
    social_account_ids: list[UUID] = Field(..., min_length=1, max_length=20)


class AccountValidation(BaseModel):
    social_account_id: UUID
    platform: str
    account_name: str
    ok: bool
    errors: list[str] = []
    warnings: list[str] = []
    rendered_text: str  # exactly what the adapter would send
    character_count: int
    character_limit: int


class ValidateResponse(BaseModel):
    post_id: UUID
    ok: bool
    results: list[AccountValidation]


# ── Usage ─────────────────────────────────────────────────────────────
class UsageSummaryResponse(BaseModel):
    workspace_id: UUID
    days: int
    counts: dict[str, int]          # operation -> count (every OperationType present, 0 if none)
    media_bytes: int                # sum of uploaded bytes in the window
    ai_generations: int             # reserved for Phase 2 credits; always 0 in Phase 1


# ── Media ─────────────────────────────────────────────────────────────
class MediaReorderRequest(BaseModel):
    post_id: UUID
    media_ids: list[UUID] = Field(..., min_length=1, max_length=50)


class MediaUploadResponse(BaseModel):
    id: UUID
    media_url: str
    media_type: str
    metadata: Optional[dict] = None


# ── Calendar ──────────────────────────────────────────────────────────
class CalendarPost(BaseModel):
    id: UUID
    title: Optional[str] = None
    caption: str
    status: str
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    brand_id: UUID
    media: list[PostMediaResponse] = []


# ── Templates ─────────────────────────────────────────────────────────
class CaptionTemplateResponse(BaseModel):
    id: UUID
    industry: str
    category: str
    title: str
    template_text: str
    suggested_hashtags: list[str] = []
    suggested_cta: Optional[str] = None
    is_active: bool

    model_config = {"from_attributes": True}


class CaptionTemplateListResponse(BaseModel):
    items: list[CaptionTemplateResponse]
    total: int
    industries: list[str] = []
    categories: list[str] = []


# ── Common ────────────────────────────────────────────────────────────
class MessageResponse(BaseModel):
    message: str
