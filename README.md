# TikTok Lives Backend

Backend that captures events from TikTok LIVE streams and delivers them in **batches** (one HTTP request per batch, never per event) so a downstream service (e.g. FastAPI) can store and share:

- user + message + profile photo (`comment`)
- user + message (`comment`)
- user + gift (`gift`)
- like + user (`like`)
- follow + user (`follow`)

This is **Method 1 — HTTP Batching (Agrupación vía HTTP)** from `Idea.md`. The capturer is a standalone Python package (`client/`); the FastAPI receiver is Phase 2 (see Roadmap).

## How it works

```
TikTok LIVE (WebSocket)
        │
        ▼
TikTokLiveClient          client/main.py
        │  events (comment, gift, like, follow)
        ▼
TikTokNormalizer          client/normalizers.py
        │  TikTok 7.x proto → our pydantic v2 models
        ▼
BatchBuffer               client/buffer.py
        │  in-memory list; flushes per timer (2s) OR size (100), whichever first
        ▼
Sender                    client/sender.py
        │  DryRunSender  → data/batches/<room>.jsonl (one JSON line per batch)
        │  HttpSender    → one POST per batch to API_ENDPOINT (1 retry, 2s backoff)
        ▼
FastAPI (Phase 2 — not built yet)
```

**Batch flush triggers** (whichever happens first):

1. **Timer**: every `BATCH_INTERVAL_SECONDS` (default `2`) with pending events.
2. **Size**: buffer reached `BATCH_MAX_EVENTS` (default `100`).
3. **End**: on `LiveEndEvent`, `DisconnectEvent`, or Ctrl+C — remaining events are flushed.

On a failed POST, the events stay in the buffer and are retried once after a 2s backoff; if that also fails the batch is dropped and a warning is logged (no crash).

## Project structure

```
tiktoklives_back/
├── contract/
│   └── batch.schema.json   # single source of truth (JSON Schema 2020-12)
├── capturer/               # Phase 1 — TikTok capturer (its OWN venv)
│   ├── .venv/              # venv created here
│   ├── .env                # your config (never commit real values)
│   ├── .env.example        # documented template
│   ├── requirements.txt
│   ├── data/
│   │   └── batches/        # dry-run JSONL output
│   ├── client/             # Python package (internal name, python -m client)
│   │   ├── config.py       # settings loaded from .env
│   │   ├── models.py       # pydantic v2 models (capturer's own copy of the contract)
│   │   ├── normalizers.py  # TikTokLive events → our models
│   │   ├── buffer.py       # BatchBuffer (timer + size flush)
│   │   ├── sender.py       # DryRun (JSONL) / HTTP (POST) transport
│   │   └── main.py         # entrypoint (python -m client)
│   └── tools/
│       └── smoke_test.py   # synthetic test without a live
└── service/                # Phase 2 — FastAPI receiver (placeholder, its OWN venv later)
    └── README.md
```

## Setup

Requires Python 3.11+. The capturer has its **own venv** created inside `capturer/` (the Phase 2 service will get its own later). Run from `capturer/`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # PowerShell
pip install -r requirements.txt
```

## Configuration (`.env`)

Copy `.env.example` to `.env` and edit:

```env
# Target live (the streamer's username, not yours)
TIKTOK_USERNAME=@streamer

# Optional proxies (some regions/ISPs cannot reach TikTok without them)
TIKTOK_WEB_PROXY=
TIKTOK_WS_PROXY=

# Batching
BATCH_INTERVAL_SECONDS=2
BATCH_MAX_EVENTS=100

# Receiver. Empty endpoint == dry-run regardless of DRY_RUN.
API_ENDPOINT=
DRY_RUN=true
```

- `DRY_RUN=true` or empty `API_ENDPOINT` → batches are appended as JSON lines to `data/batches/<room_id>.jsonl`. This is the safe default; no live account needed to exercise the pipeline.
- `API_ENDPOINT` set + `DRY_RUN=false` → each batch is POSTed as a single JSON body (content type `application/json`). One request per batch, never per event.

## Run

Dry-run (writes JSONL locally), run from `capturer/`:

```powershell
cd capturer
.\.venv\Scripts\python.exe -m client
```

Live-delivery (once FastAPI exists, or any receiver):

```env
API_ENDPOINT=http://localhost:8000/api/v1/lives/123/batches
DRY_RUN=false
```

Expected console output:

```
2026-09-12 21:20:01 INFO client: capturer starting for @streamer (dry_run=True, interval=2s, max=100)
2026-09-12 21:20:05 INFO client: connected to live: room_id=741234567890
2026-09-12 21:20:06 INFO client: event comment @some_user {'message': 'holaa'}
2026-09-12 21:20:07 INFO client: flush: 14 events buffered, sending...
2026-09-12 21:20:07 INFO __main__: flushed batch with 14 events -> data\batches\741234567890.jsonl
```

## Data contract

The **canonical contract is frozen as a JSON Schema document**: `contract/batch.schema.json` (single source of truth, draft 2020-12). The capturer's pydantic models (`capturer/client/models.py`) are **its own copy** of that contract — the Phase 2 service will define separate pydantic models against the same schema, with no importable shared package between them.

Each JSON line in dry-run mode (or each POST body in HTTP mode) is one `Batch`:

```json
{
  "version": 1,
  "room_id": "741234567890",
  "stream_id": "741234567890",
  "started_at": "2026-09-12T21:20:06.123456+00:00",
  "ended_at": "2026-09-12T21:20:07.987654+00:00",
  "events": [
    {
      "type": "comment",
      "timestamp": "2026-09-12T21:20:06.123456+00:00",
      "user": {
        "unique_id": "some_user",
        "nickname": "Some User",
        "profile_picture_url": "https://p16-sign-va.tiktokcdn.com/..."
      },
      "data": { "message": "holaa" }
    },
    {
      "type": "gift",
      "timestamp": "2026-09-12T21:20:06.200000+00:00",
      "user": { "unique_id": "rich_user", "nickname": "Rich User", "profile_picture_url": null },
      "data": { "gift_name": "Rose", "repeat_count": 1, "diamonds": 1 }
    },
    {
      "type": "like",
      "timestamp": "2026-09-12T21:20:06.300000+00:00",
      "user": { "unique_id": "fan1", "nickname": "Fan One", "profile_picture_url": null },
      "data": { "like_count": 17, "delta": 5 }
    },
    {
      "type": "follow",
      "timestamp": "2026-09-12T21:20:06.400000+00:00",
      "user": { "unique_id": "new_fan", "nickname": "New Fan", "profile_picture_url": null },
      "data": {}
    }
  ]
}
```

### Event types

| `type` | Trigger | `data` |
| --- | --- | --- |
| `comment` | TikTok `CommentEvent` | `message` |
| `gift` | TikTok `GiftEvent` (final streak event only) | `gift_name`, `repeat_count`, `diamonds` |
| `like` | TikTok `LikeEvent` | `like_count`, `delta` |
| `follow` | TikTok `FollowEvent` | `{}` |

Normalization rules (see `client/normalizers.py`):

- **Profile photo** comes from TikTok as a **URL** (`user.avatar_thumb/large`), not image bytes — the contract shares the URL.
- **Likes** arrive as a cumulative per-user counter; the normalizer tracks the previous value and emits `delta` (≥ 1) so one `like` record = one "user liked" unit.
- **Streakable gifts** (proto `type == 1`) fire repeatedly during a streak; only the final, non-streaking event is emitted to avoid flooding. Non-streakable gifts are emitted immediately.

## Test without a live

Run from `capturer/`:

```powershell
cd capturer
.\.venv\Scripts\python.exe tools\smoke_test.py
```

Creates a small buffer with 4 synthetic events, flushes by size and by timer, and asserts the JSONL batch is written.

## Known limitations

- **TikTokLive is reverse-engineered**: TikTok changes its internals and the library breaks. Update with `pip install -U TikTokLive`; keep an eye on `isaackogan/TikTokLive` releases.
- The installed **TikTokLive 7.x** uses a new betterproto API (`CommentEvent.content`, `LikeEvent.total`, `Gift.diamond_count`, avatars in `avatar_thumb`). The normalizers use `getattr` fallbacks for older 6.x field names so they degrade gracefully.
- Some regions/ISPs require `TIKTOK_WEB_PROXY` / `TIKTOK_WS_PROXY`.
- No `stream_id` is exposed by the library; the client uses `str(room_id)` as the stream key.
- Likes arrive pressed in large bursts; batching already absorbs this, but the downstream receiver should expect high `delta` values.

## Roadmap

- [x] Setup: capturer venv + deps + `.env` (TikTokLive 7.0.1)
- [x] Phase 1: TikTok client (models → normalizers → buffer → sender → main)
- [x] Phase 1 verified in dry-run (synthetic smoke test)
- [ ] Phase 1 live validation: real `TIKTOK_USERNAME` with an active live
- [ ] Phase 2: FastAPI receiver (`POST /api/v1/lives/{room_id}/batches`) — layered `app/api → app/services → app/repositories`, SQLAlchemy async + alembic, pydantic v2; runs in **its own venv** inside `service/`
- [ ] End-to-end: `DRY_RUN=false`, client POSTs real batches to FastAPI