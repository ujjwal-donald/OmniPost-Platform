"""Test fixtures. Environment is set BEFORE any app import."""
import os

from cryptography.fernet import Fernet

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_content_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("META_APP_ID", "test-app-id")
os.environ.setdefault("META_APP_SECRET", "test-app-secret")
# Integration tests register many users from one client IP.
os.environ.setdefault("RATE_LIMIT_AUTH_ATTEMPTS", "10000")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    """App without external dependencies (ENVIRONMENT=test skips dev create_all)."""
    from app.main import app

    with TestClient(app) as c:
        yield c
