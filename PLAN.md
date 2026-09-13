# TikTok Lives Backend — Plan & Guide

> Source of truth: `Idea.md` (three architectures). This plan implements **Method 1: HTTP Batching (Agrupación vía HTTP)** first. The TikTok client comes first; FastAPI receiver comes after the client is proven.

## 0. Goal & Scope

Backend that captures TikTok Live events and makes them shareable/consumable:

| Shareable payload | Required data |
| --- | --- |
| User + message + profile photo | `comment` event with user + `avatar` URL |
| User + message | `comment` event |
| User + gift | `gift` event |
| Like + user | `like` event |
| Follow + user | `follow` event |

**Architecture (Method 1):**
1. **Capturer (TikTokLive)**: listens to the live, normalizes events, buffers them in memory. Never sends one HTTP request per event.
2. **Timer/Size trigger**: flush the whole buffer every `BATCH_INTERVAL_SECONDS` (default 2) OR when it reaches `BATCH_MAX_EVENTS` (default 100) — whichever happens first.
3. **Receiver (FastAPI, Phase 2)**: accepts one big POST batch, stores it, returns 200.

## 1. Tech Stack

- Python 3.11+ (always in a venv)
- `TikTokLive` (isaackogan) — WebSocket capture from TikTok (reverse-engineered, needs updates when TikTok changes protocol)
- `httpx` — async HTTP POST of batches
- `pydantic` v2 — normalized event models
- `python-dotenv` — `.env` config
- Later (FastAPI): `fastapi`, `uvicorn`, `SQLAlchemy` async, `alembic` (already the user's preferred stack: layered routes → services → repositories, no `core/`)

## 2. Project Structure

```
tiktoklives_back/
├── contract/
│   └── batch.schema.json   # frozen contract — single source of truth (JSON Schema 2020-12)
├── capturer/               # PHASE 1 — TikTok capturer (its OWN venv)
│   ├── .venv/              # virtualenv (never install globally)
│   ├── .env                # local config (gitignored)
│   ├── .env.example        # documented template
│   ├── requirements.txt
│   ├── data/
│   │   └── batches/        # dry-run JSONL output (no FastAPI yet)
│   ├── client/             # Python package — internal name stays `client`
│   │   ├── __init__.py
│   │   ├── models.py       # normalized event models (pydantic v2) — capturer's copy of the contract
│   │   ├── normalizers.py  # TikTokLive event -> our models
│   │   ├── buffer.py       # BatchBuffer (timer + size flush)
│   │   ├── sender.py       # httpx POST / dry-run JSONL writer
│   │   └── main.py         # entrypoint: build client, wire listeners, run
│   └── tools/
│       └── smoke_test.py   # synthetic smoke test (no live)
└── service/                # PHASE 2 — FastAPI receiver (own venv, created later)
    └── README.md           # placeholder
```

## 3. Setup (once)

```powershell
cd tiktoklives_back
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # PowerShell
# or: .\.venv\Scripts\activate.bat    # cmd
pip install TikTokLive httpx pydantic python-dotenv
pip freeze > requirements.txt
```

## 4. Configuration (`.env`)

```env
# Target live
TIKTOK_USERNAME=@username

# Optional proxies only if needed to reach TikTok (some regions/ISPs)
TIKTOK_WEB_PROXY=
TIKTOK_WS_PROXY=

# Batching
BATCH_INTERVAL_SECONDS=2
BATCH_MAX_EVENTS=100

# Receiver. Empty/DRY_RUN=true => write JSONL to data/batches/ instead of POSTing
API_ENDPOINT=
DRY_RUN=true
```

## 5. Phase 1 — TikTok Client (current work)

### 5.1 `client/models.py` — normalized contract (pydantic v2)

```python
from pydantic import BaseModel

class UserInfo(BaseModel):
    unique_id: str | None
    nickname: str | None
    profile_picture_url: str | None   # TikTok gives a URL, not bytes

class EventEnvelope(BaseModel):
    type: str                         # "comment" | "gift" | "like" | "follow"
    timestamp: str                    # ISO 8601
    user: UserInfo

class CommentEvent(EventEnvelope):
    data: dict = {}                   # {"message": "..."}

class GiftEvent(EventEnvelope):
    data: dict = {}                   # {"gift_name", "diamonds", "repeat_count", "streak"}

class LikeEvent(EventEnvelope):
    data: dict = {}                   # {"like_count", "delta"} — delta computed client-side (see 5.4)

class FollowEvent(EventEnvelope):
    data: dict = {}                   # optional extras

class Batch(BaseModel):
    version: int = 1
    room_id: str | None
    stream_id: str | None
    started_at: str
    ended_at: str
    events: list[EventEnvelope]
```

### 5.2 `client/normalizers.py` — TikTokLive → ours

Registered listeners (verified against current docs):

| TikTokLive event | Emit as | Source fields |
| --- | --- | --- |
| `CommentEvent` | `comment` | `event.user.unique_id`, `.nickname`, `.avatar` (URL), `event.comment` |
| `GiftEvent` | `gift` | `event.user`, `event.gift.name`, `event.repeat_count`, `event.streaking`, `gift.type` |
| `LikeEvent` | `like` | `event.user`, `event.like_count` (cumulative total) |
| `FollowEvent` | `follow` | `event.user` |
| `ConnectEvent` | — (control) | sets room/stream id, resets started_at |
| `DisconnectEvent` / `LiveEndEvent` | — (control) | final flush, closes sender |

**Gift streak rule** (from docs): streakable gifts (`gift.type == 1`) fire repeatedly during the streak — emit the final one only (`not event.streaking`); non-streakable (`gift.type != 1`) emit immediately:

```python
if event.gift is None:
    return
streakable = event.gift.type == 1
if streakable and event.streaking:
    return          # wait for streak end to avoid flooding
```

### 5.3 `client/buffer.py` — `BatchBuffer`

- `add(envelope)` → append to in-memory list.
- Flush conditions (whichever first): `BATCH_INTERVAL_SECONDS` elapsed **or** list length ≥ `BATCH_MAX_EVENTS`.
- `flush()` → build `Batch` (started_at/ended_at from first/last event time), send via `Sender`, drop buffer.
- Runs as an `asyncio` task alongside the TikTokLive client loop. No FastAPI needed to test.

### 5.4 `client/sender.py` — transport

- `DRY_RUN=true` or empty endpoint → append each batch as one JSON line to `data/batches/{stream_id}.jsonl`. This is how we verify the client before FastAPI exists.
- Otherwise → single `POST {API_ENDPOINT}` with JSON body via `httpx.AsyncClient`. One request per batch, never per event.
- Failure policy (v1): 1 retry with short backoff, then drop with a logged counter. (TODO later: move to queue architecture — Method 3 from Idea.md.)

### 5.5 `client/main.py` — wiring

```python
from TikTokLive import TikTokLiveClient
from TikTokLive.events import (CommentEvent, GiftEvent, LikeEvent,
                               FollowEvent, ConnectEvent, DisconnectEvent)

client = TikTokLiveClient(unique_id=settings.tiktok_username)
# register listeners -> normalizers -> buffer
# buffer flusher task runs concurrently
# client.run()  (blocking) or await client.start()
```

Note: `LikeEvent.like_count` is a **cumulative total**, not a per-tap delta. To share "usuario del like" per unit, keep a per-user counter in the normalizer and emit `delta = like_count - previous`; emit the delta (>= 1) as one like event.

### 5.6 Verify (no FastAPI needed)

```powershell
.\.venv\Scripts\Activate.ps1
python -m client
# Watch console + data/batches/*.jsonl
# Expect: batches of comments/gifts/likes/follows, flushed every ~2s or at 100 events
```

## 6. Phase 2 — FastAPI Receiver (later, after client is proven)

- `POST /api/v1/lives/{room_id}/batches` → pydantic `Batch`, insert via service → repository, respond `200`.
- Layered structure (existing user preference): `app/api`, `app/services`, `app/repositories`, `app/schemas`, `app/models`; SQLAlchemy async + alembic migrations.
- Tables: `users`, `comments`, `gifts`, `likes`, `follows`, `streams` (room/stream metadata + batch provenance).
- Milestone: switch client `API_ENDPOINT` to FastAPI, `DRY_RUN=false`, verify end-to-end.

## 7. Known Gotchas

- Package is `TikTokLive` (not `tiktok-live-api` as written in Idea.md).
- Reverse-engineered protocol: when TikTok changes internals, update the library (`pip install -U TikTokLive`).
- Some regions/ISPs require `web_proxy`/`ws_proxy` to reach TikTok.
- Profile photo is a **URL**, not bytes — decide later whether the backend downloads/caches it.
- Likes arrive as cumulative counters; derive deltas client-side.
- Streakable gifts fire repeatedly; only emit when the streak ends.

## Progress

- [x] Setup: venv + deps + .env  (Python 3.11.9 venv, TikTokLive 7.0.1)
- [x] Repo restructure: capturer/ and service/ fully decoupled (own venvs, contract frozen as JSON Schema)
- [x] Phase 1: TikTok client (models → normalizers → buffer → sender → main)
- [x] Phase 1 verified in dry-run (synthetic smoke test PASS — JSONL batches in data/batches/)
- [x] Phase 1 live validation: real live @braingame.live (room 7684638051930344214), batches flowing every ~2s (comments verified; gifts/likes/follows pending a live that produces them)
- [ ] Phase 2: FastAPI receiver + persistence
- [ ] End-to-end: client POSTs real batches to FastAPI

### Notes (verified against installed TikTokLive 7.0.1)

- v7 is a betterproto rewrite: `LikeEvent` uses `count`/`total` (cumulative total), `CommentEvent` uses `content`, gift diamonds = `diamond_count`, gift proto has `type`/`name`. Normalizers keep getattr fallbacks for 6.x names.
- Avatar URL lives on `user.avatar_thumb/medium/large` as `ImageModel.url_list[0]` (base User proto), not `event.user.avatar`. `_avatar_url()` walks candidates.
- `streaking` property is real in v7: False for non-streakable gifts, `not repeat_end` for streakables → normalizer streak rule is correct.
- No `stream_id` exposed in v7; stream key falls back to `str(room_id)`.
- Run: `.\.venv\Scripts\python.exe -m client` (or `python -m client` with venv active). Smoke test: `.\.venv\Scripts\python.exe tools\smoke_test.py`.