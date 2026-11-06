from fastapi import APIRouter

from app.api.v1 import (
    auth, brands, calendar, health, media, posts, social_accounts,
    templates, webhooks, workspaces,
)

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(workspaces.router)
api_v1_router.include_router(brands.router)
api_v1_router.include_router(posts.router)
api_v1_router.include_router(calendar.router)
api_v1_router.include_router(social_accounts.router)
api_v1_router.include_router(media.router)
api_v1_router.include_router(templates.router)
api_v1_router.include_router(webhooks.router)
