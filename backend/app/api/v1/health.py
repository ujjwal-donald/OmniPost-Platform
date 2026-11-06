"""Liveness and readiness probes."""
from fastapi import APIRouter, Response

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health() -> dict:
    """Liveness: the process is up. No external dependencies checked."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response) -> dict:
    """Readiness: PostgreSQL + Redis reachable. 503 when degraded."""
    components: dict[str, str] = {}

    try:
        from sqlalchemy import text
        from app.db.session import get_async_session_factory
        async with get_async_session_factory()() as session:
            await session.execute(text("SELECT 1"))
        components["database"] = "ok"
    except Exception:  # noqa: BLE001
        components["database"] = "unreachable"

    try:
        from app.core.rate_limit import get_redis
        await get_redis().ping()
        components["redis"] = "ok"
    except Exception:  # noqa: BLE001
        components["redis"] = "unreachable"

    all_ok = all(v == "ok" for v in components.values())
    if not all_ok:
        response.status_code = 503
    return {"status": "ready" if all_ok else "degraded", "components": components}
