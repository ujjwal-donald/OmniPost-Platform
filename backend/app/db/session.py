"""Two session factories — the async/sync split, explicit from day one.

FastAPI request handlers use the ASYNC engine (asyncpg). Celery workers use
the SYNC engine (psycopg2): Celery tasks cannot be `async def`, and sharing
an AsyncSession with a worker breaks connection pooling.

All queries are written in SQLAlchemy 2.0 style — select(Model).where(...) —
which runs identically on both session types.
"""
from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


@lru_cache
def get_async_engine():
    return create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        # Production: enforce TLS at the DSN level (?ssl=verify-full)
    )


@lru_cache
def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_async_engine(), expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request; commit is explicit in routes."""
    async with get_async_session_factory()() as session:
        yield session


@lru_cache
def get_sync_engine():
    return create_engine(
        settings.sync_database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


@lru_cache
def get_sync_session_factory() -> sessionmaker[Session]:
    return sessionmaker(get_sync_engine(), expire_on_commit=False, autoflush=False)


@contextmanager
def worker_session() -> Generator[Session, None, None]:
    """Celery task session: commit on success, rollback on error."""
    session = get_sync_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
