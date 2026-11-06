"""Facebook OAuth flow + Graph API hardening.

- authorization URL carries a cryptographically random `state` stored in
  Redis (10-min TTL, one-time use) — CSRF protection on the connect flow
  mandated by RFC 6749.
- every outbound Graph API call carries appsecret_proof =
  HMAC-SHA256(access_token, app_secret), so an intercepted token is
  useless without the app secret.
- inbound webhooks are verified with a constant-time signature check.
"""
import hashlib
import hmac
import secrets

import httpx

from app.config import settings
from app.core.rate_limit import get_redis

_STATE_TTL_SECONDS = 600


def graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}{path}"


def appsecret_proof(access_token: str) -> str:
    return hmac.new(
        settings.META_APP_SECRET.encode("utf-8"),
        access_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def meta_auth_params(access_token: str) -> dict[str, str]:
    params = {"access_token": access_token}
    if settings.META_APP_SECRET:
        params["appsecret_proof"] = appsecret_proof(access_token)
    return params


def verify_meta_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Constant-time check of X-Hub-Signature-256."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(settings.META_APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256=").strip())


# ── OAuth state (Redis-backed, one-time use) ──────────────────────────
async def create_oauth_state(workspace_id: str, user_id: str) -> str:
    state = secrets.token_urlsafe(32)
    await get_redis().setex(f"oauth_state:{state}", _STATE_TTL_SECONDS, f"{workspace_id}:{user_id}")
    return state


async def consume_oauth_state(state: str) -> tuple[str, str] | None:
    key = f"oauth_state:{state}"
    value = await get_redis().get(key)
    if not value:
        return None
    await get_redis().delete(key)  # one-time use
    workspace_id, _, user_id = value.partition(":")
    return workspace_id, user_id


# ── Facebook OAuth exchanges ──────────────────────────────────────────
def facebook_authorization_url(state: str) -> str:
    # instagram_* scopes are what make the linked IG business account usable
    # for publishing; without them Meta omits instagram_business_account.
    scopes = ",".join([
        "pages_show_list", "pages_manage_posts", "pages_read_engagement", "business_management",
        "instagram_basic", "instagram_content_publish",
    ])
    return (
        f"https://www.facebook.com/{settings.META_GRAPH_API_VERSION}/dialog/oauth"
        f"?client_id={settings.META_APP_ID}"
        f"&redirect_uri={settings.META_REDIRECT_URI}"
        f"&state={state}"
        f"&scope={scopes}"
    )


async def exchange_code_for_token(code: str) -> dict:
    """code → short-lived user token → long-lived user token (~60 days)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        short = await client.get(graph_url("/oauth/access_token"), params={
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "redirect_uri": settings.META_REDIRECT_URI,
            "code": code,
        })
        short.raise_for_status()
        short_token = short.json()["access_token"]

        long = await client.get(graph_url("/oauth/access_token"), params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "fb_exchange_token": short_token,
        })
        long.raise_for_status()
        return long.json()  # {access_token, token_type, expires_in?}


async def fetch_user_id(user_token: str) -> str:
    """The Facebook user ID behind the token. Stored on every connected
    account so the deauthorize / data-deletion webhooks (which identify the
    user, not the page) can find what to disable."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(graph_url("/me"), params={**meta_auth_params(user_token), "fields": "id"})
        resp.raise_for_status()
        return str(resp.json()["id"])


async def fetch_user_pages(user_token: str) -> list[dict]:
    """Pages the user manages. Each page carries its OWN page access token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            graph_url("/me/accounts"),
            params={**meta_auth_params(user_token),
                    "fields": "id,name,access_token,instagram_business_account{id,username}"},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
