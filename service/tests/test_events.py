"""API tests for the read endpoints: event cursor pagination and active stream."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.database import utcnow
from app.models.entities import Stream

BATCH_URL = "/api/v1/lives/room_123/batches"
EVENTS_URL = "/api/v1/lives/room_123/events"
ACTIVE_URL = "/api/v1/lives/room_123/streams/active"
ACTIVE_LIVE_URL = "/api/v1/lives/active"
ROOM_456_BATCH_URL = "/api/v1/lives/room_456/batches"

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


async def ingest(client, events: list[dict] | None = None, **overrides) -> None:
    res = await client.post(BATCH_URL, json=make_batch(events or mixed_events(), **overrides))
    assert res.status_code == 200


async def test_events_from_cursor_all_types(client):
    await ingest(client)

    res = await client.get(EVENTS_URL, params={"since": 0})
    assert res.status_code == 200
    body = res.json()
    assert body["has_more"] is False
    assert [e["type"] for e in body["events"]] == ["comment", "comment", "gift", "like", "follow"]
    assert [e["event_id"] for e in body["events"]] == [1, 2, 3, 4, 5]
    assert body["next_cursor"] == 5


async def test_event_payload_shape(client):
    await ingest(client, [mixed_events()[0], mixed_events()[-1]])

    res = await client.get(EVENTS_URL)
    assert res.status_code == 200
    events = res.json()["events"]

    comment = events[0]
    assert set(comment) == {"event_id", "type", "ts", "user", "data"}
    assert comment["event_id"] == 1
    assert comment["type"] == "comment"
    assert comment["ts"] == "2025-01-01T10:00:00"
    assert comment["user"] == {"unique_id": "u_1", "nickname": "Tester", "avatar": "http://pic/u1"}
    assert comment["data"] == {"message": "hola"}

    follow = events[1]
    assert follow["type"] == "follow"
    assert follow["data"] == {}


async def test_gift_and_like_data_payloads(client):
    await ingest(client, [mixed_events()[2], mixed_events()[3]])

    body = (await client.get(EVENTS_URL)).json()
    gift = body["events"][0]
    like = body["events"][1]
    assert gift["data"] == {"gift_name": "Rose", "repeat_count": 3, "diamonds": 10}
    assert like["data"] == {"like_count": 12, "delta": 5}


async def test_events_filtered_by_types(client):
    await ingest(client)

    res = await client.get(EVENTS_URL, params={"types": "comment,gift"})
    assert res.status_code == 200
    body = res.json()
    assert [e["type"] for e in body["events"]] == ["comment", "comment", "gift"]
    assert [e["event_id"] for e in body["events"]] == [1, 2, 3]


async def test_events_empty_types_means_all(client):
    await ingest(client, [mixed_events()[2], mixed_events()[4]])

    body = (await client.get(EVENTS_URL, params={"types": ""})).json()
    assert [e["type"] for e in body["events"]] == ["gift", "follow"]


async def test_pagination_no_loss_no_duplicates(client):
    await ingest(client)
    await ingest(client)

    page_limit = 3
    seen_ids: list[int] = []
    cursor = 0
    more = True
    while more:
        res = await client.get(EVENTS_URL, params={"since": cursor, "limit": page_limit})
        assert res.status_code == 200
        body = res.json()
        seen_ids.extend(e["event_id"] for e in body["events"])
        more = body["has_more"]
        cursor = body["next_cursor"]

    assert seen_ids == list(range(1, 11))
    assert len(seen_ids) == 10


async def test_has_more_flag(client):
    await ingest(client)

    body = (await client.get(EVENTS_URL, params={"limit": 2})).json()
    assert len(body["events"]) == 2
    assert body["has_more"] is True
    assert body["next_cursor"] == 2


async def test_exact_limit_has_more_false(client):
    await ingest(client, mixed_events()[:2])

    body = (await client.get(EVENTS_URL, params={"limit": 2})).json()
    assert len(body["events"]) == 2
    assert body["has_more"] is False
    assert body["next_cursor"] == 2


async def test_no_new_events_keeps_cursor(client):
    await ingest(client)
    res = await client.post(BATCH_URL, json=make_batch([mixed_events()[0]]))

    body = (await client.get(EVENTS_URL, params={"since": 100})).json()
    assert body == {"events": [], "next_cursor": 100, "has_more": False}


async def test_events_after_cursor_resumes(client):
    await ingest(client, [mixed_events()[0]])
    first = (await client.get(EVENTS_URL)).json()
    assert first["next_cursor"] == 1
    assert first["has_more"] is False

    await ingest(client, mixed_events()[1:])
    second = (await client.get(EVENTS_URL, params={"since": first["next_cursor"]})).json()
    assert [e["event_id"] for e in second["events"]] == [2, 3, 4, 5]
    assert second["has_more"] is False


async def test_events_invalid_types_422(client):
    await ingest(client)
    res = await client.get(EVENTS_URL, params={"types": "comment,lols"})
    assert res.status_code == 422


async def test_events_invalid_limit_422(client):
    res = await client.get(EVENTS_URL, params={"limit": 250})
    assert res.status_code == 422


async def test_events_negative_since_422(client):
    res = await client.get(EVENTS_URL, params={"since": -1})
    assert res.status_code == 422


async def test_events_room_not_found_404(client):
    res = await client.get("/api/v1/lives/ghost_room/events")
    assert res.status_code == 404
    assert res.json()["detail"] == "Room not found"


async def test_active_stream_returns_latest(client):
    now = utcnow()
    ts = now.isoformat()
    batch = make_batch(
        [make_event("comment", data={"message": "hola"}, ts=ts)],
        started_at=ts,
        ended_at=ts,
        host_username="@host.one",
    )
    res = await client.post(BATCH_URL, json=batch)
    assert res.status_code == 200

    res = await client.get(ACTIVE_URL)
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "room_id": "room_123",
        "started_at": now.isoformat(),
        "host_username": "@host.one",
        "active": True,
    }


async def test_active_stream_stale_flag_false(client, db_session):
    await ingest(client, [mixed_events()[0]])

    stream = (await db_session.execute(select(Stream))).scalar_one()
    stream.last_seen = utcnow() - timedelta(minutes=5)
    await db_session.commit()

    body = (await client.get(ACTIVE_URL)).json()
    assert body["active"] is False
    assert body["host_username"] is None


async def test_active_stream_prefers_latest_seen(client, db_session):
    await ingest(client, [mixed_events()[0]])
    stream = (await db_session.execute(select(Stream))).scalar_one()
    stream.host_username = "@old.host"
    await db_session.commit()

    await ingest(client, [mixed_events()[1]], host_username="@new.host", stream_id="stream_99")
    assert (await db_session.scalar(select(func.count()).select_from(Stream))) == 2

    body = (await client.get(ACTIVE_URL)).json()
    assert body["host_username"] == "@new.host"


async def test_active_stream_room_not_found_404(client):
    res = await client.get("/api/v1/lives/ghost_room/streams/active")
    assert res.status_code == 404
    assert res.json()["detail"] == "Room not found"


async def test_active_live_empty_database(client):
    res = await client.get(ACTIVE_LIVE_URL)
    assert res.status_code == 200
    assert res.json() == {
        "room_id": None,
        "started_at": None,
        "host_username": None,
        "active": False,
    }


async def test_active_live_returns_ingested_room(client):
    now = utcnow()
    batch = make_batch(
        [make_event("comment", data={"message": "hola"}, ts=now.isoformat())],
        started_at=now.isoformat(),
        ended_at=now.isoformat(),
        host_username="@host.one",
    )
    assert (await client.post(BATCH_URL, json=batch)).status_code == 200

    res = await client.get(ACTIVE_LIVE_URL)
    assert res.status_code == 200
    body = res.json()
    assert body["room_id"] == "room_123"
    assert body["host_username"] == "@host.one"
    assert body["active"] is True


async def test_active_live_prefers_most_recent_room(client, db_session):
    await ingest(client, [mixed_events()[0]])
    stream = (await db_session.execute(select(Stream))).scalar_one()
    stream.last_seen = utcnow() - timedelta(minutes=5)
    await db_session.commit()

    now = utcnow().isoformat()
    other = make_batch(
        [make_event("comment", data={"message": "otro"}, ts=now)],
        room_id="room_456",
        stream_id="stream_456",
        started_at=now,
        ended_at=now,
    )
    assert (await client.post(ROOM_456_BATCH_URL, json=other)).status_code == 200

    body = (await client.get(ACTIVE_LIVE_URL)).json()
    assert body["room_id"] == "room_456"
    assert body["active"] is True


async def test_active_live_stale_still_reports_room(client, db_session):
    await ingest(client, [mixed_events()[0]])

    stream = (await db_session.execute(select(Stream))).scalar_one()
    stream.last_seen = utcnow() - timedelta(minutes=5)
    await db_session.commit()

    res = await client.get(ACTIVE_LIVE_URL)
    assert res.status_code == 200
    body = res.json()
    assert body["room_id"] == "room_123"
    assert body["active"] is False


async def test_active_live_does_not_shadow_room_routes(client):
    await ingest(client, [mixed_events()[0]])

    assert (await client.get(EVENTS_URL)).status_code == 200
    assert (await client.get(ACTIVE_URL)).status_code == 200