"""Pydantic v2 request/response models.

These mirror ``contract/batch.schema.json`` (single source of truth). The JSON
Schema expresses per-type ``data`` requirements via ``if/then``; here that is
modelled with a discriminated union on ``type`` and one data submodel per event
type, producing the same accept/reject behavior:
  - unknown ``type``                       -> 422
  - comment missing ``data.message``       -> 422
  - gift missing ``gift_name``/``repeat_count``/``diamonds`` -> 422
  - like missing ``like_count``/``delta``  -> 422
  - follow with non-empty ``data``         -> 422 (schema has maxProperties: 0)
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal["comment", "gift", "like", "follow"]


class UserInfo(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    unique_id: str | None = None
    nickname: str | None = None
    profile_picture_url: str | None = None


class CommentData(BaseModel):
    message: str


class GiftData(BaseModel):
    gift_name: str
    repeat_count: int
    diamonds: int | None


class LikeData(BaseModel):
    like_count: int
    delta: int


class FollowData(BaseModel):
    # contract: data {type: object, maxProperties: 0}
    model_config = ConfigDict(extra="forbid")


class BatchEvent(BaseModel):
    timestamp: datetime
    user: UserInfo


class CommentEvent(BatchEvent):
    type: Literal["comment"] = "comment"
    data: CommentData


class GiftEvent(BatchEvent):
    type: Literal["gift"] = "gift"
    data: GiftData


class LikeEvent(BatchEvent):
    type: Literal["like"] = "like"
    data: LikeData


class FollowEvent(BatchEvent):
    type: Literal["follow"] = "follow"
    data: FollowData = Field(default_factory=FollowData)


Event = Annotated[
    Union[CommentEvent, GiftEvent, LikeEvent, FollowEvent],
    Field(discriminator="type"),
]

# Upper bound on the number of events per batch (schema-level guard, 422 when
# exceeded). Kept as a module constant rather than importing app settings so
# the schema layer stays independent of app configuration.
MAX_BATCH_EVENTS = 500


class Batch(BaseModel):
    version: Literal[1] = 1
    room_id: str | None = None
    host_username: str | None = None
    stream_id: str | None = None
    started_at: datetime
    ended_at: datetime
    events: list[Event] = Field(max_length=MAX_BATCH_EVENTS)


class BatchAck(BaseModel):
    received: int
    batch_id: int


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"