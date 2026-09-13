"""User data access. Deduplicates on ``unique_id`` (upsert)."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import fetch_with_retry, utcnow
from app.models.entities import User

_ANON_PREFIX = "__anon__"


def _dedup_key(unique_id: str | None) -> str:
    """Fallback key for events without a TikTok unique id.

    TikTok always sends unique_id in practice; for the rare null case we mint a
    stable per-user token so distinct anonymous users aren't collapsed together
    and they can still be merged if the same nick shows up again.
    """
    if unique_id:
        return unique_id
    return f"{_ANON_PREFIX}{uuid4().hex}"


async def upsert_user(db: AsyncSession, unique_id: str | None, nickname: str | None, profile_picture_url: str | None) -> int:
    """Insert a user or refresh an existing one. Returns the users row id."""
    key = _dedup_key(unique_id)
    now = utcnow()

    user_stmt = select(User).where(User.unique_id == key)
    res = await db.execute(user_stmt)
    user = res.scalar_one_or_none()

    if user is None:
        user = User(
            unique_id=key,
            nickname=nickname,
            profile_picture_url=profile_picture_url,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(user)
        try:
            # Savepoint-scoped INSERT: on a unique-key conflict only the
            # savepoint rolls back, keeping the surrounding batch transaction
            # intact.
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # Lost a race against a concurrent POST. Detach our stale pending
            # object (it would replay the failing INSERT on the next flush)
            # and adopt the row that actually won.
            db.expunge(user)
            user = await fetch_with_retry(db, user_stmt)
            if user is None:
                raise
        else:
            return user.id

    user.nickname = nickname
    user.profile_picture_url = profile_picture_url
    user.last_seen_at = now
    return user.id