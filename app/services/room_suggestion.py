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
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..infra.tiktok_live import ActiveLive


def started_today(started_at_utc: datetime, now_utc: datetime) -> bool:
    """True when both instants fall on the same UTC calendar day."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return started_at_utc.date() == now_utc.date()


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
