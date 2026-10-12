# First-correct-responder toast

## Objective

On the reveal frame of the OBS overlay, announce the FIRST viewer who answered
correctly, with avatar, nickname, the points that answer earned, and that
viewer's running total for the show.

## Problem

The reveal scene already knows who answered each option first
(`first_by_option` in the tally payload) but not what it was worth:

- `Answer.points` is computed and persisted when the round closes
  (`app/services/session_service.py`), and `build_tally` runs *after* scoring —
  yet the frozen `tally_json` never copies the value out.
- `RankingEntry.points` is the whole-show total, a different number from the
  award for this question.

So a viewer who answered first correct is visible as an avatar badge but their
reward is not. The punchline of the whole round is missing.

## Why now

Every scoring value is already persisted at close time; nothing new needs to be
invented, only surfaced. The overlay has no toast primitive yet, so this change
also establishes one.

## Scope

In scope:

1. `points` and `total_points` on the first responder per option.
2. A toast in the reveal scene showing avatar, nickname, `+points` and total.
3. Tests for both halves.

Out of scope: any change to the scoring formula, to the option badges, to the
final podium, or to the host console.

## Constraints

- `first_by_option` is part of the polled public contract
  (`app/api/schemas.py` docstring). New fields are additive; nothing renames.
- `build_tally` also serves the LIVE tally while a round is open
  (`app/api/routes/tally.py`, `app/api/routes/rounds.py`). There, answers are
  not scored yet, so `points` is legitimately `0`. Do not fake a value.
- The overlay polls every 1s and `render()` rebuilds its DOM on every tick. A
  toast whose entrance animation replays each second is unusable: the animation
  must trigger only when the announced identity changes.
- No git repository exists in this directory, so there is no work-unit commit;
  delivery is the file change itself.

## Tasks

- [x] T1 — Surface the numbers in the tally contract.
      Add `points` (this answer) and `total_points` (running show total for that
      viewer) to `FirstVoterOut`, and populate both in `build_tally`. The total
      needs one grouped query over the show's answers filtered to the at most
      four first responders — no per-user query.
- [x] T2 — Extract a pure selector for the announced winner.
      Put it in `web/js/common.js` next to `currentRound`, returning the first
      responder for `correct_option` or null when the round is not revealed.
      Pure and DOM-free so it is unit-testable, mirroring why `currentRound`
      lives there.
- [x] T3 — Render the toast on the reveal frame.
      Markup in `web/overlay.html`, DOM in `web/js/overlay.js`, styles in
      `web/overlay.css`. Show it only when a first correct responder exists,
      never under the final podium.
- [x] T4 — Tests.
      pytest for the two new tally fields (0 while open, awarded after close,
      total spanning previous rounds); node test for the selector.

## Authorized edit surfaces

```
app/services/session_service.py
app/api/schemas.py
web/js/common.js
web/js/overlay.js
web/overlay.html
web/overlay.css
tests/integration/test_session_flow.py
web/js/first_correct_test.mjs
```

## Acceptance criteria

1. Reveal frame with a first correct responder shows avatar, nickname, the points
   that answer earned, and the viewer's show total.
2. No correct responder, or the round is not revealed: no toast.
3. `points` is `0` while the round is open — never a guess.
4. Switching rounds replaces the toast content and re-runs the animation once,
   not once per second.
5. `venv\Scripts\python.exe -m pytest -q` and `node web/js/first_correct_test.mjs`
   pass, plus the pre-existing `node web/js/current_round_test.mjs`.

## Route

Delegated writer (one). Six implementation files plus two test files, each a
non-trivial edit — over the inline threshold. Parent verified the data gap and
resolved the score-definition fork with the user before delegation.

## Verification

- `venv\Scripts\python.exe -m pytest -q`
- `node web/js/first_correct_test.mjs`
- `node web/js/current_round_test.mjs`

## Progress

All four tasks implemented. No change fell outside the authorized surfaces.

- Contract gap confirmed before starting: `points` absent from `FirstVoterOut`,
  never copied into the frozen `tally_json`.
- Score definition decided with the user: award for this answer AND running
  show total.

### What landed

- `FirstVoterOut` gains `points: int = 0` and `total_points: int = 0`. The
  default is deliberate: it is the true value while a round is open, and it
  keeps a `tally_json` frozen by an older build validating against the contract.
- `build_tally` copies `answer.points` per first responder and fills
  `total_points` from `_show_totals_by_user_id`: one grouped `sum` joining
  `Answer` to `Round` on `rnd.show_id`, filtered to the ≤4 first responders.
  The internal letter → user_id map stays out of the payload, which speaks
  `unique_id`, not primary keys.
- `firstCorrectResponder` / `firstCorrectKey` in `web/js/common.js`, next to
  `currentRound`, for the same testability reason.
- The reveal toast lives in `#o-first-correct`, with `#o-final` hiding it when
  the show finishes.

### Flicker guard

`renderFirstCorrect` rebuilds the card only when
`firstCorrectKey(round, winner)` changes. Every other 1s poll returns at the key
comparison, so the CSS entrance animation runs once per question instead of
once per second. `hideFirstCorrect` clears the key, which is what guarantees the
next announcement animates.

### Verification

| Command | Result |
| --- | --- |
| `venv\Scripts\python.exe -m pytest -q` | 163 passed, 1 warning (baseline 157, +6 new) |
| `node web/js/first_correct_test.mjs` | all passed, exit 0 (12 checks) |
| `node web/js/current_round_test.mjs` | all passed, exit 0 (pre-existing) |
| `ruff check` on the touched files | clean except a pre-existing repo-wide `I001` |

RED was observed before implementation: `6 failed` with `KeyError: 'points'` and
`AttributeError: 'FirstVoterOut' object has no attribute 'points'`, plus the
node test failing on the missing module export.

One defect was introduced and fixed mid-task: an edit left a duplicated
unreachable `return` block in `build_tally`. Tests stayed green — the names
resolve only at runtime — and `ruff` reported it as `F821`. Removed.

### Open risks

1. Autoflush is load-bearing. `build_tally` sees the pending `points` writes
   only because the leading `select` flushes them. A session configured
   `autoflush=False` would silently read `0` on the reveal frame;
   `test_points_carry_the_award_after_close` fails loudly if that happens.
2. The wire payload is not asserted end to end. `tests/integration/test_api_flow.py`
   was outside the authorized surfaces; the drop risk is covered by an in-file
   `TallyOut(**tally_json)` round-trip test instead.
3. Visual layout and the single-per-question animation are verified by reading,
   not by machine. A live reveal frame in OBS is the remaining manual step.

### Delivery

No git repository exists in this directory, so there is no work-unit commit and
no review candidate. Delivery is the working-tree change itself.