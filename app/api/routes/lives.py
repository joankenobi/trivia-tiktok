"""The two read-only live lookups behind the console's room id field.

`/lives/active` suggests a room to start from and refuses a stale one.
`/lives/current` serves the host's explicit button and reports the same live
with the staleness flagged instead. Two contracts rather than one parameterized
route, because "would we pre-fill this" and "the host says they are streaming"
are different questions, and answering both with a flag would put the safe
default behind a parameter somebody eventually passes the wrong way.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter

from ...infra.tiktok_live import TikTokLiveClient
from ...services import room_suggestion
from ..schemas import ActiveLiveOut, CurrentLiveOut

log = logging.getLogger(__name__)

router = APIRouter(tags=["lives"])


def _client() -> TikTokLiveClient:
    """The process-wide client when the app is up, a fresh one otherwise.

    Reusing the registry's client keeps the configured base URL and timeout in a
    single place, and matters more than it looks: tests inject a fake transport
    through that same client.
    """
    from ...main_state import registry

    return registry.client if registry is not None else TikTokLiveClient()


@router.get("/lives/active", response_model=ActiveLiveOut)
async def get_active_live() -> ActiveLiveOut:
    """Suggest a room id to start from, or return nulls to start from scratch.

    Never fails on upstream trouble. The host console must stay usable with the
    upstream down, and a 500 here would be indistinguishable from a broken
    console, so an unreachable upstream degrades to "no suggestion".
    """
    try:
        live = await _client().fetch_active_live()
    except (httpx.HTTPError, ValueError) as exc:
        # HTTPError covers both transport failures and a non-2xx status; ValueError
        # covers a body that is not JSON. Anything else is a real bug, not weather.
        log.warning("active live lookup failed: %s", exc)
        return ActiveLiveOut()

    suggested = room_suggestion.suggest_room(live)
    if suggested is None:
        return ActiveLiveOut()
    return ActiveLiveOut(
        room_id=suggested.room_id,
        host_username=suggested.host_username,
        started_at=suggested.started_at_utc,
    )


@router.get("/lives/current", response_model=CurrentLiveOut)
async def get_current_live() -> CurrentLiveOut:
    """The live to place in the room field because the host says they are live.

    The sibling of the suggestion route with the freshness gate removed, and
    deliberately a separate contract instead of a flag on that one: `suggest_room`
    is a *suggestion* that must never hand back a stale id, while this answers an
    explicit assertion. A live from a previous UTC day is therefore returned with
    `stale` set rather than nulled -- a host who started at 20:00 local (23:00 UTC)
    is still streaming after 21:00, and the auto-fill calls that "yesterday".

    Never fails on upstream trouble, for the same reason as the route above: a
    500 here is indistinguishable from a broken console, and the host is one
    click away from a show. An unreachable upstream degrades to the null shape,
    same as a missing live, because the console must always have an answer to
    render in the room hint.
    """
    try:
        live = await _client().fetch_active_live()
    except (httpx.HTTPError, ValueError) as exc:
        # Same two failures as the suggestion route, same reasoning about which
        # ones are weather and which are bugs.
        log.warning("current live lookup failed: %s", exc)
        return CurrentLiveOut()

    # `fetch_active_live` already dropped a room-less or unparseable payload, so
    # what is left to reject is a live that ended: the host cannot join a room
    # that is closed.
    if live is None or not live.active:
        return CurrentLiveOut()
    return CurrentLiveOut(
        room_id=live.room_id,
        host_username=live.host_username,
        started_at=live.started_at_utc,
        stale=room_suggestion.is_stale(live.started_at_utc),
    )
