"""API tests for the batch endpoint and health check."""

from __future__ import annotations

import json

from sqlalchemy import func, select

from app.config import settings
from app.database import utcnow
from app.models.entities import Comment, Follow, Gift, Like, Stream, User
from app.schemas.batch import MAX_BATCH_EVENTS

BATCH_URL = "/api/v1/lives/room_123/batches"
HEALTH_URL = "/api/v1/health"

TS = "2025-01-01T10:00:00Z"
USER = {"unique_id": "u_1", "nickname": "Tester", "profile_picture_url": "http://pic/u1"}


def make_event(type_: str, data: dict, ts: str = TS, user: dict | None = None) -> dict:
    return {"type": type_, "timestamp": ts, "user": user or USER, "data": data}


def make_batch(events: list[dict], **overrides) -> dict:
    body = {"version": 1, "room_id": "room_123", "stream_id": "stream_9", "started_at": TS, "ended_at": TS, "events": events}
    body.update(overrides)
    return body


def mixed_events() -> list[dict]:
    return [
        make_event("comment", data={"message": "hola"}),
        make_event("comment", ts="2025-01-01T10:00:01Z", data={"message": "segundo"}),
        make_event("gift", data={"gift_name": "Rose", "repeat_count": 3, "diamonds": 10}),
        make_event("like", data={"like_count": 12, "delta": 5}),
        make_event("follow", data={}),
    ]


async def test_health(client):
    res = await client.get(HEALTH_URL)
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


async def test_ingest_mixed_batch(client, db_session):
    res = await client.post(BATCH_URL, json=make_batch(mixed_events()))
    assert res.status_code == 200
    ack = res.json()
    assert ack["received"] == 5
    assert ack["batch_id"] > 0

    assert (await db_session.scalar(select(func.count()).select_from(User))) == 1
    assert (await db_session.scalar(select(func.count()).select_from(Comment))) == 2
    assert (await db_session.scalar(select(func.count()).select_from(Gift))) == 1
    assert (await db_session.scalar(select(func.count()).select_from(Like))) == 1
    assert (await db_session.scalar(select(func.count()).select_from(Follow))) == 1

    stream = (await db_session.execute(select(Stream))).scalar_one()
    assert stream.room_id == "room_123"
    assert stream.stream_id == "stream_9"
    assert stream.batch_count == 1
    assert stream.event_count == 5


async def test_upsert_user_updates_profile(client, db_session):
    first = make_event("comment", data={"message": "hola"})
    assert (await client.post(BATCH_URL, json=make_batch([first]))).status_code == 200

    updated = make_event(
        "comment",
        user={"unique_id": "u_1", "nickname": "NewName", "profile_picture_url": "http://pic/new"},
        data={"message": "chau"},
    )
    assert (await client.post(BATCH_URL, json=make_batch([updated]))).status_code == 200

    assert (await db_session.scalar(select(func.count()).select_from(User))) == 1
    user = (await db_session.execute(select(User))).scalar_one()
    assert user.nickname == "NewName"
    assert user.profile_picture_url == "http://pic/new"


async def test_stream_provenance_accumulates(client, db_session):
    batch = make_batch(mixed_events())
    assert (await client.post(BATCH_URL, json=batch)).status_code == 200
    assert (await client.post(BATCH_URL, json=batch)).status_code == 200

    assert (await db_session.scalar(select(func.count()).select_from(Stream))) == 1
    stream = (await db_session.execute(select(Stream))).scalar_one()
    assert stream.batch_count == 2
    assert stream.event_count == 10


async def test_stream_id_falls_back_to_room_id(client, db_session):
    batch = make_batch([mixed_events()[0]], stream_id=None)
    assert (await client.post(BATCH_URL, json=batch)).status_code == 200

    stream = (await db_session.execute(select(Stream))).scalar_one()
    assert stream.stream_id == "room_123"


async def test_host_username_persisted_on_stream(client, db_session):
    batch = make_batch([mixed_events()[0]], host_username="@host.one")
    assert (await client.post(BATCH_URL, json=batch)).status_code == 200

    stream = (await db_session.execute(select(Stream))).scalar_one()
    assert stream.host_username == "@host.one"


async def test_host_username_latest_wins(client, db_session):
    assert (await client.post(
        BATCH_URL, json=make_batch([mixed_events()[0]], host_username="@host.one")
    )).status_code == 200
    assert (await client.post(
        BATCH_URL, json=make_batch([mixed_events()[1]], host_username="@host.renamed")
    )).status_code == 200

    assert (await db_session.scalar(select(func.count()).select_from(Stream))) == 1
    stream = (await db_session.execute(select(Stream))).scalar_one()
    assert stream.host_username == "@host.renamed"


async def test_host_username_absent_keeps_existing(client, db_session):
    assert (await client.post(
        BATCH_URL, json=make_batch([mixed_events()[0]], host_username="@host.one")
    )).status_code == 200

    bare = make_batch([mixed_events()[1]])
    bare.pop("host_username", None)
    assert (await client.post(BATCH_URL, json=bare)).status_code == 200

    nulled = make_batch([mixed_events()[2]], host_username=None)
    assert (await client.post(BATCH_URL, json=nulled)).status_code == 200

    stream = (await db_session.execute(select(Stream))).scalar_one()
    assert stream.host_username == "@host.one"


async def test_unknown_event_type_rejected(client):
    events = [make_event("mystery", data={})]
    res = await client.post(BATCH_URL, json=make_batch(events))
    assert res.status_code == 422


async def test_comment_missing_message_rejected(client):
    events = [make_event("comment", data={})]
    res = await client.post(BATCH_URL, json=make_batch(events))
    assert res.status_code == 422


async def test_gift_missing_fields_rejected(client):
    events = [make_event("gift", data={"gift_name": "Rose"})]
    res = await client.post(BATCH_URL, json=make_batch(events))
    assert res.status_code == 422


async def test_follow_with_data_rejected(client):
    events = [make_event("follow", data={"note": "extra"})]
    res = await client.post(BATCH_URL, json=make_batch(events))
    assert res.status_code == 422


async def test_bad_version_rejected(client):
    res = await client.post(BATCH_URL, json=make_batch([mixed_events()[0]], version=2))
    assert res.status_code == 422


async def test_bad_timestamp_rejected(client):
    events = [make_event("comment", ts="not-a-date", data={"message": "hola"})]
    res = await client.post(BATCH_URL, json=make_batch(events))
    assert res.status_code == 422


async def test_room_id_mismatch_rejected(client):
    batch = make_batch([mixed_events()[0]], room_id="other_room")
    res = await client.post(BATCH_URL, json=batch)
    assert res.status_code == 400


async def test_too_many_events_rejected(client):
    events = [make_event("follow", data={}) for _ in range(MAX_BATCH_EVENTS + 1)]
    res = await client.post(BATCH_URL, json=make_batch(events))
    assert res.status_code == 422


async def test_oversized_content_length_rejected(client):
    body = json.dumps(make_batch([mixed_events()[0]])).encode()
    res = await client.post(
        BATCH_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(settings.max_payload_bytes + 1),
        },
    )
    assert res.status_code == 413


async def test_precreated_user_updated_by_batch(client, db_session):
    db_session.add(
        User(
            unique_id="u_fallback",
            nickname="Old",
            profile_picture_url="http://pic/old",
            first_seen_at=utcnow(),
            last_seen_at=utcnow(),
        )
    )
    await db_session.commit()

    event = make_event(
        "comment",
        user={
            "unique_id": "u_fallback",
            "nickname": "New",
            "profile_picture_url": "http://pic/new",
        },
        data={"message": "hola"},
    )
    res = await client.post(BATCH_URL, json=make_batch([event]))
    assert res.status_code == 200

    assert (await db_session.scalar(select(func.count()).select_from(User))) == 1
    user = (
        await db_session.execute(select(User).where(User.unique_id == "u_fallback"))
    ).scalar_one()
    assert user.nickname == "New"
    assert user.profile_picture_url == "http://pic/new"