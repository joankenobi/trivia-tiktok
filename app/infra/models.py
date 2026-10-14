"""SQLAlchemy models.

The UNIQUE constraint on (round_id, user_id) is what neutralizes the spam
    problem: one measured user produced 46.5% of a real live chat, and without
    it that account decides every question by repeating a letter.
    """

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def _utcnow() -> datetime:
    """Naive UTC: SQLite has no tz-aware storage and comparisons must match.

    The service stores naive UTC everywhere and re-attaches tzinfo at the edges,
    so mixing the two is what triggers aware/naive comparison errors.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unique_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(255), default="")
    # Local served path (e.g. "/avatars/foo.webp"). NEVER the signed CDN URL.
    avatar_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    answers: Mapped[list[Answer]] = relationship(back_populates="user")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")
    options: Mapped[list] = mapped_column(JSON)  # exactly 4 strings, positional A-D
    correct: Mapped[str] = mapped_column(String(1))  # "A" | "B" | "C" | "D"
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def base_points(self) -> int:
        from ..domain.question import DIFF_BASE

        return DIFF_BASE.get(self.difficulty, DIFF_BASE["medium"])


class Show(Base):
    __tablename__ = "shows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[str] = mapped_column(String(64), index=True)
    question_count: Mapped[int] = mapped_column(Integer, default=10)
    question_time_limit_s: Mapped[int] = mapped_column(Integer, default=20)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    rounds: Mapped[list[Round]] = relationship(
        back_populates="show",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Round(Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_answer_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    # Window boundaries keyed on the upstream monotonic event_id, not on time.
    opened_cursor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    closed_cursor: Mapped[int | None] = mapped_column(Integer, nullable=True)

    question_time_limit_s: Mapped[int] = mapped_column(Integer, default=20)
    correct_option: Mapped[str | None] = mapped_column(String(1), nullable=True)
    tally_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    show: Mapped[Show] = relationship(back_populates="rounds")
    # selectin, not the default lazy: the API serializes a Round together with its
    # question. A lazy load there would emit SQL from sync serializer code and
    # raise MissingGreenlet on the async engine.
    question: Mapped[Question] = relationship(lazy="selectin")
    answers: Mapped[list[Answer]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (
        # round_id + unique_id is the spam guard, and it also makes ingest
        # idempotent under retry.
        UniqueConstraint("round_id", "user_id", name="uq_answer_round_user"),
        Index("ix_answers_round_option", "round_id", "option"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # Upstream can re-ingest the same logical comment under a new event_id
    # (observed 21595 / 21602), so dedupe on the user, not the event.
    event_id: Mapped[int] = mapped_column(Integer, index=True)
    option: Mapped[str] = mapped_column(String(1))
    correct: Mapped[bool] = mapped_column(Boolean, default=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    ts_epoch_us: Mapped[int] = mapped_column(Integer, default=0)
    answered_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    round: Mapped[Round] = relationship(back_populates="answers")
    user: Mapped[User] = relationship(back_populates="answers")
