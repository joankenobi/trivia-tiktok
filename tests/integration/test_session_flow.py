"""Integration tests for the round lifecycle against a real SQLite DB.

These cover what the domain tests cannot: cursor windows, one-vote-per-user at
the DB layer, frozen tallies, the reveal gate, and scoring.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.question import DIFF_BASE
from app.domain.session import RoundStatus
from app.infra.models import Answer, Base, Question, Round, Show, User
from app.infra.tiktok_live import LiveComment
from app.services import session_service as svc


def comment(event_id: int, message: str, uid: str = "u1", nickname: str = "User1") -> LiveComment:
    return LiveComment(
        event_id=event_id,
        ts_utc=datetime.now(timezone.utc),
        unique_id=uid,
        nickname=nickname,
        avatar_url=None,
        message=message,
    )


def comment_at(event_id: int, ts: datetime, message: str, uid: str = "u1") -> LiveComment:
    return LiveComment(
        event_id=event_id, ts_utc=ts, unique_id=uid, nickname="User1", avatar_url=None,
        message=message,
    )


@pytest_asyncio.fixture
async def session():
    """Real async engine on a temp file: in-memory SQLite spans connections badly."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s

    await engine.dispose()
    os.unlink(path)


@pytest_asyncio.fixture
async def round_(session):
    """One open round whose correct answer is A."""
    session.add(
        Question(id=1, text="Test?", difficulty="medium",
                 options=["a", "b", "c", "d"], correct="A")
    )
    show = Show(room_id="1", question_count=1, question_time_limit_s=10)
    session.add(show)
    await session.flush()
    rnd = Round(show_id=show.id, question_id=1, position=0, status="pending",
                question_time_limit_s=10)
    session.add(rnd)
    await session.commit()
    return await svc.open_round(session, rnd.id, opened_cursor=1000)


async def answers_for(session, rnd) -> list[Answer]:
    return list(
        (await session.execute(select(Answer).where(Answer.round_id == rnd.id)))
        .scalars().all()
    )


class TestCursorWindow:
    async def test_rejects_events_at_or_below_opened_cursor(self, session, round_):
        assert await svc.ingest_event(session, round_, comment(1000, "A")) is None
        assert await svc.ingest_event(session, round_, comment(999, "A")) is None
        assert await svc.ingest_event(session, round_, comment(1001, "A")) is not None

    async def test_ignores_non_votes(self, session, round_):
        for mid, text in [(1001, "Hola"), (1002, "ABCD"), (1003, "A B"), (1004, "")]:
            assert await svc.ingest_event(session, round_, comment(mid, text)) is None

    async def test_ignores_comment_without_unique_id(self, session, round_):
        c = comment_at(1001, round_.opened_at.replace(tzinfo=timezone.utc), "A")
        c = LiveComment(c.event_id, c.ts_utc, "", c.nickname, None, "A")
        assert await svc.ingest_event(session, round_, c) is None

    async def test_ingest_is_noop_when_round_not_open(self, session, round_):
        rnd = await svc.close_round(session, round_.id)
        assert await svc.ingest_event(session, rnd, comment(1001, "A")) is None


class TestOneVotePerUser:
    async def test_second_vote_from_same_user_rejected(self, session, round_):
        assert await svc.ingest_event(session, round_, comment(1001, "A", uid="dup")) is not None
        assert await svc.ingest_event(session, round_, comment(1002, "B", uid="dup")) is None

    async def test_reingested_comment_under_new_event_id(self, session, round_):
        """Upstream re-ingests one logical comment under a new event_id."""
        await svc.ingest_event(session, round_, comment(1001, "C", uid="dupe"))
        await svc.ingest_event(session, round_, comment(1005, "C", uid="dupe"))
        assert len(await answers_for(session, round_)) == 1

    async def test_distinct_users_both_counted(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "A", uid="u1"))
        await svc.ingest_event(session, round_, comment(1002, "A", uid="u2"))
        tally = await svc.build_tally(session, round_)
        assert tally["counts"]["A"] == 2
        assert tally["unique_voters"] == 2


class TestFirstByOption:
    async def test_first_per_option_regardless_of_correctness(self, session, round_):
        # A is correct; B is wrong but must still carry a first responder.
        await svc.ingest_event(session, round_, comment(1001, "B", uid="b1"))
        await svc.ingest_event(session, round_, comment(1002, "B", uid="b2"))
        await svc.ingest_event(session, round_, comment(1003, "A", uid="a1"))

        tally = await svc.build_tally(session, round_)
        assert tally["first_by_option"]["B"]["unique_id"] == "b1"
        assert tally["first_by_option"]["A"]["unique_id"] == "a1"

    async def test_first_voter_of_wrong_option_scores_nothing(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "B", uid="b1"))
        rnd = await svc.close_round(session, round_.id)
        answer = (await answers_for(session, rnd))[0]
        assert answer.correct is False
        assert answer.points == 0


class TestScoring:
    async def test_correct_scores_base_plus_speed_bonus(self, session, round_):
        opened = round_.opened_at.replace(tzinfo=timezone.utc)
        await svc.ingest_event(session, round_, comment_at(1001, opened + timedelta(seconds=5), "A"))
        rnd = await svc.close_round(session, round_.id)
        answer = (await answers_for(session, rnd))[0]
        base = DIFF_BASE["medium"]
        assert answer.points == base + int(base * 0.5 * 0.5)

    async def test_instant_correct_gets_full_bonus(self, session, round_):
        opened = round_.opened_at.replace(tzinfo=timezone.utc)
        await svc.ingest_event(session, round_, comment_at(1001, opened, "A"))
        rnd = await svc.close_round(session, round_.id)
        answer = (await answers_for(session, rnd))[0]
        base = DIFF_BASE["medium"]
        assert answer.points == base + int(base * 0.5)

    async def test_wrong_answer_scores_zero(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "D"))
        rnd = await svc.close_round(session, round_.id)
        assert (await answers_for(session, rnd))[0].points == 0

    async def test_negative_elapsed_is_clamped(self, session, round_):
        opened = round_.opened_at.replace(tzinfo=timezone.utc)
        await svc.ingest_event(session, round_, comment_at(1001, opened - timedelta(seconds=30), "A"))
        rnd = await svc.close_round(session, round_.id)
        answer = (await answers_for(session, rnd))[0]
        assert answer.elapsed_ms == 0
        assert answer.points == DIFF_BASE["medium"] + int(DIFF_BASE["medium"] * 0.5)


class TestFrozenTally:
    async def test_tally_survives_late_events(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "A", uid="u1"))
        rnd = await svc.close_round(session, round_.id)
        frozen = rnd.tally_json["counts"]

        await svc.ingest_event(session, rnd, comment(9999, "B", uid="late"))

        # The stored snapshot is what the SPA reads, so it must be untouched.
        assert rnd.tally_json["counts"] == frozen
        # And no stray row leaked in from the late event.
        assert len(await answers_for(session, rnd)) == 1

    async def test_close_is_idempotent(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "A"))
        first = await svc.close_round(session, round_.id)
        second = await svc.close_round(session, round_.id)
        assert first.tally_json["counts"] == second.tally_json["counts"]
        assert first.closed_cursor == second.closed_cursor

    async def test_closed_cursor_covers_observed_non_votes(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "A"))
        rnd = await svc.close_round(session, round_.id, closed_cursor=5000)
        assert rnd.closed_cursor == 5000


class TestReveal:
    async def test_closed_to_revealed(self, session, round_):
        await svc.close_round(session, round_.id)
        assert (await svc.reveal_round(session, round_.id)).status == RoundStatus.REVEALED.value

    async def test_reveal_from_open_closes_first(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "A"))
        rnd = await svc.reveal_round(session, round_.id)
        assert rnd.status == RoundStatus.REVEALED.value
        assert rnd.tally_json is not None

    async def test_only_revealed_exposes_answer(self):
        from app.api.routes._common import is_revealed

        assert is_revealed(RoundStatus.REVEALED.value) is True
        assert is_revealed(RoundStatus.CLOSED.value) is False
        assert is_revealed(RoundStatus.OPEN.value) is False


class TestRanking:
    async def test_aggregates_across_rounds(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "A", uid="winner"))
        await svc.close_round(session, round_.id)

        show = (await session.execute(select(Show))).scalar_one()
        r2 = Round(show_id=show.id, question_id=1, position=1, status="pending",
                   question_time_limit_s=10)
        session.add(r2)
        await session.commit()
        r2 = await svc.open_round(session, r2.id, opened_cursor=2000)
        await svc.ingest_event(session, r2, comment(2001, "A", uid="winner"))
        await svc.close_round(session, r2.id)

        rows = await svc.ranking(session, show.id)
        assert len(rows) == 1
        assert rows[0]["unique_id"] == "winner"
        assert rows[0]["correct_count"] == 2
        assert rows[0]["points"] > 0

    async def test_wrong_only_player_ranks_with_zero(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "C", uid="wrong"))
        show = (await session.execute(select(Show))).scalar_one()
        await svc.close_round(session, round_.id)
        rows = await svc.ranking(session, show.id)
        assert rows[0]["points"] == 0
        assert rows[0]["correct_count"] == 0

    async def test_ranking_ordered_by_points_desc(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "A", uid="top"))
        await svc.ingest_event(session, round_, comment(1002, "B", uid="bottom"))
        show = (await session.execute(select(Show))).scalar_one()
        await svc.close_round(session, round_.id)
        rows = await svc.ranking(session, show.id)
        assert [r["unique_id"] for r in rows] == ["top", "bottom"]


class TestUsers:
    async def test_nickname_updates_on_later_comment(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "A", uid="u9", nickname="Old"))
        await svc.ingest_event(session, round_, comment(1002, "B", uid="u9", nickname="New"))
        user = (await session.execute(
            select(User).where(User.unique_id == "u9")
        )).scalar_one()
        assert user.nickname == "New"

    async def test_empty_nickname_falls_back_to_unique_id(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "A", uid="anon", nickname=""))
        user = (await session.execute(select(User))).scalar_one()
        assert user.nickname == "anon"


class TestSeed:
    async def test_repeated_seed_is_idempotent(self, session):
        from app.infra.seed import default_questions

        qs = default_questions()
        await svc.seed_questions(session, qs)
        first = (await session.execute(select(func.count(Question.id)))).scalar_one()
        await svc.seed_questions(session, qs)
        second = (await session.execute(select(func.count(Question.id)))).scalar_one()
        assert first == second == len(qs)
