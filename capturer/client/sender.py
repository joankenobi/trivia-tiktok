"""Batch transport: dry-run JSONL writer or single-POST HTTP sender.

One request per batch — NEVER per event. Failure policy (v1): 1 retry with a
short backoff, then log and drop (return False, don't raise).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from client.config import Settings
from client.models import Batch

logger = logging.getLogger(__name__)


class DryRunSender:
    """Append each batch as one JSON line into ``data/batches/{stream_id}.jsonl``."""

    def __init__(self, output_dir: Path | str) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _target_path(self, batch: Batch) -> Path:
        stream_id = batch.stream_id or batch.room_id or "unknown"
        safe = "".join(c for c in str(stream_id) if c.isalnum() or c in "-_")
        return self.output_dir / f"{safe or 'unknown'}.jsonl"

    async def send(self, batch: Batch) -> bool:
        path = self._target_path(batch)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(batch.model_dump_json() + "\n")
        logger.info(
            "flushed batch with %d events -> %s",
            len(batch.events),
            path,
        )
        return True


class HttpSender:
    """POST a whole ``Batch`` JSON body to ``endpoint`` via a shared async client."""

    def __init__(self, endpoint: str, client: httpx.AsyncClient, retries: int = 1, backoff: float = 2.0) -> None:
        self.endpoint = endpoint
        self.client = client
        self.retries = retries
        self.backoff = backoff

    async def send(self, batch: Batch) -> bool:
        payload = batch.model_dump(mode="json")
        for attempt in range(self.retries + 1):
            try:
                response = await self.client.post(self.endpoint, json=payload)
                if response.status_code < 400:
                    logger.info("posted batch with %d events -> %s", len(batch.events), self.endpoint)
                    return True
                logger.warning("POST %s -> HTTP %s (attempt %d/%d)", self.endpoint, response.status_code, attempt + 1, self.retries + 1)
            except httpx.HTTPError as exc:
                logger.warning("POST %s failed: %s (attempt %d/%d)", self.endpoint, exc, attempt + 1, self.retries + 1)
            if attempt < self.retries:
                await asyncio.sleep(self.backoff)
        logger.error("batch dropped after %d attempts (%d events)", self.retries + 1, len(batch.events))
        return False


def create_sender(settings: Settings) -> DryRunSender | HttpSender:
    if settings.is_dry_run:
        return DryRunSender(settings.output_dir)
    return HttpSender(settings.api_endpoint, httpx.AsyncClient(timeout=10.0))