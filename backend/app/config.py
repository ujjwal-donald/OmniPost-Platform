"""Application settings — fail-fast at boot.

Secrets have no defaults: the app refuses to start with a clear error
instead of failing at 9am when the first scheduled post fires.
"""
from functools import lru_cache
from typing import List

from cryptography.fernet import Fernet
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_SECRETS = {
    "CHANGE_ME",
    "your-secret-key",
    "your-fernet-key",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

    # --- App ---
    APP_NAME: str = "AI Content Creator API"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"  # development | staging | production | test
    DEBUG: bool = False

    # --- Database (PostgreSQL 16) ---
    DATABASE_URL: str  # postgresql+asyncpg://user:pass@host:5432/db
    SYNC_DATABASE_URL: str = ""  # derived from DATABASE_URL when empty

    # --- Redis (Celery broker + rate limiting) ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Auth / JWT (PyJWT; python-jose is NOT used: CVE-2024-33663/64) ---
    SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    JWT_ISSUER: str = "ai-content-creator-api"
    JWT_AUDIENCE: str = "ai-content-creator-client"

    # --- Cookies (refresh token travels ONLY in an HttpOnly cookie) ---
    COOKIE_SECURE: bool = False  # MUST be true in staging/production (HTTPS)
    REFRESH_COOKIE_NAME: str = "refresh_token"
    REFRESH_COOKIE_PATH: str = "/api/v1/auth"
    CSRF_COOKIE_NAME: str = "csrf_token"
    CSRF_HEADER_NAME: str = "X-CSRF-Token"

    # --- OAuth token encryption at rest (Fernet: AES-128-CBC + HMAC-SHA256) ---
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    FERNET_KEY: str

    # --- CORS: exact origins only, never "*" with credentials ---
    ALLOWED_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Reverse proxy ---
    # Only honour X-Forwarded-For when a proxy you control overwrites it
    # (nginx, Caddy, an ALB). Off = the TCP peer address is used, which cannot
    # be spoofed. TRUSTED_PROXY_COUNT = how many proxies sit in front.
    TRUST_X_FORWARDED_FOR: bool = False
    TRUSTED_PROXY_COUNT: int = 1

    # --- Rate limiting ---
    RATE_LIMIT_AUTH_ATTEMPTS: int = 5
    RATE_LIMIT_AUTH_WINDOW_SECONDS: int = 900  # 15 minutes per IP
    RATE_LIMIT_WORKSPACE_PER_MINUTE: int = 120

    # --- Meta (Facebook / Instagram) ---
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_GRAPH_API_VERSION: str = "v21.0"  # config, never hardcoded in adapters
    META_REDIRECT_URI: str = "http://localhost:8000/api/v1/social/callback/facebook"

    # --- LinkedIn ---
    LINKEDIN_CLIENT_ID: str = ""
    LINKEDIN_CLIENT_SECRET: str = ""
    LINKEDIN_REDIRECT_URI: str = "http://localhost:8000/api/v1/social/callback/linkedin"

    # --- Frontend (OAuth callback redirects back here) ---
    FRONTEND_URL: str = "http://localhost:5173"

    # --- Media storage ---
    # "local" serves from ./uploads for development. "s3" is REQUIRED for real
    # Instagram/Facebook photo publishing (Meta fetches media by public URL).
    STORAGE_BACKEND: str = "local"  # local | s3
    S3_BUCKET: str = "ai-content-media"
    S3_REGION: str = "auto"
    S3_ENDPOINT_URL: str = ""  # empty = AWS; set for R2/MinIO
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_PRESIGN_EXPIRY_SECONDS: int = 1800  # publish-time GET URLs for Meta ingestion
    MAX_IMAGE_SIZE_MB: int = 25
    MAX_VIDEO_SIZE_MB: int = 100
    MAX_MEDIA_PER_POST: int = 10

    @field_validator("DATABASE_URL")
    @classmethod
    def _asyncpg_driver(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the asyncpg driver: postgresql+asyncpg://...")
        return v

    @field_validator("FERNET_KEY")
    @classmethod
    def _valid_fernet(cls, v: str) -> str:
        try:
            Fernet(v.encode())
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                "FERNET_KEY is not a valid Fernet key. Generate one: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            ) from exc
        return v

    @model_validator(mode="after")
    def _production_guards(self):
        if self.ENVIRONMENT == "production":
            if self.SECRET_KEY in _PLACEHOLDER_SECRETS or len(self.SECRET_KEY) < 32:
                raise ValueError("SECRET_KEY is a placeholder or shorter than 32 chars — refusing to start in production.")
            if self.DEBUG:
                raise ValueError("DEBUG must be false in production.")
            if not self.COOKIE_SECURE:
                raise ValueError("COOKIE_SECURE must be true in production (HTTPS-only refresh cookie).")
        return self

    @property
    def sync_database_url(self) -> str:
        if self.SYNC_DATABASE_URL:
            return self.SYNC_DATABASE_URL
        return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
