"""Read-side workflows: chat event pagination and active stream lookup."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import utcnow
from app.repositories.events_reader import (
    EventRecord,
    latest_stream_for_room,
    list_event_page,
    room_stream_ids,
)
from app.schemas.events import (
    ActiveStreamResponse,
    ChatEventResponse,
    CommentContent,
    EventListResponse,
    EventUserInfo,
    FollowContent,
    GiftContent,
    LikeContent,
)

VALID_TYPES = ("comment", "gift", "like", "follow")


class RoomNotFoundError(Exception):
    """Raised when a room has no persisted stream/events."""

    def __init__(self, room_id: str):
        super().__init__(f"no stream rows for room {room_id!r}")
        self.room_id = room_id


def _to_event_user(user) -> EventUserInfo:
    return EventUserInfo(
        unique_id=user.unique_id,
        nickname=user.nickname,
        avatar=user.profile_picture_url,
    )


def _to_content(kind: str, entity) -> CommentContent | GiftContent | LikeContent | FollowContent:
    if kind == "comment":
        return CommentContent(message=entity.message)
    if kind == "gift":
        return GiftContent(
            gift_name=entity.gift_name,
            repeat_count=entity.repeat_count,
            diamonds=entity.diamonds,
        )
    if kind == "like":
        return LikeContent(delta=entity.delta, like_count=entity.like_count)
    return FollowContent()


def _to_event(record: EventRecord) -> ChatEventResponse:
    return ChatEventResponse(
        event_id=record.event_id,
        type=record.kind,
        ts=record.entity.timestamp,
        user=_to_event_user(record.user),
        data=_to_content(record.kind, record.entity),
    )


async def get_events(
    db: AsyncSession,
    room_id: str,
    *,
    since: int | None,
    types: list[str] | None,
    limit: int,
) -> EventListResponse:
    """Events of the room after ``since``, typed-filtered and capped at ``limit``.

    Events are returned in ingestion (cursor) order, which is chronological by
    construction: the capturer sends batches in order and each event is
    indexed at insert time. When no events match, ``next_cursor`` echoes
    ``since`` (or ``None`` when no cursor was supplied) so the client can keep
    polling with a stable cursor.
    """
    if types is not None:
        unknown = [t for t in types if t not in VALID_TYPES]
        if unknown:
            raise ValueError(f"unknown event type(s): {', '.join(unknown)}")

    stream_ids = await room_stream_ids(db, room_id)
    if not stream_ids:
        raise RoomNotFoundError(room_id)

    page = await list_event_page(db, stream_ids, since, types, limit)
    events = [_to_event(record) for record in page.events]

    if events:
        next_cursor = events[-1].event_id
    elif since is not None:
        next_cursor = since
    else:
        next_cursor = None

    return EventListResponse(events=events, next_cursor=next_cursor, has_more=page.has_more)


async def get_active_stream(db: AsyncSession, room_id: str) -> ActiveStreamResponse:
    """Most recently seen stream for the room and whether it is still active.

    "Active" means the stream's last ingested batch arrived within the last
    ``active_stream_window_seconds`` (see app.config, default 30s). A stale
    stream is still returned (with ``active=False``) so the client can react
    to the same live going quiet.
    """
    stream = await latest_stream_for_room(db, room_id)
    if stream is None:
        raise RoomNotFoundError(room_id)

    elapsed = (utcnow() - stream.last_seen).total_seconds()
    active = elapsed <= settings.active_stream_window_seconds
    return ActiveStreamResponse(
        room_id=stream.room_id,
        started_at=stream.first_seen,
        host_username=stream.host_username,
        active=active,
    )