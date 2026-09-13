"""Async engine/session factory and shared database helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def utcnow() -> datetime:
    """Naive UTC now. SQLite has no tz support, so store UTC without offset."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime) -> datetime:
    """Normalize any datetime to naive UTC for storage/comparison."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a request-scoped async session."""
    async with SessionLocal() as session:
        yield session


async def fetch_with_retry(
    db: AsyncSession,
    statement,
    *,
    retry_seconds: float = 2.0,
    retry_step: float = 0.05,
):
    """Re-read a row after a lost check-then-insert race.

    The concurrent transaction that won the race may not be committed yet
    (Postgres READ COMMITTED can't see it), so poll the SELECT until the row
    shows up or a deadline passes. Returns the row, or ``None`` on timeout.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + retry_seconds
    while True:
        res = await db.execute(statement)
        row = res.scalar_one_or_none()
        if row is not None:
            return row
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(retry_step)


async def init_db() -> None:
    """Create all tables (dev convenience).

    Alembic is the source of truth for schema migrations; this only exists so
    the app boots without a manual migration step in development.
    """
    from app.models import Base  # noqa: F401  (registers all tables)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)