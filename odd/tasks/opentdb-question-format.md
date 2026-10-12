# OpenTDB question format

## Objective
Load the question bank from the Open Trivia DB envelope shape (`response_code` + `results`), extended with a per-question `explanation` field, while keeping the app's unchanged four-option `Question` contract.

## Problem
`app/infra/seed.py` only understands a bare JSON array with `id` / `text` / `options` / `correct`. OpenTDB ships a different shape: a top-level object with `response_code` and a `results` array whose entries carry `type`, `difficulty`, `category`, `question`, `correct_answer`, and `incorrect_answers`. The loader cannot read it, so the bank must be converted and the loader rewritten.

## Why
The host wants to drop OpenTDB-shaped JSON into the game. The current custom format is being retired in favour of this shape, with `explanation` kept as the single project extension.

## Scope
- **In:** `app/infra/seed.py` (loader + writer), `data/questions.json` (converted, content preserved), new focused tests.
- **Out:** DB models, scoring, HTTP routes, host console, OBS overlay.

## Constraints and decisions
- **Exactly 4 options stay mandatory.** The domain model enforces a 4-tuple and letters A–D. `type: "boolean"` (2 options) is rejected with a clear `QuestionBankError`, not silently padded.
- **No `id` in OpenTDB.** Derive `id = position + 1` so the shipped bank keeps its current ids and the boot upsert updates rows in place instead of duplicating.
- **Decode HTML entities** (`html.unescape`) on question text, answers, category, and explanation. OpenTDB returns `&quot;`, `&#039;`, `&amp;`, `&aacute;`, `&rsquo;`.
- **Ordering.** OpenTDB lists `correct_answer` first; left as-is the correct choice would always be option A. Shuffle the four options **deterministically** (seed = `sha256(question_text + "\0" + correct_answer)`) so layouts are stable across restarts and tests, and the correct letter varies across questions.
- **`explanation` is the one extension** over the standard OpenTDB result object, preserved when present.
- Bootstrap regex/vote rules, scoring, and the `show_runner` are untouched.

## Tasks
- [x] **T1** — Rewrite the loader to read the OpenTDB envelope: derive id from position, map `question` → text, `correct_answer` + `incorrect_answers` → shuffled 4-tuple + letter, unescape HTML, pass `explanation` through, reject booleans and malformed rows with `QuestionBankError`.
- [x] **T2** — Update `save_questions_to_file` to emit the same envelope so the format round-trips.
- [x] **T3** — Convert `data/questions.json` to the OpenTDB envelope with the existing 15 questions, preserving order and their explanations.
- [x] **T4** — Add focused unit tests: envelope parsing, HTML decode, deterministic shuffle, id-from-position, explanation passthrough, boolean rejection, wrong-length rejection, non-envelope rejection, save/load round-trip, and the shipped bank still loads.

## Progress
- T4 (tests, TDD RED) done first: `tests/unit/test_seed.py`, 9 failed / 5 passed before the loader change.
- T1 loader rewritten (`app/infra/seed.py`): OpenTDB envelope, id-from-position, HTML unescape, deterministic shuffle, explanation passthrough, hard row errors.
- T2 writer emits the same envelope; T3 shipped bank converted (15 questions, order/content preserved).
- Focused suite GREEN: `14 passed`.
- Round-trip needs the pre-shuffle list to be canonical: the loader sorts the four options before the seeded `random.Random.shuffle`, so the result depends only on the option set, not on file order. Without this, `save` → `load` reorders options and the round-trip test fails.

## Verification
- `venv\Scripts\python.exe -m pytest tests/unit/test_seed.py -q`
- `venv\Scripts\python.exe -m pytest -q` (baseline before this change: 181 passed)

## Next step
Wire the new file in and confirm the app boots and seeds without error (`app/infra/seed.py` is the only consumer).
