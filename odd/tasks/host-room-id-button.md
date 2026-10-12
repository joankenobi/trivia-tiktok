# Host room-id button

## Objective

A button in the host console that places the currently active room id into the
Room ID field on demand, so the host never copies it off the stream by hand.

## Problem

The passive auto-fill already exists (`prefillRoomId`, `web/js/control.js`) but
two guards make it useless for the "I'm starting a game right now" case:

1. `if (!live?.room_id || field.value.trim()) return;` — it never overwrites a
   value the host typed. Defensible for an auto-fill; wrong for an explicit
   button press.
2. `GET /lives/active` returns nulls when `started_at` falls outside the current
   UTC day (`app/services/room_suggestion.py`). A stream started at 20:00 local
   (UTC-3) is 23:00 UTC, so after 21:00 local every still-running live reads as
   "yesterday". A two-hour trivia lands squarely in that window.

The result is a field that fills itself early in the evening and appears broken
late at night. An explicit button is a different intent: the host is asserting
they are streaming.

## Decision (user)

When the live started before the current UTC day: show a warning AND place the
room id anyway. Placement always wins over the freshness warning; the warning is
information, not a gate.

## Scope

In scope: one button, one pure client plan, one new read-only route, a pure
freshness helper, tests.

Out of scope: changing `GET /lives/active` or its contract, the scoring formula,
autoplay, the overlay, any write path.

## Constraints

- `GET /lives/active` is tested and documented as a *suggestion* that never
  returns a stale id. Freeze it. The new route is a separate contract, not a
  mode flag on the old one.
- The UTC-day rule must exist in exactly one place. `room_suggestion.py` already
  owns it with a documented rationale; the new helper goes beside it.
- The new route must never fail on upstream trouble, for the same reason
  `lives.py` documents: a 500 here is indistinguishable from a broken console.
- A `<button>` must NOT be nested inside the `<label>` that wraps the input —
  the label's activation behaviour hijacks the click and focuses the field.
  Restructure the markup instead of nesting.
- Never fail silently: every outcome of the click (placed, placed-with-warning,
  nothing active, upstream down) must say something in the room hint.
- The existing `input` listener clears `#room-hint` on manual edits. Assigning
  `field.value` programmatically fires no `input` event, so a hint written after
  the assignment survives. Keep that ordering.

## Tasks

- [x] T1 — Pure freshness helper in `room_suggestion.py`: `is_stale(...)` as the
      exact inverse of the existing `started_today`, sharing its UTC rule.
- [x] T2 — New read-only route for the explicit action, returning the live
      whenever upstream reports one (`active` true, room id present) plus a
      flag naming the previous-UTC-day case. Nulls on upstream trouble, same
      shape as the suggestion route.
- [x] T3 — Pure client plan in `web/js/common.js` (DOM-free) mapping a live to
      `{ place, value, stale }` so the three outcomes are unit-testable — same
      reason `currentRound` and `firstCorrectResponder` live there.
- [x] T4 — The button: markup outside the `<label>`, handler in `control.js`
      that always places on click, disabled while in flight, and never silent.
- [x] T5 — Tests: `is_stale` across the boundary, the new route's three
      outcomes, and the client plan's three outcomes.

## Authorized edit surfaces

```
app/services/room_suggestion.py
app/api/schemas.py
app/api/routes/lives.py
web/index.html
web/js/common.js
web/js/control.js
web/styles.css
tests/integration/test_room_suggestion.py
tests/integration/test_api_flow.py
web/js/room_fill_test.mjs
```

## Acceptance criteria

1. One click places the active room id, overwriting whatever was typed.
2. A live from a previous UTC day: id placed AND a warning in the room hint.
3. No active live, or upstream down: field untouched, hint says why.
4. `GET /lives/active` behaves exactly as before, including its stale-is-null
   assertions.
5. The UTC-day rule is implemented in exactly one place.
6. The button is not nested inside the Room ID `<label>`.
7. `venv\Scripts\python.exe -m pytest -q`, `node web/js/room_fill_test.mjs`,
   `node web/js/first_correct_test.mjs`, `node web/js/current_round_test.mjs`
   all pass, and `ruff check` reports nothing new on the touched files.

## Route

Delegated writer (one). Three implementation modules plus the console's HTML,
JS and CSS plus three test files — every edit non-trivial. Parent verified the
existing auto-fill, the freshness policy and its real-world failure window
before delegating, and resolved the freshness-vs-placement fork with the user.

## Verification

- `venv\Scripts\python.exe -m pytest -q`
- `node web/js/room_fill_test.mjs`
- `node web/js/first_correct_test.mjs`
- `node web/js/current_round_test.mjs`
- `venv\Scripts\ruff.exe check app/services/room_suggestion.py app/api/routes/lives.py app/api/schemas.py`

## Progress

All five tasks implemented. No change fell outside the authorized surfaces, and
`GET /lives/active` is byte-identical.

- Existing auto-fill confirmed at `web/js/control.js:46`, with its two guards
  documented above. Its behavior is unchanged.
- Placement-vs-warning fork decided with the user: place always, warn when stale.

### What landed

- `is_stale()` in `room_suggestion.py` DELEGATES to `started_today`
  (`not started_today(...)`) instead of re-deriving the comparison, so the UTC-day
  rule is physically single-sourced.
- `CurrentLiveOut(ActiveLiveOut)` with one added `stale: bool`. Subclassed rather
  than re-declared, so the null shape and the millisecond-precision `Z`
  timestamp serializer are literally the frozen contract's.
- `GET /lives/current`: returns the live whenever upstream reports one with a
  room id and `active` true, regardless of freshness, plus `stale`. Same null
  shape on `httpx.HTTPError`, `ValueError`, no live, or `active: false`. Never
  500s.
- `roomFillPlan(live)` in `common.js` → `{ place, value, stale }`. A refusal
  carries `value: null`, so the console can never place an id it never received.
- Markup: `<div class="field">` + `<label for="room-id">`, so the button is a
  sibling and keeps its own click. `.field` replicates the flex box the label
  wrapper used to provide.
- Handler assigns `field.value` BEFORE writing the hint, so the manual-edit
  listener cannot wipe the message. Re-enabled in `finally`.

### Verification

| Command | Result |
| --- | --- |
| `venv\Scripts\python.exe -m pytest -q` | **181 passed** (baseline 163, delta +18) |
| `node web/js/room_fill_test.mjs` | all passed, exit 0 |
| `node web/js/first_correct_test.mjs` | all passed, exit 0 |
| `node web/js/current_round_test.mjs` | all passed, exit 0 |
| `ruff check` on the three Python files | All checks passed, exit 0 |

Confirmed by the parent independently, not taken on trust: the full suite, all
three node tests, and the file mtimes proving
`tests/integration/test_upstream_and_poller.py` was never modified.

RED was observed first: `ImportError: cannot import name 'is_stale'`, the node
test failing on the missing `roomFillPlan` export, and 8 route failures showing
`{'detail': 'Not Found'}`.

### Pre-existing flake, not this work

`test_upstream_and_poller.py::TestPollerResilience::test_failing_upstream_does_not_kill_the_loop`
asserts `calls["n"] >= 1` after a hardcoded `asyncio.sleep(0.05)`. It fails
intermittently under CPU contention, in a file this change never touched
(mtime 2026-10-01, versus 2026-10-02 for every touched file). Five consecutive
runs of that file passed. Fixing it means widening the authorized surfaces.

### Open risks

1. Midnight-crossing race in the new tests: `started_at` pinned to `now` plus a
   `stale is False` assertion flips on a UTC rollover between payload and route
   clock. Probability ~1 in 500k, and the two pre-existing `/lives/active` tests
   already carry the same latent race.
2. The `ValueError` branch (non-JSON upstream body) is untested for the new
   route; it is a copy of the frozen route's branch, so it is covered by proxy.
3. The eight-line transport-failure guard is duplicated between the two routes
   rather than factored, because factoring meant editing the frozen route's
   body. Worth revisiting if a third live route appears.

### Delivery

No git repository in this directory: no work-unit commit, no review candidate.
Delivery is the working-tree change itself.