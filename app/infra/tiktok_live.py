"""HTTP client for the tiktoklives_back read API.

Two behaviours here exist purely because of measured properties of that API:

- Pagination: `has_more` was still `true` at `limit=200`, so a single page per
  poll tick would systematically undercount the tally. `_drain` follows
  `next_cursor` until the upstream is exhausted.
- Timestamps: `ts` arrives naive with no timezone. tiktoklives_back already hit
  an aware/naive comparison crash in its own batch ingest, so everything is
  normalized to aware UTC at this boundary, once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from ..config import settings


@dataclass(frozen=True, slots=True)
class LiveComment:
    event_id: int
    ts_utc: datetime
    unique_id: str
    nickname: str
    avatar_url: str | None
    message: str


@dataclass(frozen=True, slots=True)
class ActiveLive:
    """What upstream reports about the one live it considers active."""

    room_id: str
    started_at_utc: datetime
    host_username: str | None
    active: bool


def _parse_ts(raw: str) -> datetime:
    """Naive upstream timestamps are UTC by contract. Force the tzinfo on."""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class TikTokLiveClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout_s: float | None = None,
        page_limit: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.lives_api_base_url).rstrip("/")
        self.timeout_s = timeout_s or settings.lives_api_timeout_s
        self.page_limit = page_limit or settings.events_page_limit
        # Injection seam for tests: the pagination logic below is the thing under
        # test, so it must not be replaced by a stub.
        self.transport = transport

    async def fetch_since(
        self,
        room_id: str,
        since_event_id: int,
        types: tuple[str, ...] = ("comment",),
    ) -> list[LiveComment]:
        return await self._drain(room_id, since_event_id, until_event_id=None, types=types)

    async def fetch_active_live(self) -> ActiveLive | None:
        """The live upstream currently considers active, if any.

        Returns None when upstream has no live, or answers with something this
        client cannot trust (no room id, unparseable timestamp). Reporting
        "unknown" as None keeps a malformed payload from reaching the console as
        a bogus room id, which would be worse than no suggestion at all.

        Raises on transport/protocol failure: the caller decides whether an
        unreachable upstream is fatal.
        """
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_s,
            follow_redirects=True,
            transport=self.transport,
        ) as client:
            resp = await client.get("/api/v1/lives/active")
            resp.raise_for_status()
            payload = resp.json()

        room_id = str(payload.get("room_id") or "").strip()
        if not room_id:
            return None
        try:
            started_at = _parse_ts(payload["started_at"])
        except (KeyError, TypeError, ValueError):
            return None

        host = payload.get("host_username")
        return ActiveLive(
            room_id=room_id,
            started_at_utc=started_at,
            host_username=str(host) if host else None,
            active=bool(payload.get("active")),
        )

    async def fetch_window(
        self,
        room_id: str,
        since_event_id: int,
        until_event_id: int | None = None,
        types: tuple[str, ...] = ("comment",),
    ) -> list[LiveComment]:
        return await self._drain(room_id, since_event_id, until_event_id, types)

    async def _drain(
        self,
        room_id: str,
        since_event_id: int,
        until_event_id: int | None,
        types: tuple[str, ...],
    ) -> list[LiveComment]:
        out: list[LiveComment] = []
        seen: set[int] = set()
        cursor = since_event_id
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_s,
            follow_redirects=True,
            transport=self.transport,
        ) as client:
            while True:
                params: dict[str, str | int] = {
                    "since": cursor,
                    "types": ",".join(types),
                    "limit": self.page_limit,
                }
                resp = await client.get(f"/api/v1/lives/{room_id}/events", params=params)
                resp.raise_for_status()
                payload = resp.json()

                for ev in payload.get("events", []) or []:
                    if ev.get("type") not in types:
                        continue
                    eid = int(ev["event_id"])
                    # The same logical comment can be re-ingested under a new
                    # event_id, so guard on id here too.
                    if eid in seen:
                        continue
                    if until_event_id is not None and eid > until_event_id:
                        continue
                    seen.add(eid)
                    user = ev.get("user") or {}
                    data = ev.get("data") or {}
                    out.append(
                        LiveComment(
                            event_id=eid,
                            ts_utc=_parse_ts(ev["ts"]),
                            unique_id=str(user.get("unique_id") or ""),
                            nickname=str(user.get("nickname") or ""),
                            avatar_url=user.get("avatar"),
                            message=str(data.get("message") or ""),
                        )
                    )

                if until_event_id is not None and out:
                    highest = max(c.event_id for c in out)
                    if highest >= until_event_id:
                        break

                if not payload.get("has_more"):
                    break
                nxt = payload.get("next_cursor")
                if nxt is None or int(nxt) <= cursor:
                    break
                cursor = int(nxt)

        out.sort(key=lambda c: c.event_id)
        return out
