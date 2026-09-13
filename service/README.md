# TikTok Lives Receiver (Phase 2)

FastAPI service that accepts the event batches emitted by `../capturer` and
persists them as normalized rows. It never imports anything from the capturer;
both sides define their own pydantic models against the frozen contract in
`../contract/batch.schema.json`.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/v1/lives/{room_id}/batches` | Accept one `Batch`, persist events, return `{"received": n, "batch_id": <stream id>}` |
| `GET` | `/api/v1/health` | `{"status": "ok"}` |

The path `room_id` is authoritative; a body `room_id` that contradicts it is
rejected with `400`.

The batch intake endpoint is unauthenticated, so each request is bounded:
at most `MAX_BATCH_EVENTS` events per payload (422 when exceeded) and at most
`MAX_PAYLOAD_BYTES` of body (413, rejected before the body is read).

## Schema

Mirrors `contract/batch.schema.json`: `version` (must be `1`), optional
`room_id`/`stream_id`, ISO 8601 `started_at`/`ended_at`, and `events[]` whose
`type` must be one of `comment | gift | like | follow`. Per-type `data`
requirements are enforced:

- `comment` → `data.message` (string)
- `gift` → `data.gift_name`, `data.repeat_count`, `data.diamonds`
- `like` → `data.like_count`, `data.delta`
- `follow` → `data` must be empty (`{}`)

Unknown event types or malformed per-type data are rejected with `422`.

## Persistence

- `streams` — one row per (room, stream), with batch provenance
  (`first_seen`, `last_seen`, `batch_count`, `event_count`).
- `users` — deduplicated on `unique_id` (upsert refreshes nickname/avatar).
- `comments`, `gifts`, `likes`, `follows` — one row per event.

## Setup

```powershell
cd service
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env          # optional; defaults match .env.example
```

## Migrations (Alembic, async)

```powershell
# Create a fresh SQLite DB with the initial schema
.\.venv\Scripts\alembic.exe upgrade head

# After model changes, generate a new migration
.\.venv\Scripts\alembic.exe revision --autogenerate -m "describe change"
.\.venv\Scripts\alembic.exe upgrade head
```

Migrations read the same `DATABASE_URL` as the app (from `.env` or the
environment). Development convenience: the app also runs
`Base.metadata.create_all` on startup, so it boots even before a migration.

## Run

```powershell
# Uses the defaults from .env.example (see .env for overrides):
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
```

> **WARNING**: the batch intake endpoint is unauthenticated and accepts open
> payloads up to `MAX_BATCH_EVENTS` events / `MAX_PAYLOAD_BYTES` per request.
> Binding `--host 0.0.0.0` exposes that endpoint to your whole network; only
> do it on a private network where every client is trusted.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Tests run against an in-memory SQLite engine; they never touch `service.db`.

## Wiring the capturer

Point the capturer at this service and disable dry-run:

```env
API_ENDPOINT=http://localhost:8000/api/v1/lives/{room_id}/batches
DRY_RUN=false
```

The capturer substitutes `{room_id}` itself before POSTing each batch.

## Structure

```
app/
  main.py          app factory + lifespan
  config.py        dataclass settings from .env (DATABASE_URL, LOG_LEVEL, HOST, PORT, MAX_BATCH_EVENTS, MAX_PAYLOAD_BYTES)
  database.py      async engine/session factory, get_db dependency
  api/v1/          HTTP routers (health, batches)
  services/        batch ingestion / normalization logic
  repositories/    data access (async SQLAlchemy)
  schemas/         pydantic v2 models mirroring the contract
  models/          SQLAlchemy ORM entities
alembic/           async-enabled migrations
tests/             API tests (httpx ASGI transport + in-memory SQLite)
```