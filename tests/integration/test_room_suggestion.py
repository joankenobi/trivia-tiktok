"""Room suggestion: upstream payload parsing and the "is it worth suggesting" rule.

The rule is the whole point of the feature, so it is tested as a pure function.
A stale or already-ended live must never be offered as a starting room id: the
host would run a show against the wrong audience and never know why.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.infra.tiktok_live import ActiveLive, TikTokLiveClient
from app.services.room_suggestion import started_today, suggest_room

NOW = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)


def naive_utc(dt: datetime) -> str:
    """Upstream sends naive UTC, as `ts` and `started_at` both do."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def make_client(payload: dict) -> TikTokLiveClient:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return TikTokLiveClient(base_url="http://up.test", transport=httpx.MockTransport(handler))


def live(**overrides) -> ActiveLive:
    base = {
        "room_id": "7691759076723772182",
        "started_at_utc": NOW - timedelta(hours=2),
        "host_username": "@alexrmr74",
        "active": True,
    }
    return ActiveLive(**{**base, **overrides})


class TestActiveLiveParsing:
    async def test_parses_the_documented_payload(self):
        client = make_client(
            {
                "room_id": "7691759076723772182",
                "started_at": "2026-10-01T18:51:07.676380",
                "host_username": "@alexrmr74",
                "active": True,
            }
        )

        got = await client.fetch_active_live()

        assert got is not None
        assert got.room_id == "7691759076723772182"
        assert got.host_username == "@alexrmr74"
        assert got.active is True
        # Naive upstream timestamp forced to aware UTC, like every other `ts`.
        assert got.started_at_utc.tzinfo is timezone.utc
        assert got.started_at_utc.date() == NOW.date()

    async def test_aware_started_at_is_converted_to_utc(self):
        client = make_client(
            {"room_id": "1", "started_at": "2026-10-01T20:51:07+02:00", "active": True}
        )
        got = await client.fetch_active_live()
        assert got is not None
        assert got.started_at_utc.hour == 18
        assert got.started_at_utc.tzinfo is timezone.utc

    async def test_no_live_yields_none(self):
        assert await make_client({"active": False}).fetch_active_live() is None

    async def test_blank_room_id_yields_none(self):
        """A room-less live is not a room the host can start from."""
        assert await make_client({"room_id": "  ", "active": True}).fetch_active_live() is None

    async def test_unparseable_started_at_yields_none(self):
        for bad in ("not-a-date", None, 12345):
            payload = {"room_id": "1", "started_at": bad, "active": True}
            assert await make_client(payload).fetch_active_live() is None

    async def test_transport_failure_propagates(self):
        """The route decides what an unreachable upstream means; not the client."""
        def handler(_r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        client = TikTokLiveClient(base_url="http://up.test", transport=httpx.MockTransport(handler))
        try:
            await client.fetch_active_live()
        except httpx.ConnectError:
            return
        raise AssertionError("expected ConnectError")


class TestSuggestionRule:
    def test_active_live_started_today_is_suggested(self):
        got = suggest_room(live(), now_utc=NOW)
        assert got is not None
        assert got.room_id == "7691759076723772182"

    def test_ended_live_is_not_suggested(self):
        assert suggest_room(live(active=False), now_utc=NOW) is None

    def test_no_live_is_not_suggested(self):
        assert suggest_room(None, now_utc=NOW) is None

    def test_live_from_a_previous_day_is_not_suggested(self):
        yesterday = live(started_at_utc=NOW - timedelta(days=1))
        assert suggest_room(yesterday, now_utc=NOW) is None

    def test_live_ending_just_before_utc_midnight_is_not_suggested(self):
        """The stale case that a naive local-time check would wave through."""
        just_before = live(started_at_utc=datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc))
        assert suggest_room(just_before, now_utc=datetime(2026, 10, 1, 0, 1, tzinfo=timezone.utc)) is None

    def test_live_starting_just_after_utc_midnight_is_suggested(self):
        just_after = live(started_at_utc=datetime(2026, 10, 1, 0, 1, tzinfo=timezone.utc))
        assert suggest_room(just_after, now_utc=just_after.started_at_utc) is not None

    def test_day_boundary_uses_utc(self):
        """A stream hours old is still today's, even though it is late locally."""
        early = live(started_at_utc=datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc))
        assert suggest_room(early, now_utc=datetime(2026, 10, 1, 23, 0, tzinfo=timezone.utc)) is not None

    def test_now_defaults_to_the_real_clock(self):
        assert suggest_room(live(started_at_utc=datetime.now(timezone.utc))) is not None

    def test_naive_now_is_accepted(self):
        """Never raise comparing naive against aware: that bug class is upstream's."""
        got = suggest_room(live(), now_utc=datetime(2026, 10, 1, 18, 0))  # noqa: DTZ001
        assert got is not None


class TestStartedToday:
    def test_same_day(self):
        assert started_today(NOW, NOW) is True

    def test_different_day(self):
        assert started_today(NOW, NOW + timedelta(days=1)) is False
