"""Request/response contracts.

`TallyOut` is the contract the SPA polls every second, so its field names are
part of the public API: `counts`, `unique_voters`, `first_by_option`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_serializer


class CreateShowIn(BaseModel):
    room_id: str
    question_count: int = Field(default=10, ge=1, le=100)
    question_time_limit_s: int = Field(default=20, ge=3, le=120)
    # The host console always sends `true`; the default stays `false` so a show
    # created by a script or a test starts in a readable, fully manual state
    # instead of racing a background task.
    autoplay: bool = False


class FirstVoterOut(BaseModel):
    unique_id: str
    nickname: str
    avatar_path: str | None = None
    event_id: int
    ts_epoch_us: int


class TallyOut(BaseModel):
    counts: dict[str, int]
    unique_voters: int
    # First responder per option, correct or incorrect. Visual only.
    first_by_option: dict[str, FirstVoterOut] = Field(default_factory=dict)


class QuestionOut(BaseModel):
    id: int
    text: str
    category: str | None = None
    difficulty: str
    options: list[str]
    correct: str | None = None
    explanation: str | None = None


class RoundOut(BaseModel):
    id: int
    show_id: int
    question_id: int
    position: int
    status: Literal["pending", "open", "closed", "revealed"]
    question: QuestionOut
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    question_time_limit_s: int
    correct_option: str | None = None
    tally: TallyOut | None = None
    server_time: datetime

    # When the current phase ends, in epoch milliseconds, or None when nothing
    # is counting down. Epoch ms (not a serialized datetime) because both clients
    # render a live countdown and would otherwise have to parse and re-anchor a
    # timestamp whose only job is arithmetic.
    deadline_epoch_ms: int | None = None
    # Server clock at serialization time, so a client with a skewed clock still
    # renders the same remaining seconds the server will enforce.
    server_epoch_ms: int = 0


class ShowOut(BaseModel):
    id: int
    room_id: str
    question_count: int
    question_time_limit_s: int
    status: str
    current_round_id: int | None = None
    rounds: list[RoundOut]
    # Whether a runner task is currently driving this show.
    autoplay: bool = False


class ActiveLiveOut(BaseModel):
    """A room id the host can start from, or null fields meaning "type your own".

    Null is a normal answer here, not an error: there may be no live, the live
    may be from a previous day, or upstream may be down. The console treats all
    three the same way, so they need to be indistinguishable to it.
    """

    room_id: str | None = None
    host_username: str | None = None
    started_at: datetime | None = None

    @field_serializer("started_at")
    def _ser_dt(self, v: datetime | None) -> str | None:
        # Upstream sends microseconds, which `new Date()` does not parse
        # consistently, so the console never sees the raw value.
        if v is None:
            return None
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        else:
            v = v.astimezone(timezone.utc)
        return v.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RankingEntry(BaseModel):
    unique_id: str
    nickname: str
    avatar_path: str | None = None
    points: int
    correct_count: int
