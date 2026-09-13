"""Event data access. One insert per event type."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Comment, Follow, Gift, Like


async def insert_comment(
    db: AsyncSession, stream_id: int, user_id: int, message: str, timestamp: datetime
) -> None:
    db.add(Comment(stream_id=stream_id, user_id=user_id, message=message, timestamp=timestamp))


async def insert_gift(
    db: AsyncSession,
    stream_id: int,
    user_id: int,
    gift_name: str,
    repeat_count: int,
    diamonds: int | None,
    timestamp: datetime,
) -> None:
    db.add(
        Gift(
            stream_id=stream_id,
            user_id=user_id,
            gift_name=gift_name,
            repeat_count=repeat_count,
            diamonds=diamonds,
            timestamp=timestamp,
        )
    )


async def insert_like(
    db: AsyncSession,
    stream_id: int,
    user_id: int,
    like_count: int,
    delta: int,
    timestamp: datetime,
) -> None:
    db.add(
        Like(
            stream_id=stream_id,
            user_id=user_id,
            like_count=like_count,
            delta=delta,
            timestamp=timestamp,
        )
    )


async def insert_follow(
    db: AsyncSession, stream_id: int, user_id: int, timestamp: datetime
) -> None:
    db.add(Follow(stream_id=stream_id, user_id=user_id, timestamp=timestamp))