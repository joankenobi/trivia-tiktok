"""SQLAlchemy ORM entities for the receiver service.

Schema follows the frozen contract in ``contract/batch.schema.json``:
``users``, ``streams`` plus one child table per event type (``comments``,
``gifts``, ``likes``, ``follows``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import utcnow
from app.models.base import Base

_ID = BigInteger().with_variant(Integer, "sqlite")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    unique_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    nickname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_picture_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False
    )

    comments: Mapped[list["Comment"]] = relationship(back_populates="user")
    gifts: Mapped[list["Gift"]] = relationship(back_populates="user")
    likes: Mapped[list["Like"]] = relationship(back_populates="user")
    follows: Mapped[list["Follow"]] = relationship(back_populates="user")


class Stream(Base):
    __tablename__ = "streams"
    __table_args__ = (
        UniqueConstraint("room_id", "stream_id", name="uq_streams_room_stream"),
    )

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    room_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Normalized: when the payload omits stream_id it falls back to room_id,
    # so the unique key never sees NULLs (SQLite/PG differ on NULL uniqueness).
    stream_id: Mapped[str] = mapped_column(String(255), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    batch_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False
    )

    comments: Mapped[list["Comment"]] = relationship(back_populates="stream")
    gifts: Mapped[list["Gift"]] = relationship(back_populates="stream")
    likes: Mapped[list["Like"]] = relationship(back_populates="stream")
    follows: Mapped[list["Follow"]] = relationship(back_populates="stream")


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    stream_id: Mapped[int] = mapped_column(
        ForeignKey("streams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="comments")
    stream: Mapped[Stream] = relationship(back_populates="comments")


class Gift(Base):
    __tablename__ = "gifts"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    stream_id: Mapped[int] = mapped_column(
        ForeignKey("streams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    gift_name: Mapped[str] = mapped_column(String(255), nullable=False)
    repeat_count: Mapped[int] = mapped_column(Integer, nullable=False)
    diamonds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="gifts")
    stream: Mapped[Stream] = relationship(back_populates="gifts")


class Like(Base):
    __tablename__ = "likes"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    stream_id: Mapped[int] = mapped_column(
        ForeignKey("streams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    like_count: Mapped[int] = mapped_column(Integer, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="likes")
    stream: Mapped[Stream] = relationship(back_populates="likes")


class Follow(Base):
    __tablename__ = "follows"

    id: Mapped[int] = mapped_column(_ID, primary_key=True)
    stream_id: Mapped[int] = mapped_column(
        ForeignKey("streams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="follows")
    stream: Mapped[Stream] = relationship(back_populates="follows")