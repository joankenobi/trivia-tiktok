"""Event data access. One insert per event type, plus the cursor index row.

Every event insert also writes an ``event_index`` row (in the same
transaction, flushed immediately so the autoincrement entity id is known)
that carries the global monotonic cursor id used by the read API.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Comment, EventIndex, Follow, Gift, Like

_KIND_COMMENT = "comment"
_KIND_GIFT = "gift"
_KIND_LIKE = "like"
_KIND_FOLLOW = "follow"


async def _index_event(
    db: AsyncSession, stream_id: int, kind: str, entity
) -> None:
    """Flush ``entity`` to assign its id, then record it in the cursor index."""
    await db.flush()
    db.add(EventIndex(stream_id=stream_id, kind=kind, entity_id=entity.id))


async def insert_comment(
    db: AsyncSession, stream_id: int, user_id: int, message: str, timestamp: datetime
) -> None:
    comment = Comment(
        stream_id=stream_id, user_id=user_id, message=message, timestamp=timestamp
    )
    db.add(comment)
    await _index_event(db, stream_id, _KIND_COMMENT, comment)


async def insert_gift(
    db: AsyncSession,
    stream_id: int,
    user_id: int,
    gift_name: str,
    repeat_count: int,
    diamonds: int | None,
    timestamp: datetime,
) -> None:
    gift = Gift(
        stream_id=stream_id,
        user_id=user_id,
        gift_name=gift_name,
        repeat_count=repeat_count,
        diamonds=diamonds,
        timestamp=timestamp,
    )
    db.add(gift)
    await _index_event(db, stream_id, _KIND_GIFT, gift)


async def insert_like(
    db: AsyncSession,
    stream_id: int,
    user_id: int,
    like_count: int,
    delta: int,
    timestamp: datetime,
) -> None:
    like = Like(
        stream_id=stream_id,
        user_id=user_id,
        like_count=like_count,
        delta=delta,
        timestamp=timestamp,
    )
    db.add(like)
    await _index_event(db, stream_id, _KIND_LIKE, like)


async def insert_follow(
    db: AsyncSession, stream_id: int, user_id: int, timestamp: datetime
) -> None:
    follow = Follow(stream_id=stream_id, user_id=user_id, timestamp=timestamp)
    db.add(follow)
    await _index_event(db, stream_id, _KIND_FOLLOW, follow)