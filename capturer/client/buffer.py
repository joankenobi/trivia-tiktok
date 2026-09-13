"""In-memory event buffer flushed by timer (interval) or size (max_events)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from client.models import Batch, EventEnvelope

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    Sender = Callable[[Batch], Awaitable[bool]]

logger = logging.getLogger(__name__)


class BatchBuffer:
    """Accumulate events and hand full batches to a sender.

    Flush trigger: either ``interval_seconds`` elapsed with pending events or
    the queue length reached ``max_events`` — whichever comes first.
    """

    def __init__(self, interval_seconds: int, max_events: int, sender: Sender) -> None:
        self.interval_seconds = interval_seconds
        self.max_events = max_events
        self.sender = sender
        self.room_id: str | None = None
        self.stream_id: str | None = None
        self._events: list[EventEnvelope] = []
        self._flusher_task: asyncio.Task | None = None
        self._running = False

    def add(self, envelope: EventEnvelope) -> bool:
        """Queue an event. Returns True when this add triggered a size flush."""
        self._events.append(envelope)
        if len(self._events) >= self.max_events:
            return True
        return False

    @property
    def pending(self) -> int:
        return len(self._events)

    def _build_batch(self) -> Batch | None:
        if not self._events:
            return None
        try:
            started_at = self._events[0].timestamp
        except (KeyError, IndexError):
            started_at = datetime.now(timezone.utc).isoformat()
        return Batch(
            room_id=self.room_id,
            stream_id=self.stream_id,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc).isoformat(),
            events=self._events,
        )

    async def flush(self) -> bool:
        """Build a Batch from the pending events, send it, clear on success."""
        batch = self._build_batch()
        if batch is None:
            return False
        logger.info("flush: %d events buffered, sending...", len(batch.events))
        ok = await self.sender.send(batch)
        if ok:
            self._events.clear()
        else:
            logger.warning("flush failed; keeping %d events in buffer", len(self._events))
        return ok

    async def flush_all(self) -> None:
        """Force a final flush (used on disconnect / exit)."""
        while self._events:
            if not await self.flush():
                logger.warning("final flush failed; abandoning %d events", len(self._events))
                break

    async def _flusher_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.interval_seconds)
            if self._events:
                await self.flush()

    def start(self) -> asyncio.Task:
        """Launch the background flusher task."""
        if self._flusher_task is not None and not self._flusher_task.done():
            return self._flusher_task
        self._running = True
        self._flusher_task = asyncio.create_task(self._flusher_loop())
        return self._flusher_task

    async def stop(self) -> None:
        """Cancel the flusher task and flush whatever is left."""
        self._running = False
        if self._flusher_task is not None:
            self._flusher_task.cancel()
            try:
                await self._flusher_task
            except asyncio.CancelledError:
                pass
            self._flusher_task = None
        if self._events:
            await self.flush_all()