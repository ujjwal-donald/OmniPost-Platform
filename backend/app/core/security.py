"""Passwords, JWTs, refresh tokens, content hashing.

PyJWT (not python-jose: CVE-2024-33663/64) and bcrypt directly
(not passlib: unmaintained, breaks with bcrypt >= 4.1).
"""
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import jwt

from app.config import settings

_BCRYPT_ROUNDS = 12
_BCRYPT_MAX_BYTES = 72  # bcrypt silently truncates past 72 bytes — reject instead


# ── Passwords ──────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        raise ValueError("Password must be at most 72 bytes.")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode("utf-8"))
    except ValueError:
        return False


# ── JWT access tokens (short-lived, held in memory by the SPA) ────────
def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": "access",
        "iat": now,
        "exp": now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)),
        "jti": uuid.uuid4().hex,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode + verify. Raises jwt.PyJWTError on any failure.

    The algorithm allowlist is explicit — the token header is never trusted.
    """
    payload = jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        issuer=settings.JWT_ISSUER,
        audience=settings.JWT_AUDIENCE,
        options={"require": ["exp", "iat", "sub"]},
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Not an access token")
    return payload


# ── Refresh tokens (opaque; DB stores only the SHA-256 hash; the raw
#    value travels in an HttpOnly cookie) ──────────────────────────────
def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ── CSRF (double-submit cookie for the cookie-authenticated endpoints) ─
def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


# ── Content hashing (approval integrity) ──────────────────────────────
def compute_content_hash(
    caption: str,
    media_ids: list[str],
    hashtags: list[str] | None = None,
    cta: str | None = None,
    platform_targets: dict | None = None,
) -> str:
    """SHA-256 over EVERYTHING that reaches the platform: caption, sorted
    media IDs, hashtags (in order), the call-to-action and any per-platform
    overrides (canonical JSON, sorted keys).

    Media IDs (not URLs): IDs are immutable, so the hash never changes
    because a URL was re-signed or a CDN domain rotated — only a real
    content change invalidates approval. Hashtags and CTA are part of the
    published text, so leaving them out would let approved content be
    altered without re-review.
    """
    parts = [
        caption or "",
        "|".join(sorted(str(m) for m in media_ids)),
        "\x1f".join(str(h) for h in (hashtags or [])),
        cta or "",
        json.dumps(platform_targets or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    ]
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


def post_content_hash(post) -> str:
    """The content hash of a Post ORM instance (media must be loaded)."""
    return compute_content_hash(
        post.caption or "",
        [str(m.id) for m in post.media],
        post.hashtags or [],
        post.cta,
        post.platform_targets or {},
    )


def resolve_platform_content(post, platform: str) -> tuple[str, list[str], str | None]:
    """What actually goes out for one platform: the post's caption/hashtags/
    CTA unless platform_targets[platform] overrides a field."""
    override = (post.platform_targets or {}).get(platform) or {}
    caption = override.get("caption") if override.get("caption") is not None else (post.caption or "")
    hashtags = override.get("hashtags") if override.get("hashtags") is not None else (post.hashtags or [])
    cta = override.get("cta") if "cta" in override else post.cta
    return caption, list(hashtags), cta
