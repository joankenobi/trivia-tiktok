"""Settings loader backed by a root-level .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    tiktok_username: str
    tiktok_web_proxy: str = ""
    tiktok_ws_proxy: str = ""
    batch_interval_seconds: int = 2
    batch_max_events: int = 100
    log_level: str = "INFO"
    api_endpoint: str = ""
    dry_run: bool = True
    output_dir: Path = PROJECT_ROOT / "data" / "batches"

    @property
    def is_dry_run(self) -> bool:
        return self.dry_run or not self.api_endpoint

    @classmethod
    def load(cls, env_path: Path | None = None) -> "Settings":
        """Load .env from the project root (or an explicit path) with defaults."""
        env_path = env_path or PROJECT_ROOT / ".env"
        load_dotenv(env_path)

        username = os.getenv("TIKTOK_USERNAME", "").strip()
        if not username:
            raise ValueError(
                "TIKTOK_USERNAME is required. Set it in .env (e.g. TIKTOK_USERNAME=@you)."
            )

        return cls(
            tiktok_username=username,
            tiktok_web_proxy=os.getenv("TIKTOK_WEB_PROXY", ""),
            tiktok_ws_proxy=os.getenv("TIKTOK_WS_PROXY", ""),
            batch_interval_seconds=_get_int("BATCH_INTERVAL_SECONDS", 2),
            batch_max_events=_get_int("BATCH_MAX_EVENTS", 100),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            api_endpoint=os.getenv("API_ENDPOINT", "").strip(),
            dry_run=_get_bool("DRY_RUN", True),
        )