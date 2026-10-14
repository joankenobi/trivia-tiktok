# Correct-answer reveal sound

## Objective

When a round is revealed (the correct answer is shown on the overlay), play one
random sound from `web/sounds/correct`. The cue must play on **every** reveal,
including rounds where nobody answered correctly, and it must not overlap the
winner jingle: with a winner it sounds right after it.

## Problem

Today the reveal is audio-silent except for the winner toast:

- `overlay.js` `renderFirstCorrect()` plays `playRandomPushSound()` at reveal, but
  only when a first-correct winner exists (it is what drives the toast).
- `showRanking()` (delayed `RANK_DELAY_MS`) plays `playRandomPushSound()` again.

So a round nobody solved reveals its answer with no sound at all, and there is no
dedicated "correct answer" cue.

## Decision (user)

- Play a random sound from `web/sounds/correct` when the correct answer is shown.
- Sequence it relative to the winner jingle ("uno después del otro"), not on top
  of it.
- It MUST play even when nobody answered correctly (the answer is revealed
  regardless of the toast).

## Scope

In scope:

1. New folder `web/sounds/correct/` with `.gitkeep` (user drops their own
   `.mp3`/`.wav`; empty folder = silent, no error).
2. `GET /api/v1/sounds/correct` via the existing `_list_sounds(subdir)` helper.
3. A `correct` player in `web/js/sounds.js` (reuse the per-endpoint factory):
   `preloadCorrectSounds` / `playRandomCorrectSound`.
4. A pure, node-testable helper in `web/js/common.js`:
   `correctCueDelayMs(hasWinner)` -> the delay before the correct cue.
5. `web/js/overlay.js`: a once-per-reveal cue guarded by round id, scheduled so it
   follows the winner jingle when there is one, and immediate when there is not.

Out of scope:

- Removing or changing the existing push sounds (toast + rank).
- Host/console audio (overlay-only, like the push/waiting sounds).
- Changing the reveal timing (`reveal_display_s`) or the toast.

## Constraints

- Technical artifacts (code, comments, tests) in English; visible UI copy in
  Spanish.
- The cue must not overlap the winner jingle: `correctCueDelayMs(true) > 0`.
- The cue must fire exactly once per revealed round (the 1s poll reruns `render()`).
- The cue must NOT be conditioned on a winner existing:
  `correctCueDelayMs(false) === 0` (fires immediately) but still fires.
- The overlay must stay error-free when the API or the sounds are unreachable
  (the existing player swallows failures).
- `playRandomPushSound`/`playRandomWaitingSound` behavior unchanged.
- The pure helper stays node-testable from `/js/common.js`.

## Design

### Backend

- `app/api/routes/sounds.py`: add `@router.get("/sounds/correct")` returning
  `_list_sounds("correct")`. No other change; `/sounds/pushPlayer` and
  `/sounds/waiting` stay as they are.

### Frontend

- `web/sounds/correct/.gitkeep`: new folder. Empty = silent.
- `web/js/sounds.js`: `const correctPlayer = createSoundPlayer("/sounds/correct")`
  plus `export function preloadCorrectSounds()` and
  `export async function playRandomCorrectSound()`. The factory is unchanged.
  The path is prefix-free: `api()` already prepends `/api/v1` (see T7).
- `web/js/common.js`:
  - `export const CORRECT_CUE_DELAY_MS = 1500;` — the gap that keeps the correct
    cue from stacking on the winner jingle (a tunable constant).
  - `export function correctCueDelayMs(hasWinner) { return hasWinner ? CORRECT_CUE_DELAY_MS : 0; }`
- `web/js/overlay.js`:
  - state: `let revealedRoundId = null; let correctTimer = null;`.
  - `renderCorrectCue(round)`:
    - `const reveal = !!round && round.status === "revealed" && !!round.correct_option;`
    - if not `reveal`: clear any pending `correctTimer` and return.
    - if `revealedRoundId === round.id`: return (flicker guard for the 1s poll).
    - set `revealedRoundId = round.id`; clear any pending timer.
    - `const delay = correctCueDelayMs(!!firstCorrectResponder(round));`
    - `delay === 0` -> `playRandomCorrectSound()`; else
      `correctTimer = setTimeout(() => { correctTimer = null; playRandomCorrectSound(); }, delay)`.
  - `stopCorrectCue()`: clear the timer (`correctTimer = null`).
  - call `renderCorrectCue(round)` from `render()` (after `renderRanking`).
  - call `stopCorrectCue()` in `renderFinal()` (next to `stopWaitingCue()`).
  - call `preloadCorrectSounds()` in `discover()` (next to the other preloads).

## Tasks

- [x] T1 - `GET /sounds/correct` endpoint (`sounds.py`).
- [x] T2 - `web/sounds/correct/.gitkeep` + `correct` player (`sounds.js`).
- [x] T3 - `CORRECT_CUE_DELAY_MS` + `correctCueDelayMs` (`common.js`).
- [x] T4 - Once-per-reveal cue in the overlay (`overlay.js`).
- [x] T5 - Tests: python endpoint + node helper test.
- [x] T6 - Independent verification (pytest + all node tests + ruff + ASGI smoke).
- [x] T7 - Root cause of "no audio at all": every endpoint URL was doubled
      (`/api/v1/api/v1/sounds/...` -> 404) and the swallow-everything catch hid
      it. Paths are now prefix-free and a regression test guards them.
- [x] T8 - The muted catches warn once per player (list failure + playback
      rejection) instead of failing silently.
- [x] T9 - The finished show no longer re-draws its last round: the final board
      used to replay the reveal (toast + rank swap, each with a sound cue) once
      per poll.

## Authorized edit surfaces

```
app/api/routes/sounds.py
web/js/common.js
web/js/sounds.js
web/js/overlay.js
web/sounds/correct/
tests/unit/test_sounds.py
web/js/correct_cue_test.mjs
web/js/sounds_endpoint_test.mjs
web/js/poll_view_test.mjs
odd/tasks/correct-answer-sound.md
```

## Acceptance criteria

1. A revealed round plays a random sound from `web/sounds/correct` exactly once.
2. When a winner exists, the correct cue plays after the winner jingle
   (`correctCueDelayMs(true) > 0`); the two never start together.
3. When nobody answered correctly, the correct cue still plays
   (`correctCueDelayMs(false) === 0`).
4. `GET /api/v1/sounds/correct` returns existing `.mp3`/`.wav` URLs sorted, `[]`
   when the folder is missing/empty; `/sounds/pushPlayer` and `/sounds/waiting`
   outputs are unchanged.
5. No overlay error when the API or the sounds are unreachable.
6. `venv\Scripts\python.exe -m pytest -q` shows only the known pre-existing
   `tests/unit/test_tts.py::TestAnnounce::test_posts_question_text_to_the_tts_endpoint`
   failure; all node tests pass; `ruff check` is clean on the touched Python file.

## Verification

- `venv\Scripts\python.exe -m pytest -q`
- `node web/js/correct_cue_test.mjs` (new) + all existing `node web/js/*_test.mjs`
- `venv\Scripts\ruff.exe check app/api/routes/sounds.py`
- ASGI smoke: `GET /api/v1/sounds/correct` -> 200 JSON; `GET /overlay` -> 200.

## Progress

Implemented and verified (TDD: RED then GREEN observed for both the Python
endpoint and the node helper). Not committed.

### Files

- `app/api/routes/sounds.py` - added `GET /sounds/correct` via `_list_sounds("correct")`; pushPlayer/waiting routes untouched.
- `web/js/common.js` - added `CORRECT_CUE_DELAY_MS = 1500` and pure `correctCueDelayMs(hasWinner)`.
- `web/js/sounds.js` - added `correctPlayer = createSoundPlayer("/sounds/correct")` (prefix-free, corrected in T7), `preloadCorrectSounds()`, `playRandomCorrectSound()`.
- `web/js/overlay.js` - `renderCorrectCue`/`stopCorrectCue`, `revealedRoundId`/`correctTimer`, wired into `discover()`, `render()`, `renderFinal()`.
- `web/sounds/correct/.gitkeep` - new empty folder marker (covered by the existing `.gitignore` rule `!web/sounds/*/.gitkeep`).
- `tests/unit/test_sounds.py` - added `TestCorrectSounds` (missing/empty folder + sorted audio, non-audio ignored).
- `web/js/correct_cue_test.mjs` - new pure-node test for `correctCueDelayMs`.

### Verification (observed)

- `venv\Scripts\python.exe -m pytest -q` -> `2 failed, 210 passed, 1 warning in 23.80s`.
  - Known pre-existing: `tests/unit/test_tts.py::TestAnnounce::test_posts_question_text_to_the_tts_endpoint`.
  - ALSO pre-existing and OUTSIDE this change's surfaces:
    `tests/unit/test_seed.py::test_shipped_bank_loads_with_matching_correct_answers`
    (`assert 200 == 15`): the sibling uncommitted `first-answer-question-timer`
    feature staged a 200-question `data/questions.json` (+2314 lines) without
    updating `test_seed.py`, which still hard-codes 15. `test_seed.py` imports only
    `app.config`/`app.domain`/`app.infra.seed`, none of which this change touches.
  - New test file: `tests/unit/test_sounds.py` -> `8 passed`.
- Node tests (all pass): `correct_cue_test.mjs`, `waiting_test.mjs`, `sounds_test.mjs`, `current_round_test.mjs`, `first_correct_test.mjs`, `room_fill_test.mjs`, `rank_test.mjs` -> each `all passed`.
- `venv\Scripts\ruff.exe check app/api/routes/sounds.py` -> `All checks passed!`
- ASGI smoke (`httpx.ASGITransport`): `GET /api/v1/sounds/correct` -> 200, body `{"sounds": []}` (empty folder); `GET /overlay` -> 200.
- RED evidence: before implementation `node web/js/correct_cue_test.mjs` failed with
  `SyntaxError: The requested module './common.js' does not provide an export named 'CORRECT_CUE_DELAY_MS'`,
  and `pytest tests/unit/test_sounds.py` failed `3 failed, 5 passed` on `GET /api/v1/sounds/correct` -> 404.

## Follow-up fixes (T7-T9)

Found while testing the reveal cue on a live overlay.

### Root cause (T7)

`api()` in `common.js` already prepends `API = "/api/v1"`, but all three sound
endpoints passed the full path, composing `/api/v1/api/v1/sounds/<dir>` -> 404.
The catch then replaced the list with `[]`, so the overlay was completely silent
with nothing in the console. Pre-existing in `HEAD`, not introduced here.

### Fixes

- `web/js/sounds.js` - three prefix-free endpoints (`/sounds/pushPlayer`,
  `/sounds/waiting`, `/sounds/correct`), import switched to `./common.js` so node
  can load the module, `export const SOUND_ENDPOINTS`. Warn once per player on a
  list failure or a playback rejection (T8).
- `web/js/common.js` - `apiUrl(path)` extracted from `api()`; new pure
  `pollView(show)` returning `{mode: "round" | "final" | "idle", round}`.
- `web/js/overlay.js` - `refresh()` routes through `pollView`. A finished show
  takes the `"final"` path and never calls `render()` again (T9).
- `renderFinal()` still resets `rankRoundId`/`announcedKey`, which is correct:
  the guard reset is what lets the NEXT show announce and swap cleanly.
- `web/js/sounds_endpoint_test.mjs` - regression guard for the prefix (19 checks).
- `web/js/poll_view_test.mjs` - regression guard for the finished-show path (10 checks).

### Verification (observed, T7-T9)

- `venv\Scripts\python.exe -m pytest -q` -> `1 failed, 211 passed, 1 warning in 22.36s`.
  Only the known pre-existing `test_tts.py` failure.
- All 9 node suites pass: `correct_cue_test` (5), `current_round_test` (10),
  `first_correct_test` (12), `poll_view_test` (10), `rank_test` (8),
  `room_fill_test` (14), `sounds_endpoint_test` (19), `sounds_test` (7),
  `waiting_test` (15).
- `node --check` (as `.mjs`) -> OK for `common.js`, `overlay.js`, `sounds.js`.
- End-to-end HTTP: the 3 list endpoints -> 200; all 14 mp3 -> `200 audio/mpeg`.
- End-of-game behavior is verified by inspection only: `overlay.js` is DOM-bound
  with no headless harness, so `pollView` is the tested surface.
- User confirmation: "ya suena".
