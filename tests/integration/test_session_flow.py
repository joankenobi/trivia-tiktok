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


class TestFirstResponderScore:
    """What the first responder's answer was actually worth.

    The reveal frame announces the first CORRECT responder with the points that
    answer earned, so the tally has to carry two different numbers: the award for
    this question, and the viewer's running total for the show.
    """

    async def test_points_are_zero_while_the_round_is_open(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "A", uid="a1"))
        tally = await svc.build_tally(session, round_)
        # Answers are scored at close, so a live tally has nothing to report.
        # Reporting a guess here would put a number on screen nobody earned.
        assert tally["first_by_option"]["A"]["points"] == 0
        assert tally["first_by_option"]["A"]["total_points"] == 0

    async def test_points_carry_the_award_after_close(self, session, round_):
        opened = round_.opened_at.replace(tzinfo=timezone.utc)
        await svc.ingest_event(
            session, round_, comment_at(1001, opened + timedelta(seconds=5), "A", uid="a1")
        )
        rnd = await svc.close_round(session, round_.id)
        award = (await answers_for(session, rnd))[0].points

        first = rnd.tally_json["first_by_option"]["A"]
        assert first["points"] == award
        assert first["total_points"] == award

    async def test_first_responder_of_a_wrong_option_is_worth_nothing(self, session, round_):
        await svc.ingest_event(session, round_, comment(1001, "B", uid="b1"))
        rnd = await svc.close_round(session, round_.id)
        first = rnd.tally_json["first_by_option"]["B"]
        assert first["points"] == 0
        assert first["total_points"] == 0

    async def test_total_points_span_previous_rounds_of_the_show(self, session, round_):
        opened = round_.opened_at.replace(tzinfo=timezone.utc)
        await svc.ingest_event(session, round_, comment_at(1001, opened, "A", uid="winner"))
        first_round = await svc.close_round(session, round_.id)
        first_award = (await answers_for(session, first_round))[0].points

        show = (await session.execute(select(Show))).scalar_one()
        r2 = Round(show_id=show.id, question_id=1, position=1, status="pending",
                   question_time_limit_s=10)
        session.add(r2)
        await session.commit()
        r2 = await svc.open_round(session, r2.id, opened_cursor=2000)
        await svc.ingest_event(session, r2, comment_at(2001, r2.opened_at.replace(tzinfo=timezone.utc), "A", uid="winner"))
        second_round = await svc.close_round(session, r2.id)
        second_award = (await answers_for(session, second_round))[0].points

        first = second_round.tally_json["first_by_option"]["A"]
        assert first["points"] == second_award
        # The running total is what the podium will eventually show this viewer.
        assert first["total_points"] == first_award + second_award

    async def test_total_points_are_one_viewer_not_the_whole_round(self, session, round_):
        opened = round_.opened_at.replace(tzinfo=timezone.utc)
        await svc.ingest_event(session, round_, comment_at(1001, opened, "A", uid="fast"))
        await svc.ingest_event(
            session, round_, comment_at(1002, opened + timedelta(seconds=6), "A", uid="slow")
        )
        rnd = await svc.close_round(session, round_.id)
        fast, slow = sorted(await answers_for(session, rnd), key=lambda a: a.event_id)

        first = rnd.tally_json["first_by_option"]["A"]
        assert first["unique_id"] == "fast"
        assert first["total_points"] == fast.points
        assert fast.points > slow.points  # the two awards differ, so the sum would show

    async def test_frozen_tally_validates_as_the_polled_contract(self, session, round_):
        """The routes rebuild `TallyOut` from the frozen snapshot on every poll.

        A field present in the dict but missing from the schema is dropped by
        pydantic, so the overlay would read `undefined` and print nothing. This
        is the guard that keeps the two halves of the contract in step.
        """
        from app.api.schemas import TallyOut

        await svc.ingest_event(session, round_, comment(1001, "A", uid="a1"))
        rnd = await svc.close_round(session, round_.id)
        out = TallyOut(**rnd.tally_json)

        first = out.first_by_option["A"]
        assert first.points == rnd.tally_json["first_by_option"]["A"]["points"]
        assert first.total_points == rnd.tally_json["first_by_option"]["A"]["total_points"]


class TestScoring:
    async def test_correct_scores_base_plus_speed_bonus(self, session, round_):
        opened = round_.opened_at.replace(tzinfo=timezone.utc)
        # The clock starts on the first answer, so a "5s slow" answer requires a
        # first buzzer to open the clock at `opened`. Both events sit above the
        # round's opened_cursor (1000).
        await svc.ingest_event(
            session, round_, comment_at(1001, opened, "B", uid="buzzer")
        )
        await svc.ingest_event(
            session, round_, comment_at(1002, opened + timedelta(seconds=5), "A")
        )
        rnd = await svc.close_round(session, round_.id)
        answer = next(a for a in await answers_for(session, rnd) if a.event_id == 1002)
        base = DIFF_BASE["medium"]
        assert answer.points == base + int(base * 0.5 * 0.5)

    async def test_first_answer_is_instant_full_bonus_later_answer_less(
        self, session, round_
    ):
        """The first answer opens the clock at elapsed 0 and earns the full bonus.

        A later answer is measured against that same first answer, so it earns a
        smaller bonus. This is the whole point of re-anchoring the clock: a late
        first answer must not zero out everyone else's speed bonus.
        """
        opened = round_.opened_at.replace(tzinfo=timezone.utc)
        await svc.ingest_event(
            session, round_, comment_at(1001, opened, "A", uid="first")
        )
        await svc.ingest_event(
            session, round_, comment_at(1002, opened + timedelta(seconds=5), "A", uid="late")
        )
        rnd = await svc.close_round(session, round_.id)
        first, late = sorted(await answers_for(session, rnd), key=lambda a: a.event_id)
        base = DIFF_BASE["medium"]
        assert first.elapsed_ms == 0
        assert first.points == base + int(base * 0.5)
        assert late.elapsed_ms > 0
        assert late.points < first.points

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


class TestSchemaMigration:
    """`init_models` upgrades an existing DB; there is no migration framework."""

    def test_adds_first_answer_at_to_a_legacy_rounds_table(self, tmp_path):
        from sqlalchemy import create_engine

        from app.db import _ensure_first_answer_at

        engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE rounds (id INTEGER PRIMARY KEY, opened_at DATETIME)"
            )
            conn.exec_driver_sql(
                "INSERT INTO rounds (id, opened_at) VALUES (1, '2020-01-01')"
            )
            _ensure_first_answer_at(conn)

        with engine.begin() as conn:
            cols = [
                r[1]
                for r in conn.exec_driver_sql("PRAGMA table_info(rounds)").fetchall()
            ]
            kept = conn.exec_driver_sql("SELECT id FROM rounds").fetchall()
        engine.dispose()

        assert "first_answer_at" in cols
        assert kept == [(1,)]  # no data loss

    def test_is_idempotent_when_the_column_already_exists(self, tmp_path):
        from sqlalchemy import create_engine

        from app.db import _ensure_first_answer_at

        engine = create_engine(f"sqlite:///{tmp_path / 'twice.db'}")
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE rounds (id INTEGER PRIMARY KEY, first_answer_at DATETIME)"
            )
            # A second run must not raise "duplicate column name".
            _ensure_first_answer_at(conn)
            _ensure_first_answer_at(conn)
        engine.dispose()


class TestSeed:
    async def test_repeated_seed_is_idempotent(self, session):
        from app.infra.seed import default_questions

        qs = default_questions()
        await svc.seed_questions(session, qs)
        first = (await session.execute(select(func.count(Question.id)))).scalar_one()
        await svc.seed_questions(session, qs)
        second = (await session.execute(select(func.count(Question.id)))).scalar_one()
        assert first == second == len(qs)
