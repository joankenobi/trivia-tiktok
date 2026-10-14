"""Round lifecycle: open, ingest votes, freeze, reveal.

Rules encoded here, all of them traceable to a measured property of real traffic:

- Vote acceptance is bounded by the upstream monotonic `event_id`, never by a
  clock. Window is `(opened_cursor, ...]`.
- One vote per (round, user). The UNIQUE constraint backs the service-level
  check so a race cannot produce a second ballot.
- `first_by_option` is the earliest answer per option REGARDLESS of correctness.
  It is a visual badge only and never affects points.
- Points are awarded only for correct answers, with a linear 0-50% speed bonus.
- A frozen tally never changes, even as new events keep arriving.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..domain.question import DIFF_BASE
from ..domain.question import score as compute_score
from ..domain.session import RoundStatus
from ..domain.vote import OptionLetter, parse_vote
from ..infra.avatar_cache import cache_avatar
from ..infra.models import Answer, Question, Round, Show, User


class RoundNotFound(Exception):
    pass


class RoundNotOpen(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_naive_utc(dt: datetime) -> datetime:
    """SQLite has no tz-aware storage; store naive UTC consistently."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


async def get_or_create_user(
    session: AsyncSession,
    unique_id: str,
    nickname: str,
    avatar_url: str | None,
) -> User:
    user = (
        await session.execute(select(User).where(User.unique_id == unique_id))
    ).scalar_one_or_none()

    if user is None:
        user = User(unique_id=unique_id, nickname=nickname or unique_id)
        session.add(user)
        await session.flush()
    else:
        if nickname and nickname != user.nickname:
            user.nickname = nickname

    # Cache outside the branch: a brand-new user also needs its first avatar.
    # `cache_avatar` short-circuits on an existing file and never raises, so a
    # dead CDN URL cannot stall vote ingestion.
    if avatar_url and not user.avatar_path:
        path = await cache_avatar(unique_id, avatar_url)
        if path:
            user.avatar_path = path

    return user


async def seed_questions(session: AsyncSession, questions) -> list[Question]:
    """Upsert by primary key so a restart does not duplicate or reorder the bank."""
    for q in questions:
        await session.execute(
            sqlite_insert(Question)
            .values(
                id=q.id,
                text=q.text,
                category=q.category,
                difficulty=q.difficulty,
                options=list(q.options),
                correct=q.correct.value,
                explanation=q.explanation,
            )
            .on_conflict_do_update(
                index_elements=[Question.id],
                set_={
                    "text": q.text,
                    "category": q.category,
                    "difficulty": q.difficulty,
                    "options": list(q.options),
                    "correct": q.correct.value,
                    "explanation": q.explanation,
                },
            )
        )
    await session.commit()
    return list(
        (await session.execute(select(Question).order_by(Question.id))).scalars().all()
    )


async def create_show(
    session: AsyncSession,
    room_id: str,
    question_ids: list[int],
    question_time_limit_s: int,
) -> Show:
    show = Show(
        room_id=str(room_id),
        question_count=len(question_ids),
        question_time_limit_s=question_time_limit_s,
        status="active",
    )
    show.rounds = [
        Round(
            show_id=show.id,
            question_id=qid,
            position=position,
            status="pending",
            question_time_limit_s=question_time_limit_s,
        )
        for position, qid in enumerate(question_ids)
    ]
    # One add: the collection is populated in memory, so the caller can serialize
    # show.rounds straight after commit without a lazy load (which would emit SQL
    # from sync serializer code and raise MissingGreenlet on the async engine).
    session.add(show)
    await session.commit()
    return show


async def open_round(
    session: AsyncSession, round_id: int, opened_cursor: int
) -> Round:
    """Open a round above `opened_cursor`.

    The cursor MUST come from the poller's observed stream position, not from
    local vote rows: chat between rounds is mostly non-votes, so a cursor
    derived from `Answer` would leave a hole and let round N+1 accept comments
    that actually belong to round N's window.
    """
    rnd = await _load_round(session, round_id)
    rnd.status = RoundStatus.OPEN.value
    rnd.opened_at = _to_naive_utc(_utcnow())
    rnd.opened_cursor = int(opened_cursor)
    rnd.closed_at = None
    rnd.closed_cursor = None
    rnd.tally_json = None
    rnd.correct_option = None
    rnd.first_answer_at = None
    await session.commit()
    return rnd


async def ingest_event(
    session: AsyncSession,
    rnd: Round,
    comment,
) -> Answer | None:
    """Turn one live comment into a ballot, or ignore it.

    Returns the created Answer, or None if the comment was not a valid vote,
    was outside the window, or the user had already voted this round.
    """
    if rnd.status != RoundStatus.OPEN.value:
        return None
    if rnd.opened_cursor is not None and comment.event_id <= rnd.opened_cursor:
        return None

    option = parse_vote(comment.message)
    if option is None:
        return None
    if not comment.unique_id:
        return None

    user = await get_or_create_user(
        session, comment.unique_id, comment.nickname, comment.avatar_url
    )

    duplicate = (
        await session.execute(
            select(Answer.id).where(Answer.round_id == rnd.id, Answer.user_id == user.id)
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        return None

    if rnd.first_answer_at is None:
        rnd.first_answer_at = _to_naive_utc(comment.ts_utc)

    base = _aware(rnd.first_answer_at)
    elapsed_ms = int(
        max(0.0, (comment.ts_utc - base).total_seconds() * 1000)
    )
    limit_ms = rnd.question_time_limit_s * 1000
    elapsed_ms = min(elapsed_ms, limit_ms)

    answer = Answer(
        round_id=rnd.id,
        question_id=rnd.question_id,
        user_id=user.id,
        event_id=comment.event_id,
        option=option.value,
        correct=False,  # resolved at close, when the question is known
        points=0,
        elapsed_ms=elapsed_ms,
        ts_epoch_us=int(comment.ts_utc.timestamp() * 1_000_000),
        answered_at=_to_naive_utc(comment.ts_utc),
    )
    session.add(answer)
    await session.flush()
    return answer


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def build_tally(session: AsyncSession, rnd: Round) -> dict:
    """Compute counts, unique voters and first responder per option.

    Each first responder also carries what its answer was worth (`points`) and
    what the viewer has earned across the show (`total_points`), because the
    reveal frame announces that number and the frozen snapshot is the only place
    it survives. Both are legitimately 0 while the round is open: scoring runs at
    close, so a live tally reports nothing rather than guessing.
    """
    answers = list(
        (
            await session.execute(
                select(Answer, User)
                .join(User, User.id == Answer.user_id)
                .where(Answer.round_id == rnd.id)
                .order_by(Answer.event_id, Answer.ts_epoch_us)
            )
        ).all()
    )

    counts = {letter: 0 for letter in OptionLetter}
    first_by_option: dict[str, dict] = {}
    unique_ids: set[int] = set()
    # Option letter -> internal user id. Kept out of the payload because the
    # public contract speaks in TikTok `unique_id`, not in our primary keys.
    first_user_ids: dict[str, int] = {}

    for answer, user in answers:
        letter = OptionLetter(answer.option)
        counts[letter] += 1
        unique_ids.add(answer.user_id)
        if letter.value not in first_by_option:
            first_by_option[letter.value] = {
                "unique_id": user.unique_id,
                "nickname": user.nickname,
                "avatar_path": user.avatar_path,
                "event_id": answer.event_id,
                "ts_epoch_us": answer.ts_epoch_us,
                # Scored by `close_round` just above this call, and that write is
                # still pending here: the query above autoflushed it, and the
                # identity map keeps the new values either way.
                "points": answer.points or 0,
                "total_points": 0,  # filled in below, once every option is known
            }
            first_user_ids[letter.value] = answer.user_id

    totals = await _show_totals_by_user_id(
        session, rnd.show_id, set(first_user_ids.values())
    )
    for letter, user_id in first_user_ids.items():
        first_by_option[letter]["total_points"] = totals.get(user_id, 0)

    return {
        "counts": {k.value: v for k, v in counts.items()},
        "unique_voters": len(unique_ids),
        "first_by_option": first_by_option,
    }


async def _show_totals_by_user_id(
    session: AsyncSession, show_id: int, user_ids: set[int]
) -> dict[int, int]:
    """Running score per viewer across one show, restricted to `user_ids`.

    One grouped query, not one per viewer: this runs on the hottest read path in
    the app (every client polls the tally every second) and there is no reason
    for its cost to grow with the size of the show when only the four first
    responders need a total. Joins `Round` because the answer's own round is what
    decides which show it belongs to.
    """
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(Answer.user_id, func.sum(Answer.points).label("points"))
            .join(Round, Round.id == Answer.round_id)
            .where(Round.show_id == show_id, Answer.user_id.in_(user_ids))
            .group_by(Answer.user_id)
        )
    ).all()
    return {row.user_id: int(row.points or 0) for row in rows}


async def close_round(
    session: AsyncSession, round_id: int, closed_cursor: int | None = None
) -> Round:
    """Freeze the tally and score every answer. Idempotent.

    `correct_option` is written here because scoring needs it, but the API only
    exposes it once the host reveals (see `_common.is_revealed`).
    """
    rnd = await _load_round(session, round_id)
    if rnd.status != RoundStatus.OPEN.value:
        return rnd

    question = await session.get(Question, rnd.question_id)
    if question is None:
        raise RoundNotFound(f"question {rnd.question_id} missing")

    correct = OptionLetter(question.correct)
    base = DIFF_BASE.get(question.difficulty, DIFF_BASE["medium"])
    limit_ms = rnd.question_time_limit_s * 1000

    answers = list(
        (
            await session.execute(select(Answer).where(Answer.round_id == rnd.id))
        ).scalars().all()
    )
    for answer in answers:
        is_correct = answer.option == correct.value
        answer.correct = is_correct
        answer.points = compute_score(is_correct, base, answer.elapsed_ms, limit_ms)

    rnd.status = RoundStatus.CLOSED.value
    rnd.closed_at = _to_naive_utc(_utcnow())
    # Freeze above every event that was actually observed, not just the voted
    # ones; otherwise late chat could still land inside this round's window.
    rnd.closed_cursor = max(
        closed_cursor if closed_cursor is not None else 0,
        max((a.event_id for a in answers), default=rnd.opened_cursor or 0),
    )
    rnd.correct_option = correct.value
    rnd.tally_json = await build_tally(session, rnd)
    await session.commit()
    return rnd


async def reveal_round(session: AsyncSession, round_id: int) -> Round:
    rnd = await _load_round(session, round_id)
    if rnd.status == RoundStatus.OPEN.value:
        await close_round(session, round_id)
        rnd = await _load_round(session, round_id)
    rnd.status = RoundStatus.REVEALED.value
    await session.commit()
    return rnd


async def ranking(session: AsyncSession, show_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(
                User.unique_id,
                User.nickname,
                User.avatar_path,
                func.sum(Answer.points).label("points"),
                func.sum(cast(Answer.correct, Integer)).label("correct_count"),
            )
            .join(Answer, Answer.user_id == User.id)
            .join(Round, Round.id == Answer.round_id)
            .where(Round.show_id == show_id)
            .group_by(User.id)
            .order_by(func.sum(Answer.points).desc(), User.nickname)
        )
    ).all()
    return [
        {
            "unique_id": r.unique_id,
            "nickname": r.nickname,
            "avatar_path": r.avatar_path,
            "points": int(r.points or 0),
            "correct_count": int(r.correct_count or 0),
        }
        for r in rows
    ]


async def _load_round(session: AsyncSession, round_id: int) -> Round:
    rnd = await session.get(Round, round_id)
    if rnd is None:
        raise RoundNotFound(str(round_id))
    return rnd
