"""Batch ingestion: normalize a contract Batch into normalized rows."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import to_naive_utc
from app.models.entities import Stream
from app.repositories import (
    get_or_create_stream,
    insert_comment,
    insert_follow,
    insert_gift,
    insert_like,
    upsert_user,
)
from app.schemas import Batch, CommentEvent, FollowEvent, GiftEvent, LikeEvent


@dataclass
class IngestResult:
    received: int
    batch_id: int


async def _handle_event(db: AsyncSession, stream: Stream, user_cache: dict, event) -> None:
    user_key = event.user.unique_id
    if user_key in user_cache:
        user_id = user_cache[user_key]
    else:
        user_id = await upsert_user(
            db,
            unique_id=event.user.unique_id,
            nickname=event.user.nickname,
            profile_picture_url=event.user.profile_picture_url,
        )
        user_cache[user_key] = user_id

    if isinstance(event, CommentEvent):
        await insert_comment(db, stream.id, user_id, event.data.message, event.timestamp)
    elif isinstance(event, GiftEvent):
        await insert_gift(
            db,
            stream.id,
            user_id,
            event.data.gift_name,
            event.data.repeat_count,
            event.data.diamonds,
            event.timestamp,
        )
    elif isinstance(event, LikeEvent):
        await insert_like(
            db, stream.id, user_id, event.data.like_count, event.data.delta, event.timestamp
        )
    elif isinstance(event, FollowEvent):
        await insert_follow(db, stream.id, user_id, event.timestamp)


async def ingest_batch(db: AsyncSession, room_id: str, batch: Batch) -> IngestResult:
    """Persist one batch inside a single transaction and update stream provenance."""
    # Normalize to naive UTC so DB storage and min/max provenance comparisons
    # never mix aware and naive datetimes (SQLite stores naive).
    batch.started_at = to_naive_utc(batch.started_at)
    batch.ended_at = to_naive_utc(batch.ended_at)
    for event in batch.events:
        event.timestamp = to_naive_utc(event.timestamp)

    stream = await get_or_create_stream(
        db, room_id, batch.stream_id, batch.started_at, host_username=batch.host_username
    )
    # Newest non-null host_username wins (the live owner can rename the
    # account mid-capture). A null batch value leaves the stored one alone.
    if batch.host_username is not None:
        stream.host_username = batch.host_username
    user_cache: dict = {}

    for event in batch.events:
        await _handle_event(db, stream, user_cache, event)

    stream.first_seen = min(stream.first_seen, batch.started_at)
    stream.last_seen = max(stream.last_seen, batch.ended_at)
    stream.batch_count += 1
    stream.event_count += len(batch.events)

    stream_pk = stream.id
    await db.commit()
    return IngestResult(received=len(batch.events), batch_id=stream_pk)