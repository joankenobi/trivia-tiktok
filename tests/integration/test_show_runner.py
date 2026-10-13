"""Tests for the automatic show runner.

The runner is the piece that replaces the host pressing buttons, so these tests
are about behaviour over wall-clock time: does a round close on its own, does the
answer appear, does the next question follow, does the show end.

Timing is the only thing that would make this slow, so the windows come from
`settings` and are patched to fractions of a second. The production code path is
identical either way: `show_runner.py` has no test-only branches.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.domain.session import RoundStatus
from app.infra.models import Base, Question, Round, Show
from app.services import session_service as svc
from app.services.show_runner import (
    SHOW_ACTIVE,
    SHOW_FINISHED,
    ShowRunner,
    Step,
    window_left,
)


class FakeRegistry:
    """Stand-in for `PollerRegistry`.

    The runner only needs the cursor window and a final drain on close. Recording
    the calls is the point: it proves the runner drains before freezing.
    """

    def __init__(self) -> None:
        self.cursor_value = 0
        self.primed = 0
        self.started: list[tuple[int, str]] = []
        self.stopped: list[int] = []

    def cursor(self, room_id: str) -> int:
        return self.cursor_value

    async def prime(self, room_id: str) -> int:
        self.primed += 1
        self.cursor_value = max(self.cursor_value, 500)
        return self.cursor_value

    def start(self, round_id: int, room_id: str) -> None:
        self.started.append((round_id, room_id))

    async def stop(self, round_id: int) -> None:
        self.stopped.append(round_id)
        self.cursor_value = max(self.cursor_value, 900)


@pytest.fixture(autouse=True)
def fast_clock(monkeypatch):
    """Collapse every window the runner reads into milliseconds."""
    monkeypatch.setattr(settings, "question_time_limit_s", 0.15)
    monkeypatch.setattr(settings, "reveal_display_s", 0.15)


@pytest_asyncio.fixture
async def maker():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessionmaker() as s:
        s.add_all(
            Question(id=i, text=f"Q{i}?", category="test", difficulty="medium",
                     options=["a", "b", "c", "d"], correct="A")
            for i in (1, 2, 3)
        )
        await s.commit()

    yield sessionmaker

    await engine.dispose()
    # Windows keeps the file locked if any connection outlived the engine.
    with contextlib.suppress(OSError):
        os.unlink(path)


@pytest_asyncio.fixture
async def new_runner(maker):
    """Factory for runners that are always stopped before the DB goes away.

    A leftover task would still hold a session, which on Windows blocks the temp
    file delete and turns a passing test into a teardown error.
    """
    created: list[ShowRunner] = []

    def build(registry=None) -> ShowRunner:
        runner = ShowRunner(maker, registry or FakeRegistry())
        created.append(runner)
        return runner

    yield build

    for runner in created:
        await runner.stop_all()


async def make_show(sessionmaker, question_ids=(1, 2, 3), limit_s: float = 0.05) -> int:
    async with sessionmaker() as session:
        show = await svc.create_show(session, "room-1", list(question_ids), limit_s)
        return show.id


async def statuses(sessionmaker, show_id: int) -> list[str]:
    async with sessionmaker() as session:
        show = await session.get(Show, show_id)
        return [r.status for r in sorted(show.rounds, key=lambda r: r.position)]


async def show_status(sessionmaker, show_id: int) -> str:
    async with sessionmaker() as session:
        show = await session.get(Show, show_id)
        return show.status


async def wait_for(predicate, timeout: float = 10.0, interval: float = 0.02):
    """Poll `predicate` until it returns something truthy.

    The runner is a background task, so a test asserts on observable state rather
    than on how long it slept. A timeout here means the runner never converged.
    Both plain and coroutine predicates are accepted, because some assertions are
    about a recorded call and others about the database.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return result
        await asyncio.sleep(interval)
    raise AssertionError("condition not reached within the timeout")


async def is_show_status(sessionmaker, show_id: int, expected: str) -> bool:
    """Coroutine predicate for `wait_for`."""
    return await show_status(sessionmaker, show_id) == expected


class TestWindowLeft:
    def test_full_window_right_after_open(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        assert window_left(now, 10) > 9.9

    def test_window_shrinks_as_time_passes(self):
        started = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=4)
        assert 5.9 < window_left(started, 10) < 6.1

    def test_expired_window_is_zero_not_negative(self):
        started = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=30)
        # A negative sleep would be a ValueError inside the runner loop.
        assert window_left(started, 10) == 0.0

    def test_missing_timestamp_has_no_window(self):
        assert window_left(None, 10) == 0.0


class TestFullShowRunsItself:
    async def test_runs_every_round_and_finishes(self, maker, new_runner):
        show_id = await make_show(maker)

        assert new_runner().start(show_id) is True
        await wait_for(lambda: is_show_status(maker, show_id, SHOW_FINISHED))

        assert await statuses(maker, show_id) == [RoundStatus.REVEALED.value] * 3

    async def test_answer_stays_on_screen_between_rounds(self, maker, new_runner):
        """End-to-end timing: the gap between closes must cover the reveal hold.

        The status-only tests above cannot catch this. A round that is revealed
        and immediately replaced still ends up `revealed`, so a runner that never
        paused would pass every one of them while the audience never saw a single
        correct answer. This asserts the wall clock instead.
        """
        settings.question_time_limit_s = 0.05
        settings.reveal_display_s = 0.4
        closed_at: list = []

        async def record(rnd):
            async with maker() as session:
                show = await session.get(Show, show_id)
                for r in show.rounds:
                    if r.status == RoundStatus.REVEALED.value and r.closed_at:
                        closed_at.append(r.closed_at)

        show_id = await make_show(maker, question_ids=(1, 2), limit_s=0.05)
        runner = new_runner()
        runner.start(show_id)
        await wait_for(lambda: is_show_status(maker, show_id, SHOW_FINISHED))
        await record(None)

        assert len(closed_at) == 2
        gap = (closed_at[1] - closed_at[0]).total_seconds()
        # Voting window plus the reveal hold, minus the poll tick that detected
        # each expiry. Without the hold this lands near the window alone.
        assert gap > 0.3, f"only {gap:.3f}s between rounds: the answer was skipped"

    async def test_start_is_idempotent(self, maker, new_runner):
        show_id = await make_show(maker)
        runner = new_runner()
        assert runner.start(show_id) is True
        # A second POST must not spawn a second task racing on the same rounds.
        assert runner.start(show_id) is False

    async def test_runner_drains_before_freezing_each_round(self, maker, new_runner):
        registry = FakeRegistry()
        show_id = await make_show(maker, question_ids=(1, 2))

        new_runner(registry).start(show_id)
        await wait_for(lambda: is_show_status(maker, show_id, SHOW_FINISHED))

        # Poller stopped for both rounds, and the first open primed the cursor so
        # the round starts after existing chat.
        assert registry.stopped == sorted(registry.stopped)
        assert len(registry.stopped) == 2
        assert registry.primed == 1

    async def test_show_starts_active_and_ends_finished(self, maker, new_runner):
        show_id = await make_show(maker, question_ids=(1,))
        assert await show_status(maker, show_id) == SHOW_ACTIVE

        new_runner().start(show_id)
        await wait_for(lambda: is_show_status(maker, show_id, SHOW_FINISHED))


class TestRunnerAdoptsManualWork:
    """The runner plans from the database, so a hand-driven round is not a conflict."""

    async def test_adopts_a_round_opened_by_hand(self, maker, new_runner):
        show_id = await make_show(maker, question_ids=(1,))
        async with maker() as session:
            rnd = (await session.execute(select(Round))).scalar_one()
            await svc.open_round(session, rnd.id, opened_cursor=0)

        new_runner().start(show_id)
        await wait_for(lambda: is_show_status(maker, show_id, SHOW_FINISHED))
        assert await statuses(maker, show_id) == [RoundStatus.REVEALED.value]

    async def test_reveals_a_round_closed_by_hand(self, maker, new_runner):
        show_id = await make_show(maker, question_ids=(1,))
        async with maker() as session:
            rnd = (await session.execute(select(Round))).scalar_one()
            await svc.open_round(session, rnd.id, opened_cursor=0)
            await svc.close_round(session, rnd.id)

        new_runner().start(show_id)
        await wait_for(lambda: is_show_status(maker, show_id, SHOW_FINISHED))
        # Closed by hand, revealed by the runner: the audience still got the answer.
        assert await statuses(maker, show_id) == [RoundStatus.REVEALED.value]

    async def test_does_not_start_a_show_that_never_began(self, maker, new_runner):
        show_id = await make_show(maker)
        runner = new_runner()
        # Every round is pending: the host created the show but did not start it.
        assert await runner.resume_active_shows() == []
        assert runner.is_running(show_id) is False
        assert await statuses(maker, show_id) == [RoundStatus.PENDING.value] * 3


class TestResume:
    async def test_resumes_a_show_left_mid_question(self, maker, new_runner):
        show_id = await make_show(maker, question_ids=(1, 2))
        async with maker() as session:
            rnd = (
                await session.execute(select(Round).order_by(Round.position))
            ).scalars().first()
            await svc.open_round(session, rnd.id, opened_cursor=0)
            # Backdate the open round so its window is already over: a restart must
            # close it now, not grant a fresh full budget.
            rnd.opened_at = rnd.opened_at - timedelta(hours=1)
            await session.commit()

        runner = new_runner()
        assert await runner.resume_active_shows() == [show_id]
        await wait_for(lambda: is_show_status(maker, show_id, SHOW_FINISHED))

    async def test_ignores_finished_shows_on_resume(self, maker, new_runner):
        show_id = await make_show(maker, question_ids=(1,))
        async with maker() as session:
            show = await session.get(Show, show_id)
            show.status = SHOW_FINISHED
            await session.commit()

        runner = new_runner()
        assert await runner.resume_active_shows() == []
        assert runner.is_running(show_id) is False


class TestStop:
    async def test_stop_leaves_the_current_round_untouched(self, maker, new_runner):
        settings.question_time_limit_s = 60  # long enough that the runner only waits
        show_id = await make_show(maker, question_ids=(1, 2))
        registry = FakeRegistry()
        runner = new_runner(registry)

        runner.start(show_id)
        await wait_for(lambda: registry.started)

        await runner.stop(show_id)
        assert runner.is_running(show_id) is False
        # The open round stays open: the host took control mid-question.
        assert await statuses(maker, show_id) == [
            RoundStatus.OPEN.value,
            RoundStatus.PENDING.value,
        ]

    async def test_stop_all_stops_every_show(self, maker, new_runner):
        runner = new_runner()
        a = await make_show(maker)
        b = await make_show(maker)
        runner.start(a)
        runner.start(b)
        await runner.stop_all()
        assert not runner.is_running(a)
        assert not runner.is_running(b)

    async def test_stopping_an_unknown_show_is_a_noop(self, maker, new_runner):
        await new_runner().stop(999)


class TestPlanTransitions:
    """`_plan` is the decision table; assert it directly instead of via timing."""

    async def test_plan_opens_the_first_pending_round(self, maker, new_runner):
        show_id = await make_show(maker, question_ids=(1,))
        assert (await new_runner()._plan(show_id)).step is Step.OPEN

    async def test_plan_holds_the_revealed_answer_before_the_next_round(
        self, maker, new_runner
    ):
        """The audience has to see the answer before the next question lands.

        Regression guard. The `pending -> OPEN` branch used to sit above this one,
        so the runner opened the next round the instant it revealed the last one
        and the correct answer never reached the screen. Every status-only test
        still passed, because a round that flashes by still ends up `revealed`.
        """
        show_id = await make_show(maker, question_ids=(1, 2))
        async with maker() as session:
            rnd = (
                await session.execute(select(Round).order_by(Round.position))
            ).scalars().first()
            await svc.open_round(session, rnd.id, opened_cursor=0)
            await svc.close_round(session, rnd.id)
            await svc.reveal_round(session, rnd.id)

        action = await new_runner()._plan(show_id)
        assert action.step is Step.WAIT
        assert action.round_id == rnd.id
        assert action.delay_s > 0

    async def test_plan_opens_the_next_round_once_the_hold_expired(
        self, maker, new_runner
    ):
        show_id = await make_show(maker, question_ids=(1, 2))
        async with maker() as session:
            first = (
                await session.execute(select(Round).order_by(Round.position))
            ).scalars().first()
            await svc.open_round(session, first.id, opened_cursor=0)
            await svc.close_round(session, first.id)
            await svc.reveal_round(session, first.id)
            # Backdate the close: the answer has been on screen long enough.
            first.closed_at = first.closed_at - timedelta(hours=1)
            await session.commit()

        action = await new_runner()._plan(show_id)
        assert action.step is Step.OPEN
        assert action.round_id != first.id

    async def test_plan_waits_while_a_window_is_open(self, maker, new_runner):
        show_id = await make_show(maker, question_ids=(1,))
        async with maker() as session:
            rnd = (await session.execute(select(Round))).scalar_one()
            await svc.open_round(session, rnd.id, opened_cursor=0)

        action = await new_runner()._plan(show_id)
        assert action.step is Step.WAIT
        assert action.delay_s > 0

    async def test_plan_closes_when_the_window_expired(self, maker, new_runner):
        show_id = await make_show(maker, question_ids=(1,))
        async with maker() as session:
            rnd = (await session.execute(select(Round))).scalar_one()
            await svc.open_round(session, rnd.id, opened_cursor=0)
            rnd.opened_at = rnd.opened_at - timedelta(hours=1)
            await session.commit()

        assert (await new_runner()._plan(show_id)).step is Step.CLOSE

    async def test_plan_is_done_when_everything_is_revealed(self, maker, new_runner):
        show_id = await make_show(maker, question_ids=(1,))
        async with maker() as session:
            rnd = (await session.execute(select(Round))).scalar_one()
            await svc.open_round(session, rnd.id, opened_cursor=0)
            await svc.close_round(session, rnd.id)
            await svc.reveal_round(session, rnd.id)
            # Past the reveal hold there is nothing left to do.
            rnd.closed_at = rnd.closed_at - timedelta(hours=1)
            await session.commit()

        assert (await new_runner()._plan(show_id)).step is Step.DONE

    async def test_plan_is_done_for_a_missing_show(self, maker, new_runner):
        assert (await new_runner()._plan(999)).step is Step.DONE

    async def test_plan_is_done_for_a_finished_show(self, maker, new_runner):
        show_id = await make_show(maker, question_ids=(1,))
        async with maker() as session:
            show = await session.get(Show, show_id)
            show.status = SHOW_FINISHED
            await session.commit()

        assert (await new_runner()._plan(show_id)).step is Step.DONE


class TestVoiceover:
    """Opening a round is the moment a new question starts, so it speaks."""

    async def test_opening_a_round_announces_the_question(
        self, maker, new_runner, monkeypatch
    ):
        from app.services import show_runner as sr

        calls: list[str | None] = []
        monkeypatch.setattr(sr, "notify_question", calls.append)

        await make_show(maker, question_ids=(1,))
        async with maker() as session:
            rnd = (await session.execute(select(Round))).scalar_one()
            await sr.open_round_now(session, rnd, FakeRegistry())

        assert calls == ["Q1?"]
