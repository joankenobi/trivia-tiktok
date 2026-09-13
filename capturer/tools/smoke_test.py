"""Synthetic dry-run smoke test — no live connection needed.

Covers: models build, BatchBuffer size+interval flush, and DryRunSender
JSONL output. Run with the venv python:

    .venv\Scripts\python.exe tools\smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client.buffer import BatchBuffer  # noqa: E402
from client.models import CommentEvent, GiftEvent, LikeEvent, UserInfo  # noqa: E402
from client.sender import DryRunSender  # noqa: E402


async def main() -> int:
    user = UserInfo(
        unique_id="@smoke_user",
        nickname="Smoke User",
        profile_picture_url="https://example.com/avatar.jpg",
    )
    events = [
        CommentEvent(type="comment", timestamp="2026-01-01T00:00:00+00:00", user=user, data={"message": "hello"}),
        CommentEvent(type="comment", timestamp="2026-01-01T00:00:01+00:00", user=user, data={"message": "world"}),
        GiftEvent(type="gift", timestamp="2026-01-01T00:00:02+00:00", user=user, data={"gift_name": "Rose", "repeat_count": 1, "diamonds": 1}),
        LikeEvent(type="like", timestamp="2026-01-01T00:00:03+00:00", user=user, data={"like_count": 5, "delta": 5}),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp) / "data-batches-test"
        sender = DryRunSender(output_dir)
        buffer = BatchBuffer(interval_seconds=0.05, max_events=3, sender=sender)
        buffer.room_id = "test123"
        buffer.stream_id = "test123"

        flusher = buffer.start()
        try:
            size_flush = False
            for envelope in events:
                if buffer.add(envelope):
                    size_flush = True
                    await buffer.flush()
            await asyncio.sleep(0.15)
        finally:
            await buffer.stop()
            flusher.cancel()

        target = output_dir / "test123.jsonl"
        assert target.exists(), f"JSONL not written: {target}"
        lines = [l for l in target.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) >= 1, "no batch lines written"

        batches = [json.loads(line) for line in lines]
        for payload in batches:
            assert payload["version"] == 1, payload
            assert payload["room_id"] == "test123", payload
            assert payload["stream_id"] == "test123", payload

        all_types = {e["type"] for line in batches for e in line["events"]}
        total_events = sum(len(line["events"]) for line in batches)
        assert all_types == {"comment", "gift", "like"}, batches
        assert total_events == 4, batches
        assert any(len(line["events"]) == 3 for line in batches), "expected a size-flushed batch of 3"
        assert size_flush, "expected a size-triggered flush"

    print("PASS: 4 events buffered, batch JSONL written and validated (size flush + interval flush)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))