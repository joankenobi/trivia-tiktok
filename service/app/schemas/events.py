"""Pydantic v2 response models for the read API (chat events, active stream)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict

EventKind = Literal["comment", "gift", "like", "follow"]


class EventUserInfo(BaseModel):
    unique_id: str | None = None
    nickname: str | None = None
    avatar: str | None = None


class CommentContent(BaseModel):
    message: str


class GiftContent(BaseModel):
    gift_name: str
    repeat_count: int
    diamonds: int | None = None


class LikeContent(BaseModel):
    like_count: int
    delta: int


class FollowContent(BaseModel):
    # Follow events carry no payload; the contract defines an empty object.
    model_config = ConfigDict(extra="forbid")


EventData = Union[CommentContent, GiftContent, LikeContent, FollowContent]


class ChatEventResponse(BaseModel):
    event_id: int
    type: EventKind
    ts: datetime
    user: EventUserInfo
    data: EventData


class EventListResponse(BaseModel):
    events: list[ChatEventResponse]
    next_cursor: int | None
    has_more: bool


class ActiveStreamResponse(BaseModel):
    room_id: str
    started_at: datetime
    host_username: str | None = None
    active: bool