"""TTS voiceover client.

Each new question is announced by posting its text to the TTS service. The call
is deliberately fire-and-forget: the round clock has to start on time even when
the voice service is slow or down, so the request runs in its own task and a
failure only reaches the log instead of stalling the show.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..config import settings

log = logging.getLogger(__name__)

TTS_PATH = "/api/v1/tts"

# Strong refs to in-flight announce tasks. Without them the event loop may
# garbage-collect a task mid-flight, silently dropping the question.
_inflight: set[asyncio.Task] = set()


class TTSClient:
    """Posts question text to the TTS service.

    `transport` is the injection seam for tests, matching `TikTokLiveClient`:
    the request shape is what matters, and a real socket would only slow the
    suite down or hit a live voice service.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.tts_api_base_url).rstrip("/")
        self.timeout_s = timeout_s or settings.tts_api_timeout_s
        self.transport = transport

    async def announce(self, text: str) -> None:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_s,
            transport=self.transport,
        ) as client:
            resp = await client.post(TTS_PATH, json={"text": text})
            resp.raise_for_status()


_default_client = TTSClient()


async def _announce_safely(text: str) -> None:
    """Announce without ever raising into the caller's task."""
    try:
        await _default_client.announce(text)
    except httpx.HTTPError:
        # A dead or slow voice service must not take the game down with it.
        log.warning("TTS announce failed for question %r", text[:80], exc_info=True)


def notify_question(text: str | None) -> None:
    """Schedule a question announcement without blocking the caller.

    Called from the round-open path, which is on the clock: awaiting the POST
    here would delay the round by the service's latency. A missing question text
    is a no-op.
    """
    if not text:
        return
    task = asyncio.get_running_loop().create_task(
        _announce_safely(text), name="tts-announce"
    )
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)
