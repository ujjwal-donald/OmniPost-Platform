"""Redis-backed fixed-window rate limiting.

Auth endpoints: per-IP (credential-stuffing defense).
Workspace routes: per-workspace (noisy-neighbor defense).
"""
import redis.asyncio as aioredis
from fastapi import HTTPException, Request

from app.config import settings
from app.core.dependencies import client_ip

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def _hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    full_key = f"rl:{key}"
    r = get_redis()
    async with r.pipeline(transaction=True) as pipe:
        pipe.incr(full_key)
        pipe.ttl(full_key)
        count, ttl = await pipe.execute()
    if ttl == -1:
        await r.expire(full_key, window_seconds)
        ttl = window_seconds
    return count <= limit, max(ttl, 0)


async def auth_rate_limit(request: Request) -> None:
    """Dependency for /login and /register. Fails open if Redis is down —
    availability of login beats a hard dependency on the limiter."""
    try:
        allowed, retry_after = await _hit(
            f"auth:{client_ip(request)}",
            settings.RATE_LIMIT_AUTH_ATTEMPTS,
            settings.RATE_LIMIT_AUTH_WINDOW_SECONDS,
        )
    except Exception:  # noqa: BLE001
        return
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


async def workspace_rate_limit(request: Request) -> None:
    """Dependency for workspace-scoped routes (path param workspace_id)."""
    key = request.path_params.get("workspace_id") or client_ip(request)
    try:
        allowed, retry_after = await _hit(f"ws:{key}", settings.RATE_LIMIT_WORKSPACE_PER_MINUTE, 60)
    except Exception:  # noqa: BLE001
        return
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded for this workspace.",
            headers={"Retry-After": str(retry_after)},
        )
