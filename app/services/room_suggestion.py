"""Which room to offer the host as a starting guess.

Upstream keeps at most one live "active" and the host console used to demand a
room id typed by hand. Suggesting that room removes the only field the host
could not read off the stream itself, so the rule for when to suggest is
deliberately narrow and lives here instead of in a route:

- `active` must be true. A live that ended is not a starting point.
- `started_at` must fall on the current UTC day. A stream still running from
  yesterday is stale, and pre-filling it would point the host at the wrong
  audience.

The day boundary is UTC, because `started_at` arrives naive and this codebase
already treats naive upstream timestamps as UTC (see `tiktok_live._parse_ts`).
Evaluating "today" in the host's local zone instead would mean comparing a UTC
instant against a local calendar day, which silently misclassifies every stream
in the first few hours after UTC midnight. `now_utc` is injectable so that
boundary is testable instead of clock-dependent.

The same rule is also needed read the *other* way. The host's explicit button
places the room id even when a live is yesterday's, and it must warn about it,
so the freshness question lives here beside the suggestion rather than being
answered twice in two modules where the two answers would drift apart at
midnight. `is_stale` is that inverse, and it delegates instead of re-deriving,
so there is exactly one UTC-day rule in the codebase.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..infra.tiktok_live import ActiveLive


def started_today(started_at_utc: datetime, now_utc: datetime) -> bool:
    """True when both instants fall on the same UTC calendar day."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return started_at_utc.date() == now_utc.date()


def is_stale(started_at_utc: datetime, now_utc: datetime | None = None) -> bool:
    """True when the live started before the current UTC calendar day.

    The exact inverse of `started_today`, and deliberately not a second
    implementation of it: two copies of this comparison are two chances for the
    auto-fill and the button to disagree about the same live, and the whole
    reason the button exists is that they currently would.

    Stale is information, never a refusal. The host pressing the button is
    asserting they are streaming, so the caller still places the room id and
    warns with this flag. It only means "this started before today UTC", which
    is why it can be answered without ever looking at the room id.
    """
    now = now_utc or datetime.now(timezone.utc)
    return not started_today(started_at_utc, now)


def suggest_room(
    live: ActiveLive | None, *, now_utc: datetime | None = None
) -> ActiveLive | None:
    """The live to offer as a starting room id, or None to offer nothing.

    Pure, so the whole policy is exercised without an HTTP server. Passing
    anything the host typed is not this function's job: an empty result simply
    leaves the field empty for manual entry.
    """
    if live is None or not live.active:
        return None
    now = now_utc or datetime.now(timezone.utc)
    return live if started_today(live.started_at_utc, now) else None
