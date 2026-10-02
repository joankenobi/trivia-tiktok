from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
DATA_DIR = BASE_DIR / "data"
QUESTIONS_FILE = DATA_DIR / "questions.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="TRIVIA_",
        extra="ignore",
    )

    app_name: str = "trivia-game"

    # Upstream tiktoklives_back read API.
    lives_api_base_url: str = "http://127.0.0.1:8081"
    lives_api_timeout_s: float = 10.0
    events_page_limit: int = 200

    # Poller cadence for the per-round ingest loop.
    poll_interval_s: float = 0.9
    poll_backoff_max_s: float = 2.0

    # Avatar cache. Signed CDN URLs expire, so bytes are captured while live.
    avatars_dir: Path = WEB_DIR / "avatars"
    avatar_timeout_s: float = 5.0

    # Autoplay: how long a round accepts votes, and how long the answer stays on
    # screen before the next question opens. Both are part of the pacing the
    # audience sees, so they are configuration and not literals in the runner.
    question_time_limit_s: int = 20
    reveal_display_s: int = 8
    questions_per_show: int = 10

    database_url: str = Field(default="")

    @property
    def sqlite_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{BASE_DIR / 'trivia_game.db'}"


settings = Settings()
