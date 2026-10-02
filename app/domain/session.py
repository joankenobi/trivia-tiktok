"""Show / Round / Tally entities.

The question window is bounded by the upstream monotonic `event_id` cursor, not
by timestamps. That is deliberate: it is deterministic, clock-independent, and
survives a restart. It also sidesteps the aware/naive datetime comparison bug
class entirely.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, field_serializer

from .vote import OptionLetter


class RoundStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    REVEALED = "revealed"


class FirstVoter(BaseModel):
    """Whoever answered a given option first, correct or incorrect."""

    unique_id: str
    nickname: str
    avatar_path: str | None
    event_id: int
    ts_epoch_us: int


class RoundTally(BaseModel):
    counts: dict[OptionLetter, int]
    unique_voters: int
    # First responder per option. Purely visual, grants no points.
    first_by_option: dict[OptionLetter, FirstVoter]


class Round(BaseModel):
    id: int
    show_id: int
    question_id: int
    status: RoundStatus
    opened_at: datetime
    closed_at: datetime | None = None
    opened_cursor: int | None = None
    closed_cursor: int | None = None
    question_time_limit_s: int = 10
    correct_option: OptionLetter | None = None
    tally: RoundTally | None = None

    @field_serializer("opened_at", "closed_at")
    def _ser_dt(self, v: datetime | None) -> str | None:
        if v is None:
            return None
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        else:
            v = v.astimezone(timezone.utc)
        return v.isoformat().replace("+00:00", "Z")


def first_by_option(answers) -> dict[OptionLetter, FirstVoter]:
    """Earliest answer per option, by event_id, tiebreak ts_epoch_us.

    Deliberately does NOT filter on correctness: the requirement is the first
    person to answer each option, whoever that was.
    """
    best: dict[OptionLetter, FirstVoter] = {}
    for a in answers:
        cur = best.get(a.option)
        if cur is None or (a.event_id, a.ts_epoch_us) < (cur.event_id, cur.ts_epoch_us):
            best[a.option] = FirstVoter(
                unique_id=a.unique_id,
                nickname=a.nickname,
                avatar_path=a.avatar_path,
                event_id=a.event_id,
                ts_epoch_us=a.ts_epoch_us,
            )
    return best
