"""Upstream client and poller behaviour.

These target the two places a bug would be invisible in a happy-path demo but
fatal in a real live: pagination (a single page undercounts a tally) and the
cursor (a hole lets a round count chat that belongs to another round).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infra.poller import PollerRegistry, next_backoff
from app.infra.tiktok_live import TikTokLiveClient, _parse_ts


def _event(eid: int, msg: str, uid: str = "u1") -> dict:
    return {
        "event_id": eid,
        "type": "comment",
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": {"unique_id": uid, "nickname": uid, "avatar": None},
        "data": {"message": msg},
    }


def make_client(events: list[dict], **kwargs) -> tuple[TikTokLiveClient, list[dict]]:
    """Client backed by a mock transport, plus the recorded requests."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        since = int(params.get("since", 0))
        limit = int(params.get("limit", 200))
        batch = sorted((e for e in events if e["event_id"] > since), key=lambda e: e["event_id"])
        page = batch[:limit]
        return httpx.Response(
            200,
            json={
                "events": page,
                "has_more": len(batch) > len(page),
                "next_cursor": page[-1]["event_id"] if page else since,
            },
        )

    client = TikTokLiveClient(
        base_url="http://up.test",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )
    return client, seen


class TestPagination:
    async def test_follows_next_cursor_across_pages(self):
        events = [_event(i, f"msg{i}") for i in range(1, 501)]
        client, seen = make_client(events, page_limit=200)

        out = await client.fetch_since("room", 0)

        assert len(out) == 500
        assert len(seen) == 3  # 200 + 200 + 100
        # A single page would have undercounted by 60%, which is the whole point.
        assert [c.event_id for c in out] == list(range(1, 501))

    async def test_respects_since_cursor(self):
        events = [_event(i, "x") for i in range(1, 51)]
        client, _ = make_client(events)
        out = await client.fetch_since("room", 30)
        assert [c.event_id for c in out] == list(range(31, 51))

    async def test_stops_when_next_cursor_does_not_advance(self):
        """A broken upstream that always returns the same cursor must not hang."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"events": [_event(5, "a")], "has_more": True, "next_cursor": 0},
            )

        client = TikTokLiveClient(base_url="http://up.test", transport=httpx.MockTransport(handler))
        out = await asyncio.wait_for(client.fetch_since("room", 0), timeout=5)
        assert len(out) == 1

    async def test_handles_empty_result(self):
        client, _ = make_client([])
        assert await client.fetch_since("room", 0) == []

    async def test_filters_out_non_comment_types(self):
        events = [_event(1, "a"), {**_event(2, "b"), "type": "like"}, _event(3, "c")]
        client, _ = make_client(events)
        out = await client.fetch_since("room", 0)
        assert [c.event_id for c in out] == [1, 3]


class TestTimestamps:
    def test_naive_ts_is_forced_to_utc(self):
        dt = _parse_ts("2026-09-30T12:00:00")
        assert dt.tzinfo is timezone.utc

    def test_aware_ts_is_converted_to_utc(self):

        dt = _parse_ts("2026-09-30T12:00:00+02:00")
        assert dt.hour == 10
        assert dt.tzinfo is timezone.utc

    def test_never_compares_naive_with_aware(self):
        # The bug class that broke tiktoklives_back's own batch ingest.
        aware = _parse_ts("2026-09-30T12:00:00")
        naive = datetime.fromisoformat("2026-09-30T12:00:00")
        assert (aware - naive.replace(tzinfo=timezone.utc)).total_seconds() == 0


class TestCursorMonotonicity:
    def test_never_moves_backwards(self):
        registry = PollerRegistry.__new__(PollerRegistry)  # no engine needed
        registry._cursors = {}
        registry.client = None  # type: ignore[assignment]

        registry.advance("r1", 100)
        registry.advance("r1", 50)   # out-of-order arrival
        assert registry.cursor("r1") == 100

        registry.advance("r1", 250)
        assert registry.cursor("r1") == 250

    def test_cursors_are_per_room(self):
        registry = PollerRegistry.__new__(PollerRegistry)
        registry._cursors = {}
        registry.client = None  # type: ignore[assignment]

        registry.advance("roomA", 10)
        registry.advance("roomB", 99)
        assert registry.cursor("roomA") == 10
        assert registry.cursor("roomB") == 99


@pytest_asyncio.fixture
async def engine_and_maker():
    import os
    import tempfile

    from app.infra.models import Base

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()
    os.unlink(path)


class TestPrime:
    async def test_prime_learns_high_water_mark(self, engine_and_maker):
        events = [_event(i, "hello") for i in range(1, 31)]
        client, _ = make_client(events)
        registry = PollerRegistry(engine_and_maker, client=client)

        assert registry.cursor("r") == 0
        assert await registry.prime("r") == 30
        # Nothing after priming: pre-existing chat cannot leak into round 1.
        assert await client.fetch_since("r", registry.cursor("r")) == []

    async def test_prime_is_non_fatal_on_upstream_failure(self, engine_and_maker):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        client = TikTokLiveClient(base_url="http://up.test", transport=httpx.MockTransport(handler))
        registry = PollerRegistry(engine_and_maker, client=client)

        # Returns 0 and stays usable: a dead upstream must not stop the show.
        assert await registry.prime("r") == 0


class TestBackoff:
    def test_grows_then_saturates_at_ceiling(self):
        interval, ceiling = 0.9, 2.0
        seen = []
        b = interval
        for _ in range(8):
            seen.append(b)
            b = next_backoff(b, ceiling)

        assert seen[0] == 0.9                      # starts at the poll interval
        assert max(seen) == ceiling                # never exceeds the ceiling
        assert all(b <= ceiling for b in seen)
        assert seen[-1] == ceiling                 # and stays there

    def test_ceiling_below_interval_is_respected(self):
        assert next_backoff(0.9, 0.5) == 0.5


class TestPollerResilience:
    async def _open_round(self, maker) -> int:
        from app.domain.question import Question as DomainQuestion
        from app.services import session_service as svc

        async with maker() as s:
            await svc.seed_questions(
                s,
                [DomainQuestion(id=1, text="Q?", difficulty="easy",
                                options=("a", "b", "c", "d"), correct="A")],
            )
            show = await svc.create_show(s, "1", [1], 10)
            rounds = sorted(show.rounds, key=lambda r: r.position)
            rnd = await svc.open_round(s, rounds[0].id, opened_cursor=0)
            return rnd.id

    async def test_failing_upstream_does_not_kill_the_loop(self, engine_and_maker):
        """The poller must survive upstream errors and still stop cleanly."""
        from app.infra.poller import RoundPoller

        round_id = await self._open_round(engine_and_maker)
        calls = {"n": 0}

        class ExplodingClient:
            async def fetch_since(self, *_a, **_k):
                calls["n"] += 1
                raise httpx.ConnectError("upstream down")

        registry = PollerRegistry(engine_and_maker, client=ExplodingClient())
        poller = RoundPoller(round_id, "room", engine_and_maker, registry)
        poller.start()
        try:
            await asyncio.sleep(0.05)
            # Still running, and it did try the upstream, and it did not crash.
            assert poller._task is not None and not poller._task.done()
            assert calls["n"] >= 1
        finally:
            await poller.stop(final_drain=False)
        assert poller._task is None

    async def test_drain_swallows_upstream_error(self, engine_and_maker):
        from app.infra.poller import RoundPoller

        round_id = await self._open_round(engine_and_maker)

        class ExplodingClient:
            async def fetch_since(self, *_a, **_k):
                raise httpx.ConnectError("upstream down")

        registry = PollerRegistry(engine_and_maker, client=ExplodingClient())
        poller = RoundPoller(round_id, "room", engine_and_maker, registry)
        # drain_once runs at close: it must return 0, not raise into the route.
        assert await poller.drain_once() == 0

    async def test_stop_all_is_safe_with_no_pollers(self, engine_and_maker):
        registry = PollerRegistry(engine_and_maker)
        await registry.stop_all()  # must not raise
