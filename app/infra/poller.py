"""Per-room ingest poller.

One asyncio task per open round, plus one monotonic cursor per room.

The cursor is the important part. It is owned here (not derived from vote rows)
because it must advance across *all* comments, including the ~99% that are not
votes. Sharing one monotonic cursor per room across rounds is what keeps round
N+1 from accepting chat that belongs to round N's window.

Two failure modes are handled explicitly:
- Network errors back off (never above `poll_backoff_max_s`) and never kill the
  round: a dead upstream must not cost the audience their votes.
- On close the caller awaits a final drain, so votes that landed between the
  last tick and the close are not lost.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import settings
from ..services import session_service as svc
from .models import Round
from .tiktok_live import TikTokLiveClient

log = logging.getLogger(__name__)

BACKOFF_GROWTH = 1.5


def next_backoff(current: float, ceiling: float) -> float:
    """Backoff progression, capped. Pure so the ceiling is directly testable."""
    return min(current * BACKOFF_GROWTH, ceiling)


class RoundPoller:
    def __init__(
        self,
        round_id: int,
        room_id: str,
        sessionmaker: async_sessionmaker[AsyncSession],
        registry: "PollerRegistry",
    ) -> None:
        self.round_id = round_id
        self.room_id = room_id
        self.sessionmaker = sessionmaker
        self.registry = registry
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name=f"poller-round-{self.round_id}"
            )

    async def stop(self, final_drain: bool = True) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        if final_drain:
            await self.drain_once()

    async def _run(self) -> None:
        backoff = settings.poll_interval_s
        while not self._stop.is_set():
            try:
                await self.poll_once()
                backoff = settings.poll_interval_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # upstream flakiness must not kill the round
                log.warning("round %s poll failed: %s", self.round_id, exc)
                backoff = next_backoff(backoff, settings.poll_backoff_max_s)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass

    async def poll_once(self) -> int:
        """One drain pass. Returns how many ballots were accepted."""
        async with self.sessionmaker() as session:
            rnd = await session.get(Round, self.round_id)
            if rnd is None or rnd.status != "open":
                return 0

            cursor = self.registry.cursor(self.room_id)
            comments = await self.registry.client.fetch_since(self.room_id, cursor)
            if not comments:
                return 0

            accepted = 0
            opened_cursor = rnd.opened_cursor or 0
            for comment in comments:
                if comment.event_id <= opened_cursor:
                    continue
                answer = await svc.ingest_event(session, rnd, comment)
                if answer is not None:
                    accepted += 1

            # Advance the shared cursor to the high-water mark of what we saw,
            # so already-processed comments are never re-read next tick.
            self.registry.advance(self.room_id, comments[-1].event_id)
            await session.commit()
            return accepted

    async def drain_once(self) -> int:
        """Final pass at close so nothing in flight is dropped."""
        try:
            return await self.poll_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("round %s final drain failed: %s", self.round_id, exc)
            return 0


class PollerRegistry:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        client: TikTokLiveClient | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.client = client or TikTokLiveClient()
        self._cursors: dict[str, int] = {}
        self._pollers: dict[int, RoundPoller] = {}

    def cursor(self, room_id: str) -> int:
        return self._cursors.get(room_id, 0)

    def advance(self, room_id: str, event_id: int) -> None:
        """Monotonic: never moves backwards, whatever arrives out of order."""
        if event_id > self._cursors.get(room_id, 0):
            self._cursors[room_id] = event_id

    async def prime(self, room_id: str) -> int:
        """Drain history once to learn the current stream position.

        Called before the first round opens so pre-existing chat is excluded.
        A failure here is non-fatal: the cursor stays where it is, and at worst
        the first round scores some early chat.
        """
        try:
            comments = await self.client.fetch_since(room_id, self.cursor(room_id))
            if comments:
                self.advance(room_id, comments[-1].event_id)
        except Exception as exc:
            log.warning("could not prime cursor for %s: %s", room_id, exc)
        return self.cursor(room_id)

    def start(self, round_id: int, room_id: str) -> RoundPoller:
        poller = RoundPoller(round_id, room_id, self.sessionmaker, self)
        poller.start()
        self._pollers[round_id] = poller
        return poller

    async def stop(self, round_id: int) -> None:
        poller = self._pollers.pop(round_id, None)
        if poller is not None:
            await poller.stop()

    async def stop_all(self) -> None:
        for round_id in list(self._pollers):
            await self.stop(round_id)
