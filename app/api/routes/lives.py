"""Room suggestion endpoint."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter

from ...infra.tiktok_live import TikTokLiveClient
from ...services import room_suggestion
from ..schemas import ActiveLiveOut

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
