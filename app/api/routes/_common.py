"""Shared route helpers: ORM -> schema conversion."""

from __future__ import annotations

from datetime import datetime, timezone

from ...config import settings
from ...domain.session import RoundStatus
from ...infra.models import Question, Round
from ..schemas import QuestionOut, RoundOut, TallyOut


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _epoch_ms(dt: datetime | None) -> int | None:
    """Epoch milliseconds for a naive-UTC timestamp.

    The service stores naive UTC everywhere, so the tzinfo is re-attached here
    rather than at every call site.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def deadline_epoch_ms(rnd: Round) -> int | None:
    """When the round leaves its current status, or None if it never will.

    - `open` runs until `opened_at + question_time_limit_s`, which is exactly the
      window the poller accepts votes for.
    - `revealed` runs until `closed_at + reveal_display_s`, how long the answer
      stays on screen before autoplay opens the next question.

    `closed` deliberately returns None: in autoplay it is a sub-tick state the
    clients never see, and by hand it means the host is deciding when to move on.
    """
    if rnd.status == RoundStatus.OPEN.value and rnd.opened_at is not None:
        return _epoch_ms(rnd.opened_at) + rnd.question_time_limit_s * 1000
    if rnd.status == RoundStatus.REVEALED.value and rnd.closed_at is not None:
        return _epoch_ms(rnd.closed_at) + int(settings.reveal_display_s * 1000)
    return None


def question_out(q: Question, reveal: bool) -> QuestionOut:
    return QuestionOut(
        id=q.id,
        text=q.text,
        category=q.category,
        difficulty=q.difficulty,
        options=list(q.options),
        correct=q.correct if reveal else None,
        explanation=q.explanation if reveal else None,
    )


def round_out(rnd: Round, reveal: bool) -> RoundOut:
    now = _utcnow()
    return RoundOut(
        id=rnd.id,
        show_id=rnd.show_id,
        question_id=rnd.question_id,
        position=rnd.position,
        status=rnd.status,
        question=question_out(rnd.question, reveal),
        opened_at=rnd.opened_at,
        closed_at=rnd.closed_at,
        question_time_limit_s=rnd.question_time_limit_s,
        correct_option=rnd.correct_option if reveal else None,
        tally=TallyOut(**rnd.tally_json) if rnd.tally_json else None,
        server_time=now,
        deadline_epoch_ms=deadline_epoch_ms(rnd),
        server_epoch_ms=int(now.timestamp() * 1000),
    )


def is_revealed(status: str) -> bool:
    """Only REVEALED exposes the answer.

    `closed` already has `correct_option` persisted (scoring needs it), so
    treating closed as revealed would hand the audience the answer a second
    before the host chooses to show it.
    """
    return status == RoundStatus.REVEALED.value


def visible_round(rounds: list[Round]) -> Round | None:
    """The round the audience is looking at right now, or None if none has played.

    One rule, shared by `/shows/active` and `ShowOut.current_round_id`, because
    two copies of "which round is this" is how they drift apart. It mirrors
    `currentRound()` in `web/js/common.js` for the same reason.

    An open round wins. Otherwise it is the *last* round that already played,
    which is the one holding its answer on screen between questions.

    Scanning backwards is the whole point. A forward scan returns position 0, so
    the moment round 2 closed, a client asking what to render would be pointed at
    question 1 together with its answer -- and it would keep happening for every
    question after the first.
    """
    ordered = sorted(rounds, key=lambda r: r.position)
    for rnd in ordered:
        if rnd.status == RoundStatus.OPEN.value:
            return rnd
    for rnd in reversed(ordered):
        if rnd.status != RoundStatus.PENDING.value:
            return rnd
    return None
