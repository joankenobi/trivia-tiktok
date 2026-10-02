"""Automatic show runner: the game plays itself.

One asyncio task per show drives the whole loop: open the next round, wait out
its window, close it, reveal the answer, hold it on screen, repeat. The host no
longer has to press anything per question.

Three decisions carry this module:

- **The clock lives on the server.** The OBS overlay has no UI and only polls,
  so a browser-side timer would stop the game the moment the host closed the tab.
- **The runner is a reconciler, not a script.** Every tick re-reads the show and
  asks "what is the next state this show needs?". That is what lets it adopt a
  round the host opened by hand, reveal a round the host closed by hand, and
  pick up a show that was mid-flight when the process restarted.
- **`open_round`/`close_round` are reused verbatim** from `session_service` via
  `open_round_now`/`close_round_now`. The cursor window is the single rule that
  stops round N+1 from accepting chat that belongs to round N, and it is only
  correct because open/close stay in that exact sequence. Duplicating the
  sequence in a second caller is how it silently breaks.

Durations come from settings and the database, never from a literal, so a test
runs this exact code path in milliseconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import settings
from ..domain.session import RoundStatus
from ..infra.models import Round, Show
from . import session_service as svc

log = logging.getLogger(__name__)

SHOW_ACTIVE = "active"
SHOW_FINISHED = "finished"

PENDING = RoundStatus.PENDING.value


def _utcnow_naive() -> datetime:
    """Naive UTC, matching how the service stores every timestamp."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def window_left(started_at: datetime | None, length_s: float) -> float:
    """Seconds left in a window that opened at `started_at`. Never negative.

    Anchored on the persisted `opened_at`/`closed_at` rather than on when this
    process started counting, so a restart mid-window resumes the remaining time
    instead of granting the round a second full budget.
    """
    if started_at is None:
        return 0.0
    deadline = started_at + timedelta(seconds=length_s)
    return max(0.0, (deadline - _utcnow_naive()).total_seconds())


class Step(str, Enum):
    """What the next tick of the loop has to do."""

    OPEN = "open"
    CLOSE = "close"
    REVEAL = "reveal"
    WAIT = "wait"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class Action:
    step: Step
    round_id: int | None = None
    delay_s: float = 0.0


async def open_round_now(
    session: AsyncSession, rnd: Round, registry
) -> Round:
    """Open `rnd` above the room's shared cursor. The manual route and the runner
    both go through here, so there is exactly one implementation of the window.
    """
    show = await session.get(Show, rnd.show_id)

    cursor = 0
    if registry is not None:
        cursor = registry.cursor(show.room_id)
        # Prime once before the first open: learn the current high-water mark so
        # the round starts after existing chat.
        if cursor == 0:
            cursor = await registry.prime(show.room_id)

    opened = await svc.open_round(session, rnd.id, cursor)
    if registry is not None:
        registry.start(opened.id, show.room_id)
    return opened


async def close_round_now(session: AsyncSession, rnd: Round, registry) -> Round:
    """Stop the poller (which drains), then freeze and score.

    Order matters. `stop` performs a final drain and advances the shared cursor to
    the last event observed; that cursor is what becomes this round's frozen
    upper bound. Freezing first would let late votes mutate a tally the audience
    already saw.
    """
    closed_cursor = None
    if registry is not None:
        await registry.stop(rnd.id)
        show = await session.get(Show, rnd.show_id)
        closed_cursor = registry.cursor(show.room_id)

    return await svc.close_round(session, rnd.id, closed_cursor=closed_cursor)


class ShowRunner:
    """Drives every show that has autoplay on. One task per show."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], registry) -> None:
        self.sessionmaker = sessionmaker
        self.registry = registry
        self._tasks: dict[int, asyncio.Task] = {}

    def is_running(self, show_id: int) -> bool:
        task = self._tasks.get(show_id)
        return task is not None and not task.done()

    def start(self, show_id: int) -> bool:
        """Begin driving `show_id`. Returns False when it was already running."""
        if self.is_running(show_id):
            return False
        self._tasks[show_id] = asyncio.create_task(
            self._run(show_id), name=f"show-runner-{show_id}"
        )
        return True

    async def stop(self, show_id: int) -> None:
        """Stop driving `show_id`, leaving its rounds exactly as they are."""
        task = self._tasks.pop(show_id, None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def stop_all(self) -> None:
        for show_id in list(self._tasks):
            await self.stop(show_id)

    async def resume_active_shows(self) -> list[int]:
        """Re-attach to shows that were already underway, and only those.

        A show with every round pending was created but never started, so
        resuming it would begin a game the host did not ask for yet. One
        non-pending round is the proof it was actually running.
        """
        async with self.sessionmaker() as session:
            shows = (
                (
                    await session.execute(
                        select(Show).where(Show.status == SHOW_ACTIVE)
                    )
                )
                .scalars()
                .all()
            )
        resumable = [
            s.id for s in shows if any(r.status != PENDING for r in s.rounds)
        ]
        for show_id in resumable:
            self.start(show_id)
        return resumable

    async def _run(self, show_id: int) -> None:
        try:
            while True:
                action = await self._plan(show_id)
                if action.step is Step.DONE:
                    await self._finish(show_id)
                    return
                if action.delay_s > 0:
                    await asyncio.sleep(action.delay_s)
                    continue
                await self._apply(action)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A dead upstream or an unexpected DB error must leave the show in a
            # readable state, not kill the process mid-live.
            log.exception("autoplay for show %s stopped on an error", show_id)
        finally:
            self._tasks.pop(show_id, None)

    async def _plan(self, show_id: int) -> Action:
        """The next state this show needs, from the database alone."""
        async with self.sessionmaker() as session:
            show = await session.get(Show, show_id)
            if show is None or show.status != SHOW_ACTIVE:
                return Action(Step.DONE)

            rounds = sorted(show.rounds, key=lambda r: r.position)
            if not rounds:
                return Action(Step.DONE)

            opened = next(
                (r for r in rounds if r.status == RoundStatus.OPEN.value), None
            )
            if opened is not None:
                left = window_left(opened.opened_at, opened.question_time_limit_s)
                return Action(
                    Step.WAIT if left > 0 else Step.CLOSE, opened.id, left
                )

            # A round closed by hand still owes the audience its answer.
            closed = next(
                (r for r in reversed(rounds) if r.status == RoundStatus.CLOSED.value),
                None,
            )
            if closed is not None:
                return Action(Step.REVEAL, closed.id)

            # The revealed answer owes the audience its full on-screen time before
            # the next question takes over. This check has to come above the
            # `pending` branch: otherwise the next round opens the instant this
            # one is revealed and the correct answer is never actually seen.
            # The newest *revealed* round, not simply the last one: with rounds
            # still pending, `rounds[-1]` is the pending one, not the answer.
            revealed = next(
                (r for r in reversed(rounds) if r.status == RoundStatus.REVEALED.value),
                None,
            )
            if revealed is not None:
                left = window_left(revealed.closed_at, settings.reveal_display_s)
                if left > 0:
                    return Action(Step.WAIT, revealed.id, left)

            pending = next((r for r in rounds if r.status == PENDING), None)
            if pending is not None:
                return Action(Step.OPEN, pending.id)

            return Action(Step.DONE)

    async def _apply(self, action: Action) -> None:
        async with self.sessionmaker() as session:
            rnd = await session.get(Round, action.round_id)
            if rnd is None:
                return
            if action.step is Step.OPEN:
                await open_round_now(session, rnd, self.registry)
            elif action.step is Step.CLOSE:
                # Close and reveal in the same tick: the answer appears exactly
                # when the window shuts, which is the whole point of a buzzer.
                closed = await close_round_now(session, rnd, self.registry)
                await svc.reveal_round(session, closed.id)
            elif action.step is Step.REVEAL:
                await svc.reveal_round(session, rnd.id)

    async def _finish(self, show_id: int) -> None:
        async with self.sessionmaker() as session:
            show = await session.get(Show, show_id)
            if show is None or show.status != SHOW_ACTIVE:
                return
            show.status = SHOW_FINISHED
            await session.commit()