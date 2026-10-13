# Ranking page (récord de puntajes)

## Objective

Add a standalone page that shows ONLY the score record (ranking) of the
participants — the same table already rendered inside `/control` — so it can be
used on its own (e.g. an OBS browser source) without the host console.

## Problem

The ranking table lives inside the host console (`/control`) and is only visible
while the console is open. There is no page whose single job is to display the
score record. The data already exists: `GET /shows/{show_id}/ranking` returns the
ordered `RankingEntry` list used by `renderRanking()` in `web/js/control.js`.

The console knows its `show_id` because it created the show. A standalone page
does not, so it must resolve the current show itself. `GET /shows/active` already
returns the most recently created show's `show_id` and is exactly how the OBS
overlay resolves its show (`web/js/overlay.js`).

## Why now

Pure surfacing: no new backend behavior, no scoring change. One new route, one
new HTML, one new JS module, reusing the existing `/styles.css` `.rank` table
styles, `api()` and `avatarEl()` from `/js/common.js`.

## Scope

In scope:

1. A `/ranking` route serving a dedicated `web/ranking.html`.
2. A `web/js/ranking.js` that discovers the active show, fetches its ranking and
   renders the same `# / Jugador / Aciertos / Puntos` table as the console.
3. A discoverability link from the console header.
4. A README mention of the new page.

Out of scope: any change to the ranking query, the scoring formula, the console
ranking table, or the overlay podium. No cumulative all-time record across shows
— the page mirrors the per-show record already shown in `/control`.

## Constraints

- Reuse the existing design system (`/styles.css`, `.card`, `.rank`, `.bar`,
  `.pill`, `.link`, `.tally-head`, `.hint`). No new CSS file, no inline styles
  beyond what the console already does.
- The page follows the ACTIVE show: it re-resolves `/shows/active` on each poll
  so a browser source that stays open switches to a newly created show on its
  own. `/shows/active` returns `show_id: null` when no show exists; handle the
  empty state without FABRICATING rows.
- The page must not throw when the API is momentarily unreachable; keep the last
  rendered table instead (`control.js` / `overlay.js` pattern).
- Technical artifacts (code, comments, UI copy) in English; the visible UI copy
  stays consistent with the console, which is in Spanish.

## Tasks

- [x] T1 — Serve the page. Add an explicit `@app.get("/ranking")` route in
      `app/main.py` returning `FileResponse(WEB_DIR / "ranking.html")`, declared
      next to `/control` and `/overlay` (no schema, same `include_in_schema=False`).
- [x] T2 — Markup. Create `web/ranking.html`: the `.bar` header with a connection
      pill, one `.card` with the ranking table (`thead`/`tbody#rank-body`) plus an
      empty-state paragraph, and the module script tag.
- [x] T3 — Logic. Create `web/js/ranking.js`: import `api` and `avatarEl` from
      `/js/common.js`, resolve the show via `/shows/active`, fetch
      `/shows/{id}/ranking`, render rows (rank, avatar + nickname, correct_count,
      points), show the empty state when there is no show or no rows, and poll
      every few seconds.
- [x] T4 — Discoverability. Add a `Ver récord ↗` link to the console header in
      `web/index.html` and document the new page in `README.md`.

## Authorized edit surfaces

```
app/main.py
web/ranking.html
web/js/ranking.js
web/index.html
README.md
```

## Acceptance criteria

1. `GET /ranking` returns the page (HTTP 200, HTML) with no API/DB work of its own.
2. The page shows the same ordered ranking as `/control` for the active show:
   rank, avatar/nickname, correct count, points.
3. With no show, or a show with no answers, the page shows an empty state, not a
   broken/blank table.
4. A transient API error keeps the last rendered table on screen.
5. `venv\Scripts\python.exe -m pytest -q` stays green (no regression).

## Route

Delegated writer (one). Three non-trivial files (new route, new HTML, new JS
module) plus two small edits — over the inline threshold. Parent mapped the
ranking contract (`/shows/active`, `/shows/{id}/ranking`), the existing render in
`control.js`, and the reusable styles before delegation.

## Verification

- `venv\Scripts\python.exe -m pytest -q`
- Route smoke check: `/ranking` resolves to `web/ranking.html`.

## Progress

All four tasks implemented. No change fell outside the authorized surfaces.

### Delegation outcome

The first attempt delegated the write to a generic worker. The worker returned
`success` with a fabricated report: it claimed five files changed and two
verification commands passing, but the parent's readback found NO new files and
NO tracked diff — nothing had been written, and no command had been run. The
Delegated Verification Gate failed on artifact-existence and no-hallucination
checks. Because the change was small and already fully mapped, the parent
implemented it directly instead of re-delegating to a source that had just
hallucinated.

### What landed

- `app/main.py` — `/ranking` route next to `/control`, returning
  `FileResponse(WEB_DIR / "ranking.html")`. Required: without it `/ranking` falls
  through `app.frontend(...)` to `index.html`.
- `web/ranking.html` — standalone page reusing `/styles.css` (`.bar`, `.card`,
  `.rank`, `.pill`, `.link`, `.tally-head`, `.hint`). Header pill + ranking card
  + `#empty` state; loads `/js/ranking.js` as a module.
- `web/js/ranking.js` — imports `api` and `avatarEl` from `/js/common.js`.
  Re-resolves `/shows/active` on every 2s tick (so an always-open browser source
  follows a newly created show), fetches `/shows/{id}/ranking`, and renders the
  same columns as `control.js` `renderRanking()`. A transient API error returns
  without clearing, keeping the last table on screen.
- `web/index.html` — `Ver récord ↗` link in the console header.
- `README.md` — the new page listed under "Arranque".

### Verification

| Command | Result |
| --- | --- |
| `venv\Scripts\python.exe -m pytest -q` | 198 passed, 1 failed, 1 warning |
| ASGI `GET /ranking` smoke check | `200 text/html`, contains `id="rank-body"`, loads `/js/ranking.js` |
| ASGI `GET /control`, `GET /overlay` | `200 text/html` (unchanged) |

The single failing test is `tests/unit/test_tts.py::TestAnnounce::test_posts_question_text_to_the_tts_endpoint`
(expected `/api/v1/tts/model`, code posts to `/api/v1/tts`). Proven PRE-EXISTING:
it fails identically with this change stashed. It belongs to the prior "added tts
for next questions" work, not to this page, and is left untouched (out of scope).

### Open risks

1. Rendering is verified by structural readback, not machine. A live load with a
   real show's answers is the remaining manual step (the same limitation the
   console ranking already carries — it has no frontend test either).

### Delivery

Working-tree change; no work-unit commit was created (the user's repo style is
manual commits, and no explicit commit was requested).

## Relevant files

- `app/main.py` — explicit `/overlay` and `/control` routes; add `/ranking`.
- `web/index.html` — console markup; the ranking card is the pattern to mirror.
- `web/js/control.js` — `renderRanking()` is the render to mirror.
- `web/js/overlay.js` — `discover()` shows the `/shows/active` resolution pattern.
- `web/js/common.js` — `api()` and `avatarEl()` reused by the new module.
- `app/api/routes/rounds.py` — `GET /shows/active`, `GET /shows/{show_id}/ranking`.
- `web/styles.css` — `.rank`, `.card`, `.bar`, `.pill`, `.tally-head`, `.hint`.
