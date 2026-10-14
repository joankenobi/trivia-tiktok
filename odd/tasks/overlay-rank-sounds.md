# Overlay: ranking swap animation + push sounds

## Objective

On the OBS overlay (`web/overlay.html`), after the reveal toast announces the
best responder, animate the ranking so real position changes (e.g. 2nd<->1st,
then 4th<->3rd) visibly trade places with avatar images. Additionally, play one
sound picked at random from `web/sounds/pushPlayer/` every time either overlay
animation runs: the ranking swap and the first-correct toast.

## Problem

The overlay shows the question, options, timer, the first-correct toast and the
final podium, but it has no leaderboard surface. The ranking only exists inside
`/control` and the standalone `/ranking` page, so a live viewer never sees who
climbed or dropped. The overlay is also silent: neither the toast nor any motion
has an audio cue.

## Why now

The data already exists. `GET /shows/{show_id}/ranking` returns the ordered
`RankingEntry` list (points desc, then nickname). The overlay already polls
`GET /shows/{show_id}` every second and already resolves its show through
`GET /shows/active`. The swap is pure presentation; the sound is one new static
folder plus one listing endpoint.

## Scope

In scope:

1. A rank panel (`#o-rank`) on the overlay showing the top 4 entries, hidden by
   default.
2. Show it only after the reveal ("Solo tras el cartel"); animate only the swaps
   that actually happen; hide it when the next question opens or the show
   finishes.
3. A sounds folder `web/sounds/pushPlayer/` (served at `/sounds/pushPlayer/`).
4. A listing endpoint `GET /api/v1/sounds/pushPlayer` returning the available
   `.mp3`/`.wav` URLs.
5. A sounds module `web/js/sounds.js` that preloads that list and plays one at
   random (`playRandomPushSound()`).
6. Wire the sound into BOTH animations: the first-correct toast and the ranking
   swap.

Out of scope:

- Any change to the ranking query, the scoring formula, `/control` or `/ranking`.
- Any autoplay-unlock UI beyond a best-effort `play()` rejection catch.

## Constraints

- Technical artifacts (code, comments) in English; visible UI copy in Spanish,
  consistent with the overlay ("Esperando.", "Fin de la partida").
- Reuse `/styles.css` (`.hidden`) and existing overlay patterns. New CSS stays in
  `web/overlay.css`.
- The overlay must not throw when the API or the sounds are momentarily
  unreachable.
- Animate with DOM transform/opacity transitions only. No external animation
  libraries.
- Pure helpers stay node-testable from `/js/common.js` (node already imports
  `common.js`, see `web/js/first_correct_test.mjs`).
- Deterministic under an injected RNG: `pickRandom(list, rnd = Math.random)`.

## Design

- `web/js/common.js`: add two pure exports.
  - `rankOffsets(prevIds, rows, pitch)` -> for each row of the NEW order, the
    starting `translateY` in px: `(oldIndex - newIndex) * pitch`; `0` when the
    row is absent from `prevIds` (a new entrant) or `prevIds` is null/empty.
  - `pickRandom(list, rnd = Math.random)` -> one element of `list`, or `null`
    when the list is empty/null; consumes `rnd()` so tests can seed it.
- `web/js/sounds.js` (new ES module): imports `api` and `pickRandom`. Loads
  `/api/v1/sounds/pushPlayer` once (cached promise), builds an array of URLs,
  preloads one `Audio` per URL, and exports `playRandomPushSound()` which picks
  one at random, resets `currentTime`, and plays it, swallowing autoplay
  rejections. Also exports `preloadPushSounds()` for an early call at boot.
- `web/js/overlay.js`: import `rankOffsets` and the sounds helpers. Add state
  `prevRankIds = null`, `rankRoundId = null`, `rankTimer = null`; constants
  `RANK_SIZE = 4`, `RANK_PITCH` (row height + gap, from CSS), `RANK_DELAY_MS`.
  Add `renderRanking(round)` (called from `render()`), `showRanking()`,
  `hideRanking()` and `renderRankList(rows)`. Play the sound at the start of the
  swap and at the toast reveal. Call `hideRanking()` in `renderFinal()`.
- `web/overlay.html`: add `#o-rank` (`<div id="o-rank" class="o-rank hidden">`
  with a title and an empty list) between `#o-first-correct` and `#o-final`.
- `web/overlay.css`: styles for `.o-rank`, `.o-rank-title`, `.o-rank-list`,
  `.o-rank-row`, `.o-rank-pos`, scoped avatar/number, plus a
  `prefers-reduced-motion` guard.
- `app/api/routes/sounds.py` (new router): `GET /sounds/pushPlayer` returns
  `{"sounds": ["/sounds/pushPlayer/<file>", ...]}` with files sorted by name.
- `app/main.py`: register the new router on the existing `/api/v1` router.
- `web/sounds/pushPlayer/`: three short placeholder `.wav` files so the feature
  is testable now; the user drops their own `.mp3`/`.wav` later.

## Tasks

- [x] T1 - Folder + placeholders. Create `web/sounds/pushPlayer/` and synthesize
      three short, seed-clean `.wav` placeholders (pure-python `wave`).
- [x] T2 - Listing endpoint. Add `app/api/routes/sounds.py` and register it in
      `app/main.py`. List only `.mp3`/`.wav`, sorted, as
      `/sounds/pushPlayer/<name>` URLs; empty list when the folder is empty.
- [x] T3 - Pure helpers. Add `rankOffsets` and `pickRandom` to `web/js/common.js`.
- [x] T4 - Sounds module. Add `web/js/sounds.js` with `preloadPushSounds()` and
      `playRandomPushSound()`.
- [x] T5 - Rank markup + styles. Add `#o-rank` to `web/overlay.html` and the
      `.o-rank*` styles to `web/overlay.css`.
- [x] T6 - Rank behavior. Implement `renderRanking`/`showRanking`/`hideRanking`/
      `renderRankList` in `web/js/overlay.js` and wire `renderRanking` into
      `render()`.
- [x] T7 - Wire sounds. Play `playRandomPushSound()` at the ranking swap start
      and at the first-correct toast reveal; call `preloadPushSounds()` at boot.
- [x] T8 - Tests. Add node tests for `rankOffsets` and `pickRandom`.

## Authorized edit surfaces

`app/api/routes/sounds.py`
`app/main.py`
`web/js/common.js`
`web/js/sounds.js`
`web/js/overlay.js`
`web/overlay.html`
`web/overlay.css`
`web/js/rank_test.mjs`
`web/js/sounds_test.mjs`
`web/sounds/pushPlayer/`
`odd/tasks/overlay-rank-sounds.md`

## Acceptance criteria

- `GET /api/v1/sounds/pushPlayer` returns a JSON array of existing `.mp3`/`.wav`
  URLs, sorted; `[]` when the folder has none.
- The overlay shows the rank panel only after a reveal, animates the position
  changes, and hides it when the next question opens or the show finishes.
- A random sound plays once per animation run (toast reveal and rank swap).
- No overlay error when the API or the sounds are unreachable.
- All existing node tests still pass and the python suite shows only the known
  pre-existing failure.

## Verification

- `node web/js/rank_test.mjs`
- `node web/js/sounds_test.mjs`
- `node web/js/current_round_test.mjs`, `node web/js/first_correct_test.mjs`,
  `node web/js/room_fill_test.mjs` (existing)
- `venv\Scripts\python.exe -m pytest -q` (expect only the pre-existing
  `tests/unit/test_tts.py::TestAnnounce::test_posts_question_text_to_the_tts_endpoint`
  failure)
- ASGI: `GET /overlay` -> 200; `GET /api/v1/sounds/pushPlayer` -> 200 JSON.

## Progress

Done. All 8 tasks implemented and verified by the orchestrator (not only by the
writer's report).

Files:

- `app/api/routes/sounds.py` (new) and its registration in `app/main.py`.
- `web/js/common.js` (`rankOffsets`, `pickRandom`); `web/js/sounds.js` (new).
- `web/js/overlay.js` (rank swap + sound wiring); `web/overlay.html` (`#o-rank`);
  `web/overlay.css` (`.o-rank*`).
- `web/js/rank_test.mjs`, `web/js/sounds_test.mjs` (new).
- `web/sounds/pushPlayer/push-1.wav`, `push-2.wav`, `push-3.wav` (placeholders).

Verification (reproduced independently):

- `node web/js/rank_test.mjs`, `sounds_test.mjs`, `current_round_test.mjs`,
  `first_correct_test.mjs`, `room_fill_test.mjs` -> all pass (exit 0).
- `venv\Scripts\python.exe -m pytest -q` -> 198 passed, 1 failed (only the
  pre-existing `tests/unit/test_tts.py::TestAnnounce::test_posts_question_text_to_the_tts_endpoint`).
- ASGI: `GET /overlay` -> 200 text/html; `GET /api/v1/sounds/pushPlayer` -> 200
  with the three WAV URLs.
- Test-first RED observed (missing exports) before the helpers, GREEN after.

Notes:

- `RANK_PITCH` (62) must stay equal to `.o-rank-row` height (54) +
  `.o-rank-list` gap (8) or every animated row lands off by part of a row.
- Autoplay: an OBS browser source allows audio; a plain browser may block the
  first `play()` until a user gesture.
- The overlay caches the sound list once per page load; adding files to the
  folder needs a page reload (or a browser-source refresh).
- Flaky and pre-existing, unrelated to this change:
  `tests/integration/test_show_runner.py::TestPlanTransitions::test_plan_waits_while_a_window_is_open`
  failed once under load and then passed 5/5 in isolation and 3/3 full-suite
  reruns.

No commit made (the user commits manually).
