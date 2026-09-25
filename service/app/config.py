"""Settings loader backed by a root-level .env file.

Follows the same convention as the capturer: a plain dataclass plus
python-dotenv, no pydantic-settings indirection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

SERVICE_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite+aiosqlite:///./service.db"
    log_level: str = "INFO"

    # Server bind address/port (documented for uvicorn). Defaults to loopback:
    # binding 0.0.0.0 exposes the unauthenticated ingestion endpoint.
    host: str = "127.0.0.1"
    port: int = 8000

    # Request guards for the batch intake endpoint.
    max_batch_events: int = 500
    max_payload_bytes: int = 1_000_000

    # A stream is reported as "active" while its most recent batch arrived no
    # more than this many seconds ago.
    active_stream_window_seconds: int = 30

    @classmethod
    def load(cls, env_path: Path | None = None) -> "Settings":
        """Load .env from the service root (or an explicit path) with defaults."""
        env_path = env_path or SERVICE_ROOT / ".env"
        load_dotenv(env_path)
        return cls(
            database_url=os.getenv(
                "DATABASE_URL", "sqlite+aiosqlite:///./service.db"
            ).strip(),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            host=os.getenv("HOST", "127.0.0.1").strip(),
            port=int(os.getenv("PORT", "8000")),
            max_batch_events=int(os.getenv("MAX_BATCH_EVENTS", "500")),
            max_payload_bytes=int(os.getenv("MAX_PAYLOAD_BYTES", "1000000")),
            active_stream_window_seconds=int(
                os.getenv("ACTIVE_STREAM_WINDOW_SECONDS", "30")
            ),
        )


settings = Settings.load()