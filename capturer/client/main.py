"""Phase 1 entrypoint: build the TikTok live capturer, wire it, and run it.

Usage (from the project root, with the venv active):

    python -m client

Prints connected room + each normalized event, and flushes batches to
``data/batches/`` (dry-run) or a single POST per batch to ``API_ENDPOINT``.
"""

from __future__ import annotations

import asyncio
import logging
import signal

import httpx
from TikTokLive import TikTokLiveClient
from TikTokLive.events import (
    CommentEvent as CaCommentEvent,
    ConnectEvent,
    DisconnectEvent,
    FollowEvent as CaFollowEvent,
    GiftEvent as CaGiftEvent,
    LikeEvent as CaLikeEvent,
    LiveEndEvent,
)

from client.buffer import BatchBuffer
from client.config import Settings
from client.models import EventEnvelope
from client.normalizers import TikTokNormalizer
from client.sender import create_sender

logger = logging.getLogger("client")


async def main() -> None:
    settings = Settings.load()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Keep console readable: httpx/httpcore spam every HTTP request at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logger.info("capturer starting for %s (dry_run=%s, interval=%ss, max=%s, log=%s)",
                settings.tiktok_username, settings.is_dry_run,
                settings.batch_interval_seconds, settings.batch_max_events,
                settings.log_level)

    kwargs: dict = {"unique_id": settings.tiktok_username}
    if settings.tiktok_web_proxy:
        kwargs["web_proxy"] = httpx.Proxy(settings.tiktok_web_proxy)
    if settings.tiktok_ws_proxy:
        kwargs["ws_proxy"] = httpx.Proxy(settings.tiktok_ws_proxy)

    client = TikTokLiveClient(**kwargs)
    buffer = BatchBuffer(
        interval_seconds=settings.batch_interval_seconds,
        max_events=settings.batch_max_events,
        sender=create_sender(settings),
        host_username=settings.tiktok_username,
    )
    normalizer = TikTokNormalizer()

    @client.on(ConnectEvent)
    async def on_connect(event: ConnectEvent) -> None:
        buffer.room_id = str(event.room_id)
        buffer.stream_id = str(event.room_id)
        logger.info("connected to live: room_id=%s", event.room_id)

    async def handle(event: object) -> None:
        envelope: EventEnvelope | None = normalizer.normalize(event)
        if envelope is None:
            return
        logger.info("event %-7s @%-20s %s", envelope.type,
                    envelope.user.unique_id, envelope.data)
        if buffer.add(envelope):
            logger.info("size flush triggered at %d events", buffer.pending)
            await buffer.flush()

    @client.on(CaCommentEvent)
    async def on_comment(event: CaCommentEvent) -> None:
        await handle(event)

    @client.on(CaGiftEvent)
    async def on_gift(event: CaGiftEvent) -> None:
        await handle(event)

    @client.on(CaLikeEvent)
    async def on_like(event: CaLikeEvent) -> None:
        await handle(event)

    @client.on(CaFollowEvent)
    async def on_follow(event: CaFollowEvent) -> None:
        await handle(event)

    async def final_flush(*_args: object) -> None:
        logger.info("live ended; flushing remaining events")
        await buffer.flush_all()

    @client.on(LiveEndEvent)
    async def on_live_end(event: LiveEndEvent) -> None:
        await final_flush(event)

    @client.on(DisconnectEvent)
    async def on_disconnect(event: DisconnectEvent) -> None:
        await final_flush(event)
        logger.info("disconnected from live")

    flusher = buffer.start()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(final_flush()))
        except NotImplementedError:
            pass

    try:
        # connect() is the blocking variant of start(): it awaits the
        # WebSocket loop task. start() alone returns immediately and
        # asyncio.run() would cancel the WS task -> process exits silently.
        await client.connect()
    except Exception as exc:
        logger.exception("capturer stopped due to error: %s", exc)
        raise
    finally:
        await buffer.stop()


if __name__ == "__main__":
    asyncio.run(main())