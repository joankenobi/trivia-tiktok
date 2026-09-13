"""Stream data access. One row per (room, stream) with batch provenance."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import fetch_with_retry
from app.models.entities import Stream


async def get_or_create_stream(
    db: AsyncSession, room_id: str, stream_id: str | None, first_event_at: datetime
) -> Stream:
    """Find the stream row for (room_id, stream_id) or create it.

    ``stream_id`` is normalized to ``room_id`` when absent so the unique key
    never contains NULLs (NULL uniqueness differs between SQLite and Postgres).
    """
    key = stream_id or room_id
    stream_stmt = select(Stream).where(
        Stream.room_id == room_id, Stream.stream_id == key
    )

    res = await db.execute(stream_stmt)
    stream = res.scalar_one_or_none()

    if stream is None:
        stream = Stream(
            room_id=room_id,
            stream_id=key,
            first_seen=first_event_at,
            last_seen=first_event_at,
            batch_count=0,
            event_count=0,
        )
        db.add(stream)
        try:
            # Savepoint-scoped INSERT: a race on the (room, stream) unique
            # constraint only rolls back the savepoint, not the batch
            # transaction that an IntegrityError would otherwise abort.
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # Lost the race. Detach the stale pending object and reuse the
            # row that won.
            db.expunge(stream)
            stream = await fetch_with_retry(db, stream_stmt)
            if stream is None:
                raise

    return stream