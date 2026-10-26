# Reveal cue sequencing (serialized sound + animation)

## Objective

Serialize the overlay's reveal cues so that no animation starts while the
previous cue's sound is still playing. Each animation begins exactly when its
own sound begins, and only after the previous sound has finished.

## Problem

On a reveal frame three cues fire close together and overlap:

- toast `o-fc-in` entrance + push sound at `t=0` (`renderFirstCorrect`)
- ranking swap + push sound at `t=1200ms` (`RANK_DELAY_MS`)
- correct-answer sound at `t=0` or `t=1500ms` (`CORRECT_CUE_DELAY_MS`)

Both fixed delays assume sounds shorter than the delay. With real clips the
cues stack and the sequence reads as noise.

## Decision (user-confirmed)

Chain order: **Toast -> Correct -> Ranking**.

- The toast (winner jingle) owns the moment it announces.
- The correct-answer cue follows it: its visual (highlight + `Correcta:` status)
  and its sound start together, after the toast's sound ends.
- The ranking swap follows the correct cue: visual + push sound together.
- With no winner there is no toast, so the correct cue starts immediately
  (same as today) and the ranking still comes after it.
- The fixed gaps `CORRECT_CUE_DELAY_MS` and `RANK_DELAY_MS` are removed: the
  real sound duration is the only gate. Awaiting a sound end replaces both.
- The waiting beep never queues: it only fires when the cue channel is idle, so
  it can never delay a reveal.
- The show-final podium drops any queued cue (epoch guard): nothing sounds
  under the podium.

## Scope

- In: `web/js/common.js`, `web/js/sounds.js`, `web/js/overlay.js`, node tests.
- Out: CSS, server, sound assets, control/ranking pages.

## Design

### `common.js`

- Add pure `createCueSequencer()`: a FIFO of async tasks. `run(task)` returns a
  promise that settles when that task finished; the next task starts only after
  the previous settles (errors are swallowed so one failed cue never wedges the
  chain). Expose `busy` (queued or running > 0).
- Remove `CORRECT_CUE_DELAY_MS` and `correctCueDelayMs` (superseded).

### `sounds.js`

- `playRandom()` per player returns a promise that resolves when the clip ends
  (`ended`), and also resolves immediately on: empty list, no Audio, `play()`
  rejection/throw (autoplay block). Fallback timeout so a clip that never fires
  `ended` cannot wedge the chain forever: finite `audio.duration` ? duration +
  500ms : 5000ms.
- Warnings stay one-shot; failures stay non-fatal.

### `overlay.js`

- One module-level sequencer. Flicker guards (`announcedKey`, `revealedRoundId`,
  `rankRoundId`) are still set synchronously at schedule time; the queue holds
  the paint+sound, not the guard.
- Epoch counter bumped when the active round id changes and when the show
  finalizes. Every queued task re-checks the epoch at start and no-ops if stale.
- Toast: guard set, then queued task builds the card and plays the push sound.
- Correct: guard set, then queued task arms the reveal (`revealArmedRoundId`),
  re-renders, and plays the correct sound. `render()` paints `is-correct` /
  `is-dimmed` and the `Correcta:` status only once armed; before that a revealed
  round looks closed.
- Ranking: guard set, then queued task fetches the ranking, paints the swap and
  plays the push sound. No `rankTimer`.
- Waiting cue: interval callback fires only when `!sequencer.busy`.

## Tasks

- [x] T1 RED: `web/js/cue_sequencer_test.mjs` (serial order, busy, error does
      not break the chain, later tasks still run). Observed failure first:
      `SyntaxError: The requested module './common.js' does not provide an
      export named 'createCueSequencer'`.
- [x] T2 GREEN: `createCueSequencer` in `common.js`; deleted
      `correct_cue_test.mjs` and the superseded `CORRECT_CUE_DELAY_MS` /
      `correctCueDelayMs`. Sequencer test passes (10 checks).
- [x] T3 `sounds.js`: `playAndWait()` resolves on `ended` with a fallback timer
      (finite duration ? duration + 500ms, else 5000ms); resolves immediately on
      empty list / no Audio / `play()` reject; warnings stay one-shot.
- [x] T4 `overlay.js`: toast/correct/ranking wired through the module-level
      sequencer with an epoch guard, `revealArmedRoundId` arming, waiting skip
      when `!cues.busy`; removed `RANK_DELAY_MS`/`rankTimer`/`correctTimer`.
- [x] T5 Verify: `node --test web/js/*.mjs` -> tests 9 / pass 9 / fail 0.
      `python -m pytest -q` -> 1 failed, 211 passed; the single failure
      (`tests/unit/test_tts.py::...test_posts_question_text_to_the_tts_endpoint`)
      is pre-existing and unrelated (confirmed by stashing the JS edits and
      re-running the test, same failure).

## Acceptance

- No two cue sounds overlap; each animation starts with its own sound.
- Order on a reveal with a winner: toast -> correct -> ranking.
- No winner: correct then ranking, both immediate-ish.
- Podium: silent, pending cues dropped.
- Waiting beep never queues behind reveal cues.

## Verification

- `node --test web/js/*.mjs`
- `python -m pytest -q` (untouched server; sanity only)

## Status

Implemented and verified (node tests green; pre-existing pytest TTS failure
unrelated). User commits manually (repo convention on this feature line).

## Review outcome (this candidate)

- Candidate: 6 paths (4 modified/deleted tracked + 2 intended untracked),
  548 changed lines, target `sha256:3aea0dd8...`, lineage `review-76f64c6494bd96dc`.
- Native preflight returned `action: start`; the `gentle-ai.review-integration.consent/v3`
  envelope was relayed intact (risk `medium`).
- User decision: **declined for this candidate only** (`Skip this time`). The
  captured decline invocation was executed once; response validated as
  `action: "declined"`, `consent: "declined_this_candidate"`, `lenses_required: false`,
  `correction_budget: 0`. No review record, receipt, or authority was created, so
  this candidate is unmanaged and delivery stays under ordinary repository policy.
- Post-decline assessment (`gentle-ai review assess --json` with the untracked
  declaration): risk `medium`, reason `executable_change` (`web/js/common.js`),
  `review_due: true`, `review_due_reason: slice_budget_reached`. The first assess
  call without the declaration returned `unassessable`/`high`; the determinate
  medium result is the one recorded.
- Verification for this tier: writer self-verification (observed RED -> GREEN on
  `web/js/cue_sequencer_test.mjs`) plus parent spot check
  (`node --test web/js/*.mjs` -> tests 9 / pass 9 / fail 0). No separate verifier
  was run: the writer used the default runtime model, not a small-model profile.
- Next candidate (a later commit or PR slice) asks again and starts a fresh
  transaction; this declined target is not re-opened.
