# PLAN — TikTok Live Chat Trivia

A live trivia game where TikTok LIVE viewers answer by commenting `A`, `B`, `C` or `D`.
The backend counts votes from the live chat, awards speed bonuses, and a static SPA
served by FastAPI acts as the **host console** plus an **OBS overlay**.

- **Status:** agreed, ready for implementation
- **Python:** 3.11.9
- **Players:** TikTok LIVE chat (no player-facing web app)
- **SPA audience:** the host / streamer (control) and OBS (overlay)

---

## 1. Upstream dependency

Trivia consumes the read API of the existing `tiktoklives_back` service (Phase 2,
already validated live). It is an **external dependency**, not part of this repo.

```
GET {LIVES_API_BASE_URL}/api/v1/lives/{room_id}/events
    ?since={event_id}
    &types=comment
    &limit=200
```

Response (verified against the running service):

```json
{
  "events": [
    {
      "event_id": 21594,
      "type": "comment",
      "ts": "2026-09-26T21:48:13.184305",
      "user": {
        "unique_id": "riotxss",
        "nickname": "🫯.",
        "avatar": "https://p16-common-sign.tiktokcdn.com/...&x-expires=1790629200&x-signature=..."
      },
      "data": { "message": "!y wazaberto?" }
    }
  ],
  "next_cursor": 22519,
  "has_more": true
}
```

Default `LIVES_API_BASE_URL=http://127.0.0.1:8081`.

The same logical comment can be re-ingested under a **new `event_id`** (observed:
`21595` and `21602` carry the same user, message and `ts`). `event_id` is therefore
*not* a reliable logical-comment identity — per-user-per-question dedup is what
guarantees vote integrity.

---

## 2. Measured findings (200 real comments, 2026-09-26 live)

Every number below was measured by fetching the live endpoint, not assumed.

| Metric | Value |
|---|---|
| Comments sampled | 200 |
| Messages that are exactly `A`/`B`/`C`/`D` | **0** |
| Messages containing **any** `[A-Da-d]` character | **200 (100%)** |
| Messages with a capital A–D inside a word | 0 |
| Messages starting with `!` | 73 (36.5%) |
| Unique users | **28** |
| Top user (`banano_6664`) | **93 / 200 (46.5%)** |
| Top 3 users combined | **68.5%** |
| Distinct messages | 71 |
| `has_more` at `limit=200` | `true` (next_cursor 22519) |

### 2.1 Consequences

**a) Substring matching is unusable.** 100% of real messages contain an A–D
character. `"jacinta aceptas tarjeta"` would vote A, B and C at once. The vote
rule must be **anchored**, not searched.

**b) Spam is the dominant problem, not message format.** One user produced 46.5%
of the chat. Without a **one-vote-per-`unique_id`-per-round** rule, that single
account decides every question by repeating one letter. The tally therefore
reports `unique_voters`, never raw message count.

**c) `!` is already taken.** Real traffic uses `!` as a host command prefix
(`!jacinta`, `!paisana`, `!cuanto por 100 fronas`, `! me regala algo`). A `!A`
vote convention would collide with existing commands. **A vote is a bare letter**;
`!` is tolerated only as surrounding decoration.

**d) Pagination must be drained.** `has_more` is `true` even at `limit=200`. The
poller follows `next_cursor` until `has_more` is false, otherwise tallies are
systematically undercounted.

**e) Retracted hypothesis.** Mojibake was suspected (a capital `A` inside
`"chupetA-n"`). The `\w[A-D]\w` count of 0 disproved it — that was a PowerShell
console rendering artifact. The JSON is clean UTF-8. Do not claim encoding
corruption without inspecting codepoints.

**f) `ts` is naive** (no timezone): `2026-09-26T21:48:13.184305`. `tiktoklives_back`
already suffered a tz-aware/naive comparison crash in batch ingest. This plan
avoids timestamps for logic entirely (see §6.1) and normalizes to UTC aware
anyway (see §7.1).

### 2.2 Avatar URLs are signed and expire

Measured on 2026-09-30:

```
x-expires=1790629200 -> 2026-09-28T21:00:00Z   (already expired)
x-expires=1790632800 -> 2026-09-28T22:00:00Z   (already expired)
direct GET -> HTTP 403 Forbidden
```

Avatar URLs carry `x-expires` + `x-signature` and are rejected after expiry (and
often on hotlink protection). **The raw URL must never be persisted or embedded
directly.** Avatars are downloaded to `web/avatars/{unique_id}.webp` while the
round is live, and the UI falls back to initials on failure.

---

## 3. Design decisions

| Decision | Rationale |
|---|---|
| **Strict anchored vote regex** `^\W*([ABCDabcd])\W*$` | 0 false positives on 200 real messages. Accepts `A`, ` a `, `A?`, `!a`, `🔥B`. Rejects `Hola`, `A B`, `ABCD`, `chupetA-n`. |
| **One vote per (`round_id`, `unique_id`)** | Anti-spam. 46.5% of chat from a single account. |
| **`first_by_option` = first to answer that option, correct or not** | Explicit requirement: the first person who answered each option gets their avatar beside that option. |
| **Speed bonus applies only to correct answers** | The badge is visual; it does not award points. Wrong answers score 0 regardless of speed. |
| **Question window keyed on `event_id` cursor, not `ts`** | Deterministic, clock-independent, restart-safe. Avoids the aware/naive trap. |
| **Avatars cached on ingestion, fail-soft** | Signed URLs expire. Cache while the round is live; never persist the signed URL. |
| **`correct` stored as a letter, not an index** | Removes the whole class of "index/letter mismatch" bugs. |
| **Full async stack** (`aiosqlite` + `httpx.AsyncClient`) | The per-round poller is naturally async. Also removes the `check_same_thread=False` footgun entirely. |
| **Control and overlay are separate routes** | OBS gets a transparent, `pointer-events: none` surface with no controls. |
| **No WebSocket** | The host console and overlay poll `tally` every 1s. 1s latency on a screen the host narrates over is free; it avoids all connection/reconnect complexity. |

---

## 4. Domain (pure, no framework imports)

### 4.1 `domain/vote.py`

```python
from __future__ import annotations
import re
from enum import Enum

VOTE_RE = re.compile(r"^\W*([ABCDabcd])\W*$")


class OptionLetter(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


def parse_vote(s: str) -> OptionLetter | None:
    m = VOTE_RE.match(s or "")
    if not m:
        return None
    return OptionLetter(m.group(1).upper())
```

Why the anchor rejects `ABCD`: `\W*` cannot match `B` (a word character), so the
trailing anchor fails. One message cannot vote four times.

### 4.2 `domain/question.py`

```python
from __future__ import annotations
from pydantic import BaseModel, field_validator
from .vote import OptionLetter

DIFF_BASE = {"easy": 100, "medium": 150, "hard": 200}


class Question(BaseModel):
    id: int
    text: str
    category: str | None = None
    difficulty: str = "medium"  # easy | medium | hard
    options: tuple[str, str, str, str]  # EXACTLY 4, positional == A, B, C, D
    correct: OptionLetter
    explanation: str | None = None

    @field_validator("options")
    @classmethod
    def _exactly4(cls, v: tuple[str, str, str, str]) -> tuple[str, str, str, str]:
        if len(v) != 4:
            raise ValueError("Question must have exactly 4 options")
        return v
```

The seed loader **rejects boot** if any question lacks exactly 4 options.

### 4.3 `domain/session.py`

```python
from __future__ import annotations
from datetime import datetime, timezone
from pydantic import BaseModel, field_serializer
from .vote import OptionLetter


class FirstVoter(BaseModel):
    unique_id: str
    nickname: str
    avatar_path: str | None  # e.g. "/avatars/riotxss.webp", or None
    event_id: int
    ts_epoch_us: int         # UTC microseconds


class RoundTally(BaseModel):
    counts: dict[OptionLetter, int]
    unique_voters: int
    first_by_option: dict[OptionLetter, FirstVoter]


class Round(BaseModel):
    id: int
    show_id: int
    question_id: int
    status: str  # open | closed | revealed
    opened_at: datetime
    closed_at: datetime | None = None
    opened_cursor: int | None = None
    closed_cursor: int | None = None
    question_time_limit_s: int = 10
    correct_option: OptionLetter | None = None
    tally: RoundTally | None = None

    @field_serializer("opened_at", "closed_at")
    def _ser_dt(self, v: datetime | None) -> str | None:
        if v is None:
            return None
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        else:
            v = v.astimezone(timezone.utc)
        return v.isoformat().replace("+00:00", "Z")
```

---

## 5. Scoring

**Points are awarded only for correct answers.** Being the first to answer an
option grants no points; that badge is purely visual.

```
limit_ms   = round.question_time_limit_s * 1000
base       = DIFF_BASE[question.difficulty]        # 100 / 150 / 200
time_left  = max(0, limit_ms - elapsed_ms)
ratio      = time_left / limit_ms                  # 1.0 = instant, 0.0 = at the buzzer
bonus      = int(base * 0.50 * ratio)              # 0 .. 50% of base
points     = base + bonus                          # correct
points     = 0                                     # incorrect
```

`elapsed_ms = (answer_ts_utc - opened_at_utc)` clamped to `[0, limit_ms]`.

Rank-based speed tiers were considered and rejected: they depend on how many
people voted, so a round with three voters would hand out the same spread as a
round with three hundred. A time-based ratio is stable under variable
participation.

Arrival order is already available via `event_id`, so a "first correct answer
wins the round" tiebreaker is a future one-line change if wanted.

---

## 6. Question window

### 6.1 Cursor-bounded, not clock-bounded

Each round stores `opened_cursor` (highest `event_id` ingested when the round
opened) and `closed_cursor`. The accepted window is:

```
(opened_cursor, closed_cursor]
```

No timestamp comparison is involved in vote acceptance. This is deterministic,
clock-independent, survives app restarts, and sidesteps the aware/naive datetime
bug class entirely.

### 6.2 Dedup

`UNIQUE (round_id, unique_id)` on `answers`. First valid vote wins; later votes
from the same user in the same round are ignored. This is what neutralizes the
46.5%-of-chat spammer and the re-ingested duplicate comments.

---

## 7. Ports (swap boundaries)

```python
# ports.py
from __future__ import annotations
from datetime import datetime
from typing import Protocol


class LiveComment(Protocol):
    event_id: int
    ts_utc: datetime        # aware, UTC
    unique_id: str
    nickname: str
    avatar_url: str | None


class LiveCommentsClient(Protocol):
    async def fetch_since(
        self, room_id: str, since_event_id: int, types: tuple[str, ...] = ("comment",)
    ) -> list[LiveComment]:
        ...


class QuestionRepo(Protocol): ...
class SessionRepo(Protocol): ...
```

`LiveCommentsClient` is the port that matters: swapping REST polling for a direct
TikTok WebSocket later means implementing this Protocol and nothing else changes.

---

## 8. Infrastructure

### 8.1 `infra/tiktok_live.py`

Implements `LiveCommentsClient` over the `tiktoklives_back` read API.

- Fetches with `since=<event_id>`, follows `next_cursor` while `has_more` is true.
- Converts naive `ts` to aware UTC: `dt.replace(tzinfo=timezone.utc)`.
- `limit=200` per page.

### 8.2 `infra/avatar_cache.py`

```python
from __future__ import annotations
from pathlib import Path
import httpx

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


async def cache_avatar(
    client: httpx.AsyncClient, unique_id: str, url: str | None, out_dir: Path
) -> str | None:
    if not url:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{unique_id}.webp"
    if path.exists():
        return f"/avatars/{path.name}"
    try:
        resp = await client.get(
            url, timeout=5.0, follow_redirects=True, headers={"User-Agent": _UA}
        )
        if resp.status_code == 200 and resp.content:
            path.write_bytes(resp.content)
            return f"/avatars/{path.name}"
    except Exception:
        pass  # fail-soft: avatar must never break vote ingestion
    return None
```

Rules: idempotent (existing file short-circuits the GET), never raises, never
blocks vote parsing, never persists the signed URL.

### 8.3 `db.py` / `models.py`

Async engine, so no `check_same_thread` concern:

```python
engine = create_async_engine("sqlite+aiosqlite:///./trivia_game.db", echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
```

Model additions beyond the obvious:

- `Round.opened_cursor` (int, null), `closed_cursor` (int, null)
- `Round.question_time_limit_s` (int)
- `Round.correct_option` (str(1), null) — `A`..`D`
- `Round.tally_json` (JSON, null)
- `Answer.event_id` (int, indexed), `option` (str(1)), `correct` (bool), `points` (int), `elapsed_ms` (int)
- `UNIQUE (round_id, unique_id)` on `answers`
- `User.avatar_path` (str, null) — local served path, never the signed URL

Migrations via `create_all` on lifespan startup. Alembic is the upgrade path, not
an immediate need; tables live in one place, so adopting it later is cheap.

---

## 9. Services

`services/session_service.py` owns the round lifecycle.

**`open_round(show_id, question_id)`** — set `status=open`, `opened_at=now(UTC)`,
`opened_cursor = highest event_id ingested so far`.

**Vote ingestion (per new event, `event_id > last_ingested`)**
1. `parse_vote(event.data.message)` — filters the 100% noise rate down to 0.
2. Skip if an `Answer` already exists for `(round_id, unique_id)`.
3. Persist `Answer` with `event_id`, `option`, `elapsed_ms` clamped to the limit.
4. On a newly seen `unique_id`, opportunistically cache the avatar
   (`cache_avatar`, fail-soft). Repeat comments from a known user skip the fetch.

**`close_round(round_id)`** — drain pending events first, then freeze:
- `status=closed`, `closed_at=now(UTC)`, `closed_cursor = highest ingested event_id`
- `counts[opt]` = number of unique voters who chose `opt`
- `unique_voters` = distinct voters in the round
- **`first_by_option[opt]` = the earliest `Answer` with `option == opt`, by
  `event_id` (tiebreak `ts_epoch_us`), regardless of `is_correct`**
- compute and persist `points` per answer per §5

**`reveal_round(round_id)`** — set `correct_option`, `status=revealed`.

**Tally is immutable after `close_round`.** Late events never alter a frozen tally.

### 9.1 Poller supervisor

Lifespan-managed asyncio task per open round:
- Poll every ~0.9s (never below 0.7s — no need to hammer the upstream).
- Track and advance `last_ingested_event_id`.
- On close, perform one final drain so votes arriving between the last poll and
  the close are not lost.
- Network errors: log and back off (1.0s → 1.5s → 2.0s cap). Never kill the round.

---

## 10. API

Prefix `/api/v1`. No auth — the service binds `127.0.0.1` by default. See §12.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/shows` | Create show `{room_id, question_count, question_time_limit_s?}` |
| `GET` | `/shows/{id}` | Show state + current round |
| `POST` | `/shows/{id}/rounds` | Open the next question (host action) |
| `GET` | `/rounds/{id}` | Round state (status, opened_at, cursors) |
| `GET` | `/rounds/{id}/tally` | Live tally, polled every 1s by control and overlay |
| `POST` | `/rounds/{id}/close` | Drain, freeze tally, persist points |
| `POST` | `/rounds/{id}/reveal` | Set `correct_option`, mark revealed |
| `GET` | `/shows/{id}/ranking` | Leaderboard by points / correct count |
| `GET` | `/questions` | Seeded question catalog |
| `GET` | `/healthz` | Health check |
| `GET` | `/avatars/{filename}` | Static avatar files |

**Tally contract** — the field is `first_by_option` (keyed `A`..`D`). Each key is
present only if somebody chose that option:

```json
{
  "counts": { "A": 8, "B": 3, "C": 1, "D": 0 },
  "unique_voters": 12,
  "first_by_option": {
    "A": {
      "unique_id": "riotxss",
      "nickname": "🫯.",
      "avatar_path": "/avatars/riotxss.webp",
      "event_id": 21594,
      "ts_epoch_us": 1790634493184305
    }
  }
}
```

`avatar_path` is `string | null`. The frontend falls back to initials on `null`
or on a failed image load.

---

## 11. SPA (static, no build step)

Served with `app.frontend("/", directory="web", fallback="index.html")` —
requires **FastAPI >= 0.141.0**, which introduced `app.frontend()` and its
`check_dir="auto"` mode. This replaces the old `StaticFiles(html=True)` mounted at
`/`, which swallows 404s and competes with API routes. `check_dir="auto"` also
skips the directory existence check under `FASTAPI_ENV=development`, so `fastapi dev`
boots before `web/` is complete.

Vanilla HTML/CSS/ES modules. History API routes: `/` (→ `/control`), `/control`, `/overlay`.

### 11.1 `/control` — host console
Question picker, open round, countdown, live tally bars with per-option counts,
and the first-responder avatar beside each option. Buttons: Open, Close, Reveal, Next.
Polls `GET /rounds/{id}/tally` every 1000ms.

### 11.2 `/overlay` — OBS surface
Transparent background, `pointer-events: none`, absolutely positioned layout.

- Question text plus four option boxes (A–D).
- Percentage bar per option.
- **The first responder's avatar is rendered beside the box of the option they
  chose**, tucked against that box's edge. Placement alternates by side
  (A left, B right, C left, D right) to avoid overlap.
- `avatar_path` is used when present; otherwise a circle with the first character
  of `nickname`, or of `unique_id` if there is no nickname.
- On `status=revealed`, the correct option is highlighted and the first-responder
  avatars stay in place.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| **Avatar expires before we cache it** | Cache during the live at ~0.9s poll cadence. Fail-soft to `None` → initials. Never blocks vote ingestion. |
| **Disk / permissions (Windows)** | `mkdir(parents=True, exist_ok=True)`; blanket `except`; avatar failures never propagate. |
| **CDN rate limiting / 403** | 5s timeout, real browser UA, `follow_redirects=True`, existing file short-circuits the GET. |
| **Votes lost at close** | Final drain before freezing the tally. |
| **Duplicate / re-ingested events** | Cursor window + `(round_id, unique_id)` unique constraint + `event_id <= last_ingested` skip. |
| **Naive/aware datetime crash** | Window keyed on `event_id`; `ts` normalized to aware UTC at the boundary. |
| **Unauthenticated exposure** | Bind `127.0.0.1` by default. Opening a round / closing a round is unauthenticated, so exposing this beyond loopback would let anyone manipulate the game. Same hardening posture already used in `tiktoklives_back`. |
| **Systematic undercount** | Drain `next_cursor` until `has_more` is false, not a single page per tick. |

---

## 13. Tests

| Test | Asserts |
|---|---|
| `tests/domain/test_vote.py` | The 200 real messages yield **0** votes. Accepts `A`, ` a `, `A?`, `!a`, `🔥B`. Rejects `Hola`, `A B`, `ABCD`, `chupetA-n`, `""`, `None`. |
| `tests/services/test_first_by_option_any_correctness.py` | An **incorrect** responder with the lowest `event_id` on A becomes `first_by_option["A"]`. A correct responder arriving later on A does **not** replace them. |
| `tests/services/test_first_by_option_earliest.py` | Among several voters on the same option, the lowest `event_id` wins regardless of correctness; `ts_epoch_us` breaks ties. |
| `tests/services/test_dedup_per_user.py` | One user sending `A`, `A`, `B` counts as a single vote. |
| `tests/services/test_speed_bonus.py` | Bonus is 0–50% of base, scaled by remaining time. Incorrect answers score 0. First-responder status grants no points. |
| `tests/services/test_cursor_window.py` | `(opened_cursor, closed_cursor]` excludes events at or below `opened_cursor`. |
| `tests/services/test_avatar_fallback.py` | Expired/invalid URL → `None` → UI falls back. Existing file → cached path returned without a network call. |
| `tests/api/test_round_close_freezes_tally.py` | After `POST /rounds/{id}/close`, `counts` and `first_by_option` never change, even as new events arrive. |
| `tests/api/test_tally_shape.py` | `tally` exposes `first_by_option` keyed A–D with nullable `avatar_path` and the required fields. |

The golden fixture `tests/fixtures/real_chat_sample.json` holds the 200 real
messages. It is the highest-value test in the project: it is what protects the
strict-anchor decision from silent regression.

---

## 14. Execution order

1. `requirements.txt` — `fastapi>=0.141.0`, `uvicorn[standard]`, `pydantic-settings`, `sqlalchemy[asyncio]`, `aiosqlite`, `httpx`; dev: `pytest`, `pytest-asyncio`, `respx`.
2. **Domain + vote tests.** Write `tests/fixtures/real_chat_sample.json` and `tests/domain/test_vote.py` first. Green before anything else exists.
3. **Infra + async DB** — `db.py`, `models.py`, `infra/tiktok_live.py`, `infra/avatar_cache.py`.
4. **Services** — `services/session_service.py` with `first_by_option`, dedup, speed bonus, frozen tally.
5. **API + poller** — `api/`, `deps.py`, `main.py` with lifespan and poller supervisor; mount `web/` via `app.frontend()`.
6. **SPA** — `web/index.html`, `web/styles.css`, `web/js/`, plus `web/avatars/.gitkeep`.
7. **E2E smoke** — open a round, inject synthetic events (including: wrong answer first on A, correct answer first on B), close, verify `first_by_option` and avatars with and without cache hits.

---

## 15. Assumptions

- **The badge is the first responder per option, correct or incorrect** — per the
  explicit change to the original "first correct" requirement.
- **Speed bonus applies only to correct answers.** The badge is visual only.
- **Exactly 4 options per question**, labelled A–D positionally. Enforced at seed
  load time; boot fails otherwise.
- **A vote is a bare letter.** `!A` is *not* the vote syntax, because `!` collides
  with the host's existing chat commands in real traffic.
- **1s polling** for both control and overlay. No WebSocket.
- **Avatars degrade to initials** on any failure.
- **Round time limit** is configurable, default 10s.
- **No authentication**; loopback bind is the mitigation.

---

## 16. Open questions

None blocking implementation. Optional follow-ups, in priority order:

1. **Player display name.** Leaderboard currently keys on `unique_id` with the
   comment's `nickname` and cached avatar. A separate player name is not required
   by the current spec.
2. **Round-level winner.** A "fastest correct answer" badge is a one-line change
   (`first_by_option[correct_option]`) but is not currently part of scoring.
3. **OBS layout.** Exact avatar placement (which edge, pixel offset, size) still
   needs a real look against a 1080p stream before it is final.
