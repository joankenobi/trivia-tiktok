"""API-level tests: real ASGI app, temp SQLite, fake upstream.

The upstream is faked rather than mocked at the module level so the pagination,
cursor advance, and backoff paths in `TikTokLiveClient` and `RoundPoller` are
exercised for real.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from app.config import settings


class FakeUpstream:
    """Minimal stand-in for the tiktoklives_back read API.

    Wired in through the client's transport seam, so the real `_drain`
    pagination loop is what runs in these tests.
    """

    def __init__(self, page_limit: int = 200) -> None:
        self.events: list[dict] = []
        self.calls: list[dict] = []
        self.page_limit = page_limit
        self.active: dict | None = None
        self.active_error: Exception | None = None

    def add_comment(
        self,
        event_id: int,
        message: str,
        uid: str = "u1",
        nickname: str = "User1",
        ts: datetime | None = None,
    ) -> None:
        self.events.append(
            {
                "event_id": event_id,
                "type": "comment",
                "ts": (ts or datetime.now(timezone.utc)).isoformat(),
                "user": {"unique_id": uid, "nickname": nickname, "avatar": None},
                "data": {"message": message},
            }
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/lives/active":
            if self.active_error is not None:
                raise self.active_error
            return httpx.Response(200, json=self.active or {"active": False})

        params = dict(request.url.params)
        since = int(params.get("since", 0))
        limit = int(params.get("limit", self.page_limit))
        self.calls.append(params)

        batch = sorted(
            (e for e in self.events if e["event_id"] > since),
            key=lambda e: e["event_id"],
        )
        page = batch[:limit]
        return httpx.Response(
            200,
            json={
                "events": page,
                "has_more": len(batch) > len(page),
                "next_cursor": page[-1]["event_id"] if page else since,
            },
        )


@pytest_asyncio.fixture
async def client():
    """TestClient wired to a temp DB and a faked upstream transport."""
    from contextlib import asynccontextmanager

    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app import main_state
    from app.db import Base, get_session
    from app.domain.question import Question as DomainQuestion
    from app.infra.poller import PollerRegistry
    from app.infra.tiktok_live import TikTokLiveClient
    from app.main import create_app
    from app.services import session_service as svc
    from app.services.show_runner import ShowRunner

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    upstream = FakeUpstream()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    live_client = TikTokLiveClient(
        base_url="http://upstream.test", transport=upstream.transport()
    )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app):
        async with maker() as s:
            await svc.seed_questions(
                s,
                [
                    DomainQuestion(
                        id=i, text=f"Q{i}?", difficulty="medium",
                        options=("a", "b", "c", "d"), correct="A",
                    )
                    for i in (1, 2, 3)
                ],
            )
        main_state.registry = PollerRegistry(maker, client=live_client)
        # The real runner, wired exactly as `main.lifespan` wires it. Windows are
        # collapsed by the `fast_autoplay` fixture so a whole show plays in
        # milliseconds instead of minutes.
        main_state.runner = ShowRunner(maker, main_state.registry)
        app.state.runner = main_state.runner
        try:
            yield
        finally:
            await main_state.runner.stop_all()
            main_state.runner = None
            await main_state.registry.stop_all()
            await engine.dispose()

    app = create_app()
    app.router.lifespan_context = lifespan

    # Routes resolve their session through this dependency, which is bound to the
    # module-level engine. Override it, or the suite would read and write the real
    # trivia_game.db next to the app.
    async def override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override_session

    with TestClient(app) as c:
        c.upstream = upstream  # type: ignore[attr-defined]
        # Exposed so a test can put the DB in a state the API cannot reach (a
        # finished show) instead of contorting the endpoints to get there.
        c.maker = maker  # type: ignore[attr-defined]
        yield c

    # Windows keeps the file locked briefly if a poll task was mid-drain; the DB
    # is a temp file either way, so a surviving handle is not worth a red suite.
    with contextlib.suppress(OSError):
        os.unlink(path)


class TestHealth:
    def test_healthz(self, client):
        r = client.get("/api/v1/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_static_surfaces_served(self, client):
        assert client.get("/", headers={"accept": "text/html"}).status_code == 200
        # OBS sends */*, so /overlay needs a real route, not the HTML fallback.
        assert client.get("/overlay", headers={"accept": "*/*"}).status_code == 200
        assert client.get("/control", headers={"accept": "*/*"}).status_code == 200
        assert client.get("/js/overlay.js").status_code == 200
        assert client.get("/overlay.css").status_code == 200

    def test_app_js_and_css_are_never_cached(self, client):
        """A browser source must run the bytes on disk, not the ones it kept.

        An OBS source keeps its document alive across a server restart, so a
        cached `common.js` outlives the fix that changed it and the screen lies
        while the API and the tests are both right.
        """
        for path in ("/js/common.js", "/js/overlay.js", "/overlay.css"):
            headers = client.get(path).headers
            assert headers["cache-control"] == "no-store", path

    def test_avatars_stay_cacheable(self, client):
        """`no-store` on the static tree would cost ~300 image requests per render.

        The guard has to be narrow enough to leave the avatars alone.
        """
        # Any avatar will do; the header is what is under test, not the bytes.
        avatar = next(Path(settings.avatars_dir).glob("*.webp"), None)
        if avatar is None:
            pytest.skip("no cached avatars in this checkout")
        headers = client.get(f"/avatars/{avatar.name}").headers
        assert "no-store" not in headers.get("cache-control", "")

    def test_unknown_api_path_404s_as_json(self, client):
        assert client.get("/api/v1/nope").status_code == 404


class TestQuestions:
    def test_seeded_questions_listed_without_answers(self, client):
        rows = client.get("/api/v1/questions").json()
        assert len(rows) == 3
        # The list endpoint must never leak the correct option.
        assert all("correct" not in r for r in rows)


class TestRoomSuggestion:
    """`GET /lives/active` feeds the console's Room ID field.

    The console treats "no live", "stale live" and "upstream down" identically:
    an empty field to type into. A 500 would not be that, so degradation is
    part of the contract rather than an error path.
    """

    def test_suggests_the_room_of_a_live_started_today(self, client):
        client.upstream.active = {
            "room_id": "7691759076723772182",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "host_username": "@alexrmr74",
            "active": True,
        }

        body = client.get("/api/v1/lives/active").json()

        assert body["room_id"] == "7691759076723772182"
        assert body["host_username"] == "@alexrmr74"
        assert body["started_at"].endswith("Z")

    def test_no_suggestion_when_the_live_started_yesterday(self, client):
        client.upstream.active = {
            "room_id": "7691759076723772182",
            "started_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "host_username": "@alexrmr74",
            "active": True,
        }

        assert client.get("/api/v1/lives/active").json() == {
            "room_id": None, "host_username": None, "started_at": None
        }

    def test_no_suggestion_when_the_live_ended(self, client):
        client.upstream.active = {
            "room_id": "7691759076723772182",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "active": False,
        }

        assert client.get("/api/v1/lives/active").json()["room_id"] is None

    def test_no_suggestion_when_there_is_no_live(self, client):
        assert client.get("/api/v1/lives/active").json()["room_id"] is None

    def test_unreachable_upstream_degrades_instead_of_failing(self, client):
        import httpx

        client.upstream.active_error = httpx.ConnectError("down")

        r = client.get("/api/v1/lives/active")

        # A 500 here would look like a broken console and block the show.
        assert r.status_code == 200
        assert r.json()["room_id"] is None

    def test_suggestion_is_not_sticky_across_calls(self, client):
        """The stream may end between page loads; the answer must be re-read."""
        client.upstream.active = {
            "room_id": "7691759076723772182",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }
        assert client.get("/api/v1/lives/active").json()["room_id"] is not None

        client.upstream.active = None
        assert client.get("/api/v1/lives/active").json()["room_id"] is None


class TestCurrentLive:
    """`GET /lives/current` backs the host's explicit room-id button.

    Different contract, different purpose: the press is the host asserting they
    are streaming, so a live from a previous UTC day is returned anyway and named
    by `stale`. That is the case the auto-fill gives up on, so this route exists
    to disagree with it -- and, like the suggestion route, it never fails.
    """

    def test_returns_a_live_started_today(self, client):
        client.upstream.active = {
            "room_id": "7691759076723772182",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "host_username": "@alexrmr74",
            "active": True,
        }

        body = client.get("/api/v1/lives/current").json()

        assert body["room_id"] == "7691759076723772182"
        assert body["host_username"] == "@alexrmr74"
        assert body["stale"] is False
        # Millisecond precision with a Z, like `ts` everywhere else: upstream
        # sends microseconds, which `new Date()` does not parse consistently.
        assert body["started_at"].endswith("Z")
        assert "." in body["started_at"]

    def test_yesterdays_live_comes_back_flagged_not_filtered(self, client):
        """THE CASE THE BUTTON EXISTS FOR: 20:00 local is 23:00 UTC."""
        client.upstream.active = {
            "room_id": "7691759076723772182",
            "started_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "host_username": "@alexrmr74",
            "active": True,
        }

        body = client.get("/api/v1/lives/current").json()

        assert body["room_id"] == "7691759076723772182"
        assert body["stale"] is True

    def test_the_two_routes_disagree_only_about_a_previous_day(self, client):
        """One upstream live, two contracts. The frozen one stays null-shaped."""
        client.upstream.active = {
            "room_id": "7691759076723772182",
            "started_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "active": True,
        }

        assert client.get("/api/v1/lives/active").json()["room_id"] is None
        assert client.get("/api/v1/lives/current").json()["room_id"] is not None

    def test_no_live_yields_the_null_shape(self, client):
        assert client.get("/api/v1/lives/current").json() == {
            "room_id": None, "host_username": None, "started_at": None, "stale": False
        }

    def test_an_ended_live_yields_the_null_shape(self, client):
        client.upstream.active = {
            "room_id": "7691759076723772182",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "active": False,
        }

        assert client.get("/api/v1/lives/current").json()["room_id"] is None

    def test_an_unparseable_live_yields_the_null_shape(self, client):
        """A room id nobody can trust is worse than no room id."""
        client.upstream.active = {
            "room_id": "7691759076723772182",
            "started_at": "not-a-date",
            "active": True,
        }

        assert client.get("/api/v1/lives/current").json()["room_id"] is None

    def test_unreachable_upstream_degrades_instead_of_failing(self, client):
        import httpx

        client.upstream.active_error = httpx.ConnectError("down")

        r = client.get("/api/v1/lives/current")

        # Same rule as the suggestion route: a 500 here is indistinguishable
        # from a broken console, and the host cannot start a show over it.
        assert r.status_code == 200
        assert r.json() == {
            "room_id": None, "host_username": None, "started_at": None, "stale": False
        }

    def test_the_live_is_re_read_on_every_press(self, client):
        """The stream can end between two presses, so nothing is cached."""
        client.upstream.active = {
            "room_id": "7691759076723772182",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }
        assert client.get("/api/v1/lives/current").json()["room_id"] is not None

        client.upstream.active = None
        assert client.get("/api/v1/lives/current").json()["room_id"] is None


class TestShowLifecycle:
    def test_create_show_builds_pending_rounds(self, client):
        r = client.post(
            "/api/v1/shows",
            json={"room_id": "12345", "question_count": 2, "question_time_limit_s": 10},
        )
        assert r.status_code == 201
        body = r.json()
        assert len(body["rounds"]) == 2
        assert all(rd["status"] == "pending" for rd in body["rounds"])
        assert all(rd["question"]["correct"] is None for rd in body["rounds"])

    def test_show_not_found(self, client):
        assert client.get("/api/v1/shows/999").status_code == 404

    def test_active_show_is_none_initially(self, client):
        assert client.get("/api/v1/shows/active").json()["show_id"] is None

    def test_active_show_reports_newest(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        active = client.get("/api/v1/shows/active").json()
        assert active["show_id"] == show["id"]
        # Nothing to display until the host opens a round.
        assert active["round_id"] is None
        assert active["status"] is None

    def test_active_show_tracks_open_round(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 2}).json()
        rnd = client.post(f"/api/v1/shows/{show['id']}/rounds").json()
        active = client.get("/api/v1/shows/active").json()
        assert active["round_id"] == rnd["id"]
        assert active["status"] == "open"

    def test_active_show_prefers_open_over_recently_closed(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 3}).json()
        first = client.post(f"/api/v1/shows/{show['id']}/rounds").json()
        client.post(f"/api/v1/rounds/{first['id']}/close")
        second = client.post(f"/api/v1/shows/{show['id']}/rounds").json()

        active = client.get("/api/v1/shows/active").json()
        assert active["round_id"] == second["id"]
        assert active["status"] == "open"

    def test_active_show_reports_the_last_played_round_when_none_is_open(self, client):
        """THE BUG: with no open round the fallback must look BACKWARDS.

        A forward scan returns position 0, so the moment round 2 closed, any
        client asking "what do I render right now?" was told to show question 1
        together with its answer -- for every question after the first.
        """
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 3}).json()
        first = client.post(f"/api/v1/shows/{show['id']}/rounds").json()
        client.post(f"/api/v1/rounds/{first['id']}/reveal")
        second = client.post(f"/api/v1/shows/{show['id']}/rounds").json()
        client.post(f"/api/v1/rounds/{second['id']}/reveal")

        active = client.get("/api/v1/shows/active").json()
        assert active["round_id"] == second["id"]
        assert active["status"] == "revealed"

    def test_current_round_id_survives_the_reveal_hold(self, client):
        """Between questions the audience is looking at a round, not at nothing.

        Autoplay holds the answer on screen for `reveal_display_s` with no round
        open, so an open-only lookup would report null exactly while the overlay
        is showing a question.
        """
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 2}).json()
        rnd = client.post(f"/api/v1/shows/{show['id']}/rounds").json()
        client.post(f"/api/v1/rounds/{rnd['id']}/reveal")

        body = client.get(f"/api/v1/shows/{show['id']}").json()
        assert body["current_round_id"] == rnd["id"]

    def test_open_round_returns_to_pending_next(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 3}).json()
        r = client.post(f"/api/v1/shows/{show['id']}/rounds")
        assert r.status_code == 200
        assert r.json()["status"] == "open"

    def test_cannot_open_two_rounds_at_once(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 3}).json()
        client.post(f"/api/v1/shows/{show['id']}/rounds")
        assert client.post(f"/api/v1/shows/{show['id']}/rounds").status_code == 409

    def test_answer_hidden_until_reveal(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        rnd = client.post(f"/api/v1/shows/{show['id']}/rounds").json()

        closed = client.post(f"/api/v1/rounds/{rnd['id']}/close").json()
        assert closed["status"] == "closed"
        # Scoring is done, but the audience must not see the answer yet.
        assert closed["correct_option"] is None
        assert closed["question"]["correct"] is None

        revealed = client.post(f"/api/v1/rounds/{rnd['id']}/reveal").json()
        assert revealed["status"] == "revealed"
        assert revealed["correct_option"] == "A"
        assert revealed["question"]["correct"] == "A"

    def test_show_payload_gates_answer_by_status(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        rnd = client.post(f"/api/v1/shows/{show['id']}/rounds").json()
        client.post(f"/api/v1/rounds/{rnd['id']}/close")

        during = client.get(f"/api/v1/shows/{show['id']}").json()
        assert during["rounds"][0]["correct_option"] is None

        client.post(f"/api/v1/rounds/{rnd['id']}/reveal")
        after = client.get(f"/api/v1/shows/{show['id']}").json()
        assert after["rounds"][0]["correct_option"] == "A"


def _wait_for_counts(client, show_id: int, timeout: float = 5.0) -> dict:
    """Poll the show payload until the open round has counted a vote.

    Ingestion is the poller's background job, so votes land on its cadence rather
    than the moment the fake upstream receives them. Asserting on the first read
    would be testing the poll interval, not the payload.
    """
    deadline = time.monotonic() + timeout
    tally: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/shows/{show_id}").json()
        tally = body["rounds"][0]["tally"] or {}
        if tally.get("unique_voters"):
            return tally
        time.sleep(0.05)
    return tally


class TestShowPayload:
    def test_created_show_does_not_autoplay_by_default(self, client):
        """A show created without `autoplay` must sit still.

        The console always asks for autoplay, so the default only matters for
        scripts and tests -- where a background task racing the assertions would
        be a nuisance, not a feature.
        """
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 2}).json()
        assert show["autoplay"] is False
        assert all(r["status"] == "pending" for r in show["rounds"])

    def test_autoplay_flag_is_reported_back(self, client):
        show = client.post(
            "/api/v1/shows", json={"room_id": "1", "question_count": 1, "autoplay": True}
        ).json()
        assert show["autoplay"] is True

    def test_no_deadline_while_waiting_for_the_first_answer(self, client):
        """A round with no first answer waits forever, so it has no deadline.

        Both clients hide the countdown on a null deadline, which is exactly the
        wanted behaviour: no clock is running until somebody answers.
        """
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        rnd = client.post(f"/api/v1/shows/{show['id']}/rounds").json()

        assert rnd["status"] == "open"
        assert rnd["deadline_epoch_ms"] is None

        body = client.get(f"/api/v1/shows/{show['id']}").json()
        cur = next(r for r in body["rounds"] if r["status"] == "open")
        assert cur["deadline_epoch_ms"] is None

    def test_deadline_is_sent_once_the_first_answer_lands(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        client.post(f"/api/v1/shows/{show['id']}/rounds")
        client.upstream.add_comment(7001, "A", uid="alice")

        # Ingestion is the poller's background job, so wait on the vote it counts
        # rather than on the first read.
        _wait_for_counts(client, show["id"])
        body = client.get(f"/api/v1/shows/{show['id']}").json()
        cur = next(r for r in body["rounds"] if r["status"] == "open")

        # The countdown is pure arithmetic for the client: it needs a deadline and
        # the server clock it was measured against, not a timestamp to parse.
        assert cur["deadline_epoch_ms"] is not None
        assert cur["deadline_epoch_ms"] > cur["server_epoch_ms"]

    def test_no_deadline_for_a_pending_round(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        rnd = show["rounds"][0]
        assert rnd["status"] == "pending"
        assert rnd["deadline_epoch_ms"] is None

    def test_open_round_payload_carries_a_live_tally(self, client):
        """The overlay has no tally endpoint of its own; it renders the show payload.

        `tally_json` is only written at close, so a payload without a live tally
        would show zeros for the entire voting window.
        """
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        client.post(f"/api/v1/shows/{show['id']}/rounds")
        client.upstream.add_comment(7001, "A", uid="alice")
        client.upstream.add_comment(7002, "B", uid="bob")

        tally = _wait_for_counts(client, show["id"])
        assert tally["counts"]["A"] == 1
        assert tally["counts"]["B"] == 1
        assert tally["unique_voters"] == 2
        assert tally["first_by_option"]["A"]["unique_id"] == "alice"

    def test_closed_round_keeps_its_frozen_tally(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        rnd = client.post(f"/api/v1/shows/{show['id']}/rounds").json()
        client.upstream.add_comment(7001, "A", uid="alice")
        client.post(f"/api/v1/rounds/{rnd['id']}/close")

        client.upstream.add_comment(9999, "B", uid="latecomer")
        body = client.get(f"/api/v1/shows/{show['id']}").json()

        # Frozen: the late comment must not move a result already on screen.
        assert body["rounds"][0]["tally"]["counts"]["B"] == 0

    def test_tally_shape_matches_spa_contract(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        rnd = client.post(f"/api/v1/shows/{show['id']}/rounds").json()
        tally = client.get(f"/api/v1/rounds/{rnd['id']}/tally").json()
        # The SPA polls this every second; these keys are a public contract.
        assert set(tally) == {"counts", "unique_voters", "first_by_option"}
        assert tally["counts"] == {"A": 0, "B": 0, "C": 0, "D": 0}
        assert tally["unique_voters"] == 0

    def test_tally_of_missing_round_404s(self, client):
        assert client.get("/api/v1/rounds/999/tally").status_code == 404

    def test_ranking_empty_before_any_vote(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        assert client.get(f"/api/v1/shows/{show['id']}/ranking").json() == []


class TestIngestEndToEnd:
    def _round(self, client, upstream):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 2}).json()
        rnd = client.post(f"/api/v1/shows/{show['id']}/rounds").json()
        return show, rnd

    def test_votes_land_and_count(self, client):
        upstream = client.upstream
        show, rnd = self._round(client, upstream)
        upstream.add_comment(2001, "A", uid="alice")
        upstream.add_comment(2002, "B", uid="bob")
        upstream.add_comment(2003, "C", uid="carol")

        assert client.post(f"/api/v1/rounds/{rnd['id']}/close").status_code == 200
        frozen = client.get(f"/api/v1/rounds/{rnd['id']}/tally").json()

        assert frozen["unique_voters"] == 3
        assert frozen["counts"]["A"] == 1
        assert frozen["counts"]["B"] == 1
        assert frozen["counts"]["D"] == 0

    def test_chat_before_round_open_is_rejected(self, client):
        upstream = client.upstream
        upstream.add_comment(100, "A", uid="early")
        show, rnd = self._round(client, upstream)
        # Priming at open already consumed the pre-round event.
        upstream.add_comment(5001, "A", uid="late")

        client.post(f"/api/v1/rounds/{rnd['id']}/close")
        ranking = client.get(f"/api/v1/shows/{show['id']}/ranking").json()
        voted = {r["unique_id"] for r in ranking}
        assert "early" not in voted
        assert "late" in voted

    def test_one_vote_per_user_survives_chat_retries(self, client):
        upstream = client.upstream
        show, rnd = self._round(client, upstream)
        upstream.add_comment(5001, "A", uid="spammer")
        upstream.add_comment(5002, "B", uid="spammer")
        upstream.add_comment(5003, "C", uid="spammer")

        client.post(f"/api/v1/rounds/{rnd['id']}/close")
        tally = client.get(f"/api/v1/rounds/{rnd['id']}/tally").json()
        assert tally["unique_voters"] == 1
        assert tally["counts"]["A"] == 1  # first vote wins

    def test_first_by_option_set_regardless_of_correctness(self, client):
        upstream = client.upstream
        show, rnd = self._round(client, upstream)
        upstream.add_comment(5001, "D", uid="first_d")
        upstream.add_comment(5002, "D", uid="second_d")

        client.post(f"/api/v1/rounds/{rnd['id']}/close")
        tally = client.get(f"/api/v1/rounds/{rnd['id']}/tally").json()
        assert tally["first_by_option"]["D"]["unique_id"] == "first_d"
        # D is wrong, so nobody scored.
        ranking = client.get(f"/api/v1/shows/{show['id']}/ranking").json()
        assert all(r["points"] == 0 for r in ranking)

    def test_non_votes_ignored(self, client):
        upstream = client.upstream
        show, rnd = self._round(client, upstream)
        upstream.add_comment(5001, "Hola amigos")
        upstream.add_comment(5002, "ABCD")
        upstream.add_comment(5003, "A B")

        client.post(f"/api/v1/rounds/{rnd['id']}/close")
        tally = client.get(f"/api/v1/rounds/{rnd['id']}/tally").json()
        assert tally["unique_voters"] == 0

    def test_scores_and_ranking_after_reveal(self, client):
        upstream = client.upstream
        show, rnd = self._round(client, upstream)
        upstream.add_comment(5001, "A", uid="winner")
        upstream.add_comment(5002, "B", uid="loser")

        client.post(f"/api/v1/rounds/{rnd['id']}/close")
        ranking = client.get(f"/api/v1/shows/{show['id']}/ranking").json()
        by_id = {r["unique_id"]: r for r in ranking}
        assert by_id["winner"]["correct_count"] == 1
        assert by_id["winner"]["points"] > 0
        assert by_id["loser"]["points"] == 0

    def test_frozen_tally_ignores_late_chat(self, client):
        upstream = client.upstream
        show, rnd = self._round(client, upstream)
        upstream.add_comment(5001, "A", uid="early")
        client.post(f"/api/v1/rounds/{rnd['id']}/close")
        before = client.get(f"/api/v1/rounds/{rnd['id']}/tally").json()

        upstream.add_comment(9999, "B", uid="latecomer")
        client.get(f"/api/v1/rounds/{rnd['id']}/tally")
        after = client.get(f"/api/v1/rounds/{rnd['id']}/tally").json()

        assert before == after


def _mark_finished(client, show_id: int) -> None:
    """Put a show in the finished state the API cannot produce on demand.

    The runner is the only writer of that status, and reaching it through autoplay
    would mean racing a clock instead of asserting a rule. The TestClient's portal
    runs this on the same event loop as the app, so the engine is not shared across
    loops.
    """
    from app.infra.models import Show

    async def _write() -> None:
        async with client.maker() as session:
            show = await session.get(Show, show_id)
            show.status = "finished"
            await session.commit()

    client.portal.call(_write)  # type: ignore[union-attr]


class TestAutoplay:
    """The endpoint surface of the runner: start it, stop it, ask about it.

    Timing belongs in `test_show_runner.py`; here the contract is only that the
    routes reach the runner and report its state honestly.
    """

    def test_autoplay_is_off_until_asked(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        assert client.get(f"/api/v1/shows/{show['id']}").json()["autoplay"] is False

    def test_start_then_stop_reports_both_states(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()

        started = client.post(f"/api/v1/shows/{show['id']}/autoplay")
        assert started.status_code == 200
        assert started.json()["autoplay"] is True

        stopped = client.delete(f"/api/v1/shows/{show['id']}/autoplay")
        assert stopped.status_code == 200
        assert stopped.json()["autoplay"] is False

    def test_start_is_idempotent(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        assert client.post(f"/api/v1/shows/{show['id']}/autoplay").json()["autoplay"] is True
        assert client.post(f"/api/v1/shows/{show['id']}/autoplay").json()["autoplay"] is True

    def test_stop_is_idempotent(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        assert client.delete(f"/api/v1/shows/{show['id']}/autoplay").status_code == 200
        assert client.delete(f"/api/v1/shows/{show['id']}/autoplay").status_code == 200

    def test_unknown_show_404s_on_both_routes(self, client):
        assert client.post("/api/v1/shows/999/autoplay").status_code == 404
        assert client.delete("/api/v1/shows/999/autoplay").status_code == 404

    def test_finished_show_cannot_restart(self, client):
        """Nothing is left to play, so a 409 beats a button that silently does nothing."""
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 1}).json()
        _mark_finished(client, show["id"])

        assert client.post(f"/api/v1/shows/{show['id']}/autoplay").status_code == 409

    def test_stopping_mid_round_leaves_it_open(self, client):
        show = client.post("/api/v1/shows", json={"room_id": "1", "question_count": 2}).json()
        rnd = client.post(f"/api/v1/shows/{show['id']}/rounds").json()
        client.post(f"/api/v1/shows/{show['id']}/autoplay")

        client.delete(f"/api/v1/shows/{show['id']}/autoplay")

        body = client.get(f"/api/v1/shows/{show['id']}").json()
        assert body["current_round_id"] == rnd["id"]
        assert body["rounds"][0]["status"] == "open"



