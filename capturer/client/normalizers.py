"""TikTokLive events -> our normalized models.

Targets TikTokLive 7.x (betterproto) but keeps getattr fallbacks so it also
degrades gracefully against older 6.x field names.
"""

from __future__ import annotations

from datetime import datetime, timezone

from TikTokLive.events import CommentEvent as CaCommentEvent
from TikTokLive.events import FollowEvent as CaFollowEvent
from TikTokLive.events import GiftEvent as CaGiftEvent
from TikTokLive.events import LikeEvent as CaLikeEvent

from client.models import (
    CommentEvent,
    EventEnvelope,
    FollowEvent,
    GiftEvent,
    LikeEvent,
    UserInfo,
)


def now_iso() -> str:
    """UTC now as an ISO 8601 string with timezone offset."""
    return datetime.now(timezone.utc).isoformat()


def _avatar_url(user: object) -> str | None:
    """Best-effort avatar URL across TikTokLive v6 (``avatar``) and v7 (ImageModel)."""
    for attr in ("avatar_thumb", "avatar_medium", "avatar_large", "avatar"):
        image = getattr(user, attr, None)
        if image is None:
            continue
        url_list = getattr(image, "url_list", None)
        if url_list:
            return url_list[0]
        url = getattr(image, "url", None)
        if url:
            return url
        nested = getattr(image, "image", None)
        if nested is not None:
            inner = getattr(nested, "url_list", None)
            if inner:
                return inner[0]
    return None


def _user_info(user: object) -> UserInfo:
    unique_id = getattr(user, "unique_id", None)
    nickname = getattr(user, "nickname", None) or getattr(user, "nick_name", None)
    return UserInfo(
        unique_id=unique_id if unique_id else None,
        nickname=nickname if nickname else None,
        profile_picture_url=_avatar_url(user),
    )


class TikTokNormalizer:
    """Stateless normalizers except for the per-user like counter.

    TikTok's LikeEvent carries a CUMULATIVE per-user total. To share one
    "user liked" record per unit we keep ``_like_counts`` keyed by user and
    emit ``delta = total - previous`` (only when delta >= 1).
    """

    def __init__(self) -> None:
        self._like_counts: dict[str, int] = {}

    def reset(self) -> None:
        self._like_counts.clear()

    def normalize(self, event: object) -> EventEnvelope | None:
        if isinstance(event, CaCommentEvent):
            return self._comment(event)
        if isinstance(event, CaGiftEvent):
            return self._gift(event)
        if isinstance(event, CaLikeEvent):
            return self._like(event)
        if isinstance(event, CaFollowEvent):
            return self._follow(event)
        return None

    def _comment(self, event: CaCommentEvent) -> CommentEvent | None:
        message = getattr(event, "comment", None)
        if message is None:
            message = getattr(event, "content", None)
        if message is None:
            return None
        return CommentEvent(
            type="comment",
            timestamp=now_iso(),
            user=_user_info(event.user),
            data={"message": message},
        )

    def _gift(self, event: CaGiftEvent) -> GiftEvent | None:
        # Streak rule: streakable gifts fire repeatedly mid-streak — only
        # emit the final, non-streaking event. Non-streakable emit immediately.
        if event.gift is None:
            return None
        streakable = getattr(event.gift, "type", None) == 1
        if streakable and getattr(event, "streaking", False):
            return None
        diamonds = getattr(event.gift, "diamonds", None)
        if diamonds is None:
            diamonds = getattr(event.gift, "diamond_count", None)
        return GiftEvent(
            type="gift",
            timestamp=now_iso(),
            user=_user_info(event.user),
            data={
                "gift_name": getattr(event.gift, "name", None) or str(event.gift),
                "repeat_count": getattr(event, "repeat_count", 1),
                "diamonds": diamonds,
            },
        )

    def _like(self, event: CaLikeEvent) -> LikeEvent | None:
        like_count = getattr(event, "like_count", None)
        if like_count is None:
            like_count = getattr(event, "total", None)  # v7 cumulative total
        if like_count is None:
            like_count = getattr(event, "count", 0)
        like_count = int(like_count)

        key = str(getattr(event.user, "unique_id", None) or "-")
        previous = self._like_counts.get(key, 0)
        delta = like_count - previous
        self._like_counts[key] = like_count
        if delta < 1:
            return None
        return LikeEvent(
            type="like",
            timestamp=now_iso(),
            user=_user_info(event.user),
            data={"like_count": like_count, "delta": delta},
        )

    def _follow(self, event: CaFollowEvent) -> FollowEvent:
        return FollowEvent(
            type="follow",
            timestamp=now_iso(),
            user=_user_info(event.user),
            data={},
        )