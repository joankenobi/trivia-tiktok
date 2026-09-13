"""Normalized event contract (pydantic v2).

These models are what the capturer emits and — in Phase 2 — what the
FastAPI receiver will accept via POST /api/v1/lives/{room_id}/batches.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserInfo(BaseModel):
    unique_id: str | None = None
    nickname: str | None = None
    profile_picture_url: str | None = None  # TikTok gives a URL, not bytes


class EventEnvelope(BaseModel):
    type: str  # "comment" | "gift" | "like" | "follow"
    timestamp: str  # ISO 8601
    user: UserInfo
    # Per-event payload. Lives on the BASE class on purpose: fields declared on
    # subclasses are dropped when pydantic serializes a list typed as the base
    # (e.g. Batch.events: list[EventEnvelope]), which silently removed `data`
    # from every batch and made the receiver 422 on the "data required" rule.
    data: dict = Field(default_factory=dict)


class CommentEvent(EventEnvelope):
    """data: {"message": "..."}"""


class GiftEvent(EventEnvelope):
    """data: {"gift_name", "diamonds", "repeat_count"}"""


class LikeEvent(EventEnvelope):
    """data: {"like_count", "delta"} — delta computed client-side"""


class FollowEvent(EventEnvelope):
    """data: {} (contract: maxProperties 0)"""


class Batch(BaseModel):
    version: int = 1
    room_id: str | None = None
    stream_id: str | None = None
    started_at: str
    ended_at: str
    events: list[EventEnvelope] = Field(default_factory=list)