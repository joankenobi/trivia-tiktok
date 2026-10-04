"""Read-side data access for chat events and the cursor index.

Entity lookups are batched per kind (one IN query each) so a page of up to
``limit`` events stays O(limit) in queries regardless of the type mix, then
user rows are fetched in a single additional query.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    Comment,
    EventIndex,
    Follow,
    Gift,
    Like,
    Stream,
    User,
)

KIND_ENTITY = {
    "comment": Comment,
    "gift": Gift,
    "like": Like,
    "follow": Follow,
}

Entity = Comment | Gift | Like | Follow


@dataclass
class EventRecord:
    event_id: int
    kind: str
    entity: Entity
    user: User


@dataclass
class EventPage:
    events: list[EventRecord]
    has_more: bool


async def room_stream_ids(db: AsyncSession, room_id: str) -> list[int]:
    """All stream PKs registered under a room. Empty means the room is unknown."""
    res = await db.execute(select(Stream.id).where(Stream.room_id == room_id))
    return list(res.scalars())


async def latest_stream_for_room(db: AsyncSession, room_id: str) -> Stream | None:
    """Most recently seen stream for the room (by ``last_seen``)."""
    res = await db.execute(
        select(Stream)
        .where(Stream.room_id == room_id)
        .order_by(Stream.last_seen.desc(), Stream.id.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def latest_stream(db: AsyncSession) -> Stream | None:
    """Most recently seen stream across all rooms (by ``last_seen``).

    Used for room discovery: the caller has no room id yet.
    """
    res = await db.execute(
        select(Stream).order_by(Stream.last_seen.desc(), Stream.id.desc()).limit(1)
    )
    return res.scalar_one_or_none()


async def list_event_page(
    db: AsyncSession,
    stream_ids: list[int],
    since: int | None,
    types: list[str] | None,
    limit: int,
) -> EventPage:
    """Events with ``id > since`` in index order, plus a ``has_more`` flag.

    ``limit + 1`` rows are fetched so ``has_more`` reflects whether a further
    page exists without an extra query. Ordering follows the index id, which is
    the ingestion sequence — the only ordering consistent with the cursor.
    """
    lower_bound = 0 if since is None else since
    stmt = (
        select(EventIndex.id, EventIndex.kind, EventIndex.entity_id)
        .where(EventIndex.stream_id.in_(stream_ids))
        .where(EventIndex.id > lower_bound)
        .order_by(EventIndex.id)
        .limit(limit + 1)
    )
    if types:
        stmt = stmt.where(EventIndex.kind.in_(types))

    rows = (await db.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    if not rows:
        return EventPage(events=[], has_more=False)

    ids_by_kind: dict[str, list[int]] = {}
    for _, kind, entity_id in rows:
        ids_by_kind.setdefault(kind, []).append(entity_id)

    entity_by_key: dict[tuple[str, int], Entity] = {}
    for kind, entity_ids in ids_by_kind.items():
        model = KIND_ENTITY[kind]
        entities = (await db.execute(select(model).where(model.id.in_(entity_ids)))).scalars()
        for entity in entities:
            entity_by_key[(kind, entity.id)] = entity

    user_ids = {entity.user_id for entity in entity_by_key.values()}
    users_by_id: dict[int, User] = {}
    if user_ids:
        users = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars()
        users_by_id = {user.id: user for user in users}

    events: list[EventRecord] = []
    for event_id, kind, entity_id in rows:
        entity = entity_by_key[(kind, entity_id)]
        events.append(
            EventRecord(event_id=event_id, kind=kind, entity=entity, user=users_by_id[entity.user_id])
        )
    return EventPage(events=events, has_more=has_more)