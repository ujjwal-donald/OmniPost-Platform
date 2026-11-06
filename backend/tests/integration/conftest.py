"""Database-backed integration tests.

Runs the real API against a throwaway PostgreSQL database built with the
real Alembic migrations. Needs `docker compose up -d postgres redis`; if
PostgreSQL isn't reachable every test here is SKIPPED (unit tests still run).

Safety: the database is dropped and recreated per session, so its name MUST
end in `_test` — the fixture refuses anything else.
"""
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _sync_url() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)


@pytest.fixture(scope="session")
def database():
    url = make_url(_sync_url())
    if not (url.database or "").endswith("_test"):
        pytest.fail(f"Refusing to recreate database {url.database!r}: integration tests need a *_test database.")

    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not reachable ({exc.__class__.__name__}); start it with docker compose up -d postgres")
    finally:
        admin.dispose()

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")

    # Old queued Celery messages from earlier runs must not linger in the test broker.
    try:
        import redis

        redis.Redis.from_url(os.environ["REDIS_URL"]).flushdb()
    except Exception:  # noqa: BLE001
        pass
    yield url


@pytest.fixture(scope="session")
def api(database):
    """One TestClient for the session (keeps a single event loop for asyncpg)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db_exec(database):
    """Run raw SQL against the test DB — for setting up states the API can't produce."""
    engine = create_engine(_sync_url())

    def run(sql: str, **params):
        with engine.begin() as conn:
            return conn.execute(text(sql), params)

    yield run
    engine.dispose()


# ── Helpers ────────────────────────────────────────────────────────────
@dataclass
class Actor:
    headers: dict
    user_id: str
    email: str
    workspace_id: str


def register(api, name: str = "Owner") -> Actor:
    email = f"{uuid.uuid4().hex[:10]}@example.com"
    r = api.post("/api/v1/auth/register", json={"email": email, "password": "password123", "full_name": name})
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    ws = api.get("/api/v1/workspaces", headers=headers).json()[0]["id"]
    return Actor(headers=headers, user_id=body["user"]["id"], email=email, workspace_id=ws)


def run_job(job_id: str) -> dict:
    """Execute one publish job in-process, exactly as the Celery worker would."""
    from app.workers.tasks.publish import publish_post_task

    return publish_post_task.apply(kwargs={"job_id": job_id}).get()
