# First-answer-anchored question timer + waiting audio

## Objective

The active question's countdown must start when the FIRST answer arrives, not when
the question opens. While nobody has answered, the question stays open
indefinitely (no auto-close). During that wait the overlay plays a sound from
`web/sounds/waiting/` every `2 x question_time_limit_s` seconds.

## Problem

Today the window is anchored on `Round.opened_at`:

- `show_runner._plan` closes an OPEN round once `window_left(opened_at, limit)`
  hits 0.
- `_common.deadline_epoch_ms` sends `opened_at + limit`, which is what the overlay
  and console count down.
- `session_service.ingest_event` scores the speed bonus on `comment.ts - opened_at`.

So the timer runs from the instant the question appears, even before the TTS has
finished reading it and before anyone can possibly answer. The host wants the clock
to start with the audience, i.e. on the first answer.

## Decision (user)

- Indefinite wait: no auto-close while there is no first answer. If nobody ever
  answers, the host closes the round by hand.
- During that indefinite wait, replay a waiting sound every
  `2 x question_time_limit_s` seconds.

## Scope

In scope:

1. New `Round.first_answer_at` column (nullable) + idempotent SQLite migration.
2. Clock start = first accepted answer; countdown = `first_answer_at + limit`.
3. Speed bonus re-anchored to the first answer (the first answerer is elapsed 0).
4. Runner: WAIT indefinitely until a first answer exists, then close on expiry.
5. `deadline_epoch_ms` returns None while OPEN with no first answer, so both
   clients hide the timer (existing null handling, no JS change needed).
6. New `GET /api/v1/sounds/waiting` listing endpoint, mirroring
   `/sounds/pushPlayer`.
7. Waiting-sound player in `web/js/sounds.js` + cadence in `web/js/overlay.js`,
   driven by two new pure helpers in `web/js/common.js`.

Out of scope:

- Changing `reveal_display_s`, the scoring formula shape, or the tally contract.
- Any auto-close safety cap (the user chose indefinite wait).
- Console/host audio (waiting sound is overlay-only, like the push sounds).
- Questions-per-show, autoplay toggle, DB engine, or the frozen
  `GET /lives/active`.

## Constraints

- Technical artifacts (code, comments) in English; visible UI copy in Spanish.
- The speed bonus MUST keep measuring "how fast relative to the question clock".
  Since the question clock is now the first answer, the elapsed base becomes
  `first_answer_at`; otherwise a late first answer silently kills the bonus for
  the whole round (every answer caps at `limit`).
- The runner must not busy-loop: a WAIT action MUST carry `delay_s > 0`
  (`_apply` does not handle WAIT, so `delay_s == 0` would spin).
- The migration must be idempotent and must not touch an existing DB destructively
  (there is a live `trivia_game.db`; there is no migration framework).
- The overlay must stay error-free when the API or the sounds are unreachable.
- Pure helpers stay node-testable from `/js/common.js`.
- `playRandomPushSound()`/`preloadPushSounds()` behavior must not change.

## Design

### Backend

- `app/infra/models.py`: add `first_answer_at: Mapped[datetime | None] =
  mapped_column(DateTime, nullable=True)` to `Round` (naive UTC, like the other
  timestamps). Import `datetime` already present.
- `app/db.py`: in `init_models()`, after `create_all`, run a sync helper
  (`conn.run_sync`) that reads `PRAGMA table_info(rounds)` and runs
  `ALTER TABLE rounds ADD COLUMN first_answer_at DATETIME` only when the column is
  missing. Idempotent; leaves data untouched.
- `app/services/session_service.py`:
  - `open_round`: also reset `rnd.first_answer_at = None`.
  - `ingest_event`: on the first accepted answer set
    `rnd.first_answer_at = _to_naive_utc(comment.ts_utc)`; compute
    `base = _aware(rnd.first_answer_at)` and
    `elapsed_ms = int(max(0.0, (comment.ts_utc - base).total_seconds() * 1000))`,
    capped at `limit_ms` as today. The `opened_at` parameter becomes obsolete
    (`base` is now the first answer); remove it and fix callers, or keep it only
    if a caller still needs it. `close_round` is unchanged (it re-scores from the
    stored `elapsed_ms`).
- `app/services/show_runner.py`: `_plan` OPEN branch:
  - if `opened.first_answer_at is None`:
    `return Action(Step.WAIT, opened.id, settings.poll_interval_s)`.
  - else `left = window_left(opened.first_answer_at, opened.question_time_limit_s)`
    and `WAIT if left > 0 else CLOSE` (today's line, new anchor).
- `app/api/routes/_common.py`: `deadline_epoch_ms` OPEN branch: return
  `_epoch_ms(rnd.first_answer_at) + rnd.question_time_limit_s * 1000` only when
  `first_answer_at is not None`; otherwise `None`.
- `app/api/routes/sounds.py`: extract `_list_sounds(subdir)`; keep
  `GET /sounds/pushPlayer` byte-identical in output; add `GET /sounds/waiting`
  returning `{"sounds": ["/sounds/waiting/<file>", ...]}` (sorted, `.mp3`/`.wav`
  only, `[]` when the folder is missing/empty).
- `app/domain/session.py`: the pydantic `Round` model is not used at runtime
  (only `RoundStatus` is imported elsewhere). Optional: add `first_answer_at` for
  consistency. Do not otherwise change it.

### Frontend

- `web/sounds/waiting/`: new folder with `.gitkeep`. The user drops their own
  `.mp3`/`.wav` there; empty folder = silent, no error. No placeholder audio is
  generated.
- `web/js/common.js`: two pure exports.
  - `isWaitingForFirstAnswer(round)` -> `!!round && round.status === "open" &&
    !round.deadline_epoch_ms`.
  - `waitingCueIntervalMs(questionTimeLimitS)` -> `2 * Number(...) * 1000`,
    returning `0`/`null`-safe value when the input is not a finite number.
- `web/js/sounds.js`: generalize into a per-folder player factory keyed by
  endpoint (`/api/v1/sounds/pushPlayer`, `/api/v1/sounds/waiting`). Keep the cached
  promise + preloaded `Audio` map + swallowed failures. Export
  `preloadPushSounds`, `playRandomPushSound` (unchanged behavior) and
  `preloadWaitingSounds`, `playRandomWaitingSound`.
- `web/js/overlay.js`: wait-cue state machine driven from the poll.
  - state: `waitingRoundId = null`, `waitingTimer = null`.
  - `renderWaitingCue(round)`: if not waiting -> `stopWaitingCue()`; else if
    `waitingRoundId === round.id` -> return (flicker guard); else set
    `waitingRoundId` and `startWaitingCue(round.question_time_limit_s)`.
  - `startWaitingCue(limitS)`: clear any timer, `setInterval` of
    `waitingCueIntervalMs(limitS)` that calls `playRandomWaitingSound()`. No
    immediate cue (the TTS is still reading the question).
  - `stopWaitingCue()`: clear the timer, `waitingRoundId = null`.
  - Call `renderWaitingCue(current)` from `refresh()` (after `renderTimer`), and
    `stopWaitingCue()` in `renderFinal()` when the show finishes.

## Tasks

- [x] T1 - Schema + migration (`models.py`, `db.py`).
- [x] T2 - Service clock start + scoring re-anchor (`session_service.py`).
- [x] T3 - Runner waits for the first answer, closes on its expiry (`show_runner.py`).
- [x] T4 - Deadline anchored on the first answer (`_common.py`).
- [x] T5 - `GET /sounds/waiting` endpoint (`sounds.py`).
- [x] T6 - Waiting-sound folder + player functions (`sounds/waiting/`, `sounds.js`).
- [x] T7 - Overlay wait cue + pure helpers (`common.js`, `overlay.js`).
- [x] T8 - Tests: python (session/runner/api) + node (`waiting_test.mjs`).
- [x] T9 - Independent verification (pytest + all node tests + ruff).

## Authorized edit surfaces

```
app/infra/models.py
app/db.py
app/services/session_service.py
app/services/show_runner.py
app/api/routes/_common.py
app/api/routes/sounds.py
app/domain/session.py
web/js/common.js
web/js/sounds.js
web/js/overlay.js
web/sounds/waiting/
tests/integration/test_session_flow.py
tests/integration/test_show_runner.py
tests/integration/test_api_flow.py
tests/unit/test_sounds.py
web/js/waiting_test.mjs
odd/tasks/first-answer-question-timer.md
```

## Acceptance criteria

1. A round opened with no answers has `deadline_epoch_ms == None` and the runner
   WAITs (never closes on its own).
2. The first accepted answer sets `first_answer_at`, and the round then closes at
   `first_answer_at + question_time_limit_s`.
3. Speed bonus is measured from the first answer: the first answerer has
   `elapsed_ms == 0` and the full bonus; a later answer has more elapsed and less
   bonus.
4. `GET /api/v1/sounds/waiting` returns existing `.mp3`/`.wav` URLs sorted, `[]`
   when empty; `/sounds/pushPlayer` output is unchanged.
5. The overlay plays a waiting sound every `2 x question_time_limit_s` while a
   round waits for its first answer, and stops as soon as the first answer lands,
   the round changes, or the show finishes.
6. No overlay error when the API or the sounds are unreachable.
7. Existing DB upgrades in place (no data loss) via the idempotent column add.
8. `venv\Scripts\python.exe -m pytest -q` shows only the known pre-existing
   `tests/unit/test_tts.py::TestAnnounce::test_posts_question_text_to_the_tts_endpoint`
   failure; all node tests pass; `ruff check` is clean on the touched Python files.

## Verification

- `venv\Scripts\python.exe -m pytest -q`
- `node web/js/waiting_test.mjs` (new)
- `node web/js/sounds_test.mjs`, `node web/js/current_round_test.mjs`,
  `node web/js/first_correct_test.mjs`, `node web/js/room_fill_test.mjs`,
  `node web/js/rank_test.mjs`
- `venv\Scripts\ruff.exe check app`
- ASGI smoke: `GET /overlay` -> 200; `GET /api/v1/sounds/waiting` -> 200 JSON.

## Progress

Implemented and verified. The question clock now starts on the first accepted
answer; a round with no first answer waits indefinitely (runner `Step.WAIT` with
`poll_interval_s`), and the overlay replays a waiting sound every
`2 x question_time_limit_s`.

### Files

- `app/infra/models.py` — added nullable `Round.first_answer_at`.
- `app/db.py` — `_ensure_first_answer_at()` idempotent SQLite column add, run via
  `run_sync` after `create_all`.
- `app/services/session_service.py` — `open_round` resets the column; first
  accepted answer sets it; elapsed/bonus measured from it; dropped the obsolete
  `opened_at` parameter.
- `app/services/show_runner.py` — OPEN branch WAITs on the poll interval until a
  first answer exists, then computes the window from `first_answer_at`.
- `app/api/routes/_common.py` — `deadline_epoch_ms` anchored on
  `first_answer_at` (None while waiting).
- `app/api/routes/sounds.py` — extracted `_list_sounds`; added
  `GET /sounds/waiting`; `/sounds/pushPlayer` output unchanged.
- `web/sounds/waiting/.gitkeep` — waiting-sound folder (empty = silent).
- `web/js/common.js` — `isWaitingForFirstAnswer`, `waitingCueIntervalMs`.
- `web/js/sounds.js` — per-endpoint player factory; push behavior unchanged;
  added `preloadWaitingSounds` / `playRandomWaitingSound`.
- `web/js/overlay.js` — waiting-cue state machine on the 1s poll.
- `tests/unit/test_sounds.py`, `web/js/waiting_test.mjs` — new tests.
- `tests/integration/{test_session_flow,test_show_runner,test_api_flow}.py` —
  re-anchored and added coverage (plus a schema-migration regression test).

### Verification (observed)

| Command | Result |
| --- | --- |
| `venv\Scripts\python.exe -m pytest -q` | `1 failed, 208 passed` — only the known pre-existing `tests/unit/test_tts.py::TestAnnounce::test_posts_question_text_to_the_tts_endpoint` |
| `node web/js/waiting_test.mjs` + the 5 existing node tests | all `all passed` |
| `venv\Scripts\ruff.exe check <touched app files>` | `All checks passed!` |
| ASGI smoke `GET /overlay`, `GET /api/v1/sounds/waiting` | `200` / `200 application/json {'sounds': []}` |
| Migration on a copy of the live `trivia_game.db` | column added, run twice idempotent, 252 rows preserved |

Baseline note: `ruff check app` on untouched files (`rounds.py`, `main.py`,
`poller.py`, `seed.py`, `avatar_cache.py`, `tally.py`) already reported errors
before this change; the touched files are clean.
