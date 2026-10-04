# TikTok Lives — Read API for the Game

How your game consumes live chat events from the TikTok Lives backend.

- Base URL: `http://<service-host>:8000`
- All endpoints are under `/api/v1`
- **No authentication** (matches the ingestion API; a key can be added later if needed)
- All timestamps are ISO 8601 UTC (e.g. `2026-09-13T22:30:01.123456Z`)

## Consumption flow

A game must not hardcode a room id — the streamer changes. Consume the API in this order:

1. **`GET /api/v1/lives/active`** — discover which room is being captured right now.
2. **`GET /api/v1/lives/{room_id}/events`** — poll chat events for that room.
3. **`GET /api/v1/lives/{room_id}/streams/active`** — optionally, per room, to know whether the live is still going.

Step 1 is a discovery endpoint and **always returns 200**. `room_id` is `null`
only on a database that has never ingested a batch. When a live goes quiet the
room keeps being reported with `active: false`, so your game can react instead
of assuming the room id changed.

---

## 1. Discover the active live

```
GET /api/v1/lives/active
```

No parameters, no 404 — a game cannot know the room id in advance, so this
endpoint answers "none yet" in the body instead of failing.

### Response

```json
{
  "room_id": "7684638051930344214",
  "started_at": "2026-09-13T21:00:00Z",
  "host_username": "braingame.live",
  "active": true
}
```

Before the first batch has ever been ingested:

```json
{
  "room_id": null,
  "started_at": null,
  "host_username": null,
  "active": false
}
```

| Field           | Type    | Description |
| --------------- | ------- | ----------- |
| `room_id`       | string? | TikTok room id of the most recently seen stream. `null` only if the database never ingested a batch. |
| `started_at`    | string? | When the live started (first seen batch) |
| `host_username` | string? | TikTok username of the host, if known |
| `active`        | boolean | `true` if a batch was ingested in the last 30 seconds. A stale room is still returned with its real `room_id` and `active: false`. |

When several rooms exist, the one with the most recent `last_seen` wins.

---

## 2. Get chat events (polling)

```
GET /api/v1/lives/{room_id}/events
```

`{room_id}` is the TikTok live room id (string).

### Query parameters

| Param   | Type    | Required | Default | Description |
| ------- | ------- | -------- | ------- | ----------- |
| `since` | integer | no       | `0`     | Return only events with `event_id` **strictly greater** than this cursor |
| `types` | string  | no       | all     | Comma-separated types: `comment`, `gift`, `like`, `follow` |
| `limit` | integer | no       | `100`   | Max events per page, `1..200` |

### Response

```json
{
  "events": [
    {
      "event_id": 42,
      "type": "comment",
      "ts": "2026-09-13T22:30:01.123456Z",
      "user": {
        "unique_id": "braingame.live",
        "nickname": "Brain Game",
        "avatar": "https://p16-.../avatar.jpg"
      },
      "data": {
        "message": "Hello!"
      }
    }
  ],
  "next_cursor": 42,
  "has_more": false
}
```

### Event fields

| Field      | Type     | Description |
| ---------- | -------- | ----------- |
| `event_id` | integer  | Monotonic global cursor. **The only thing you must persist.** |
| `type`     | string   | `comment` \| `gift` \| `like` \| `follow` |
| `ts`       | string   | Event timestamp, ISO 8601 UTC |
| `user`     | object   | `unique_id`, `nickname`, `avatar` (any may be `null`) |
| `data`     | object   | Type-specific content (below) |

### `data` per event type

| Type     | Shape                                                        |
| -------- | ------------------------------------------------------------ |
| `comment`| `{ "message": "Hello!" }`                                    |
| `gift`   | `{ "gift_name": "TikTok", "repeat_count": 3, "diamonds": 30 }` — `diamonds` may be `null` |
| `like`   | `{ "like_count": 1234, "delta": 2 }` — `delta` = new likes for this user since the previous event |
| `follow` | `{}` (empty object)                                          |

### Polling flow (recommended)

1. Start with **no cursor** (or `since=0`) to get the first page.
2. Process `events[]`, then save `next_cursor` locally.
3. Poll again after **1–2 seconds**, sending `since=<your saved next_cursor>`.
4. Repeat. `next_cursor` is stable: if a response has no events it echoes your `since`, so you never lose or duplicate events.

```text
poll 1: GET /events                          -> next_cursor = 42
poll 2: GET /events?since=42                 -> next_cursor = 87
poll 3: GET /events?since=87                 -> events: [], next_cursor = 87
```

### Notes

- Events come in ingestion (chronological) order — `event_id` grows monotonically.
- `has_more: true` means another page exists for the same `since`; paginate by calling again with `limit` and the last `event_id` until `has_more` is false.
- **The cursor is per room, not per live session**: if the same streamer starts a new live, events from the previous live are still returned first. If your game must reset per session, ask us — we can scope the cursor by `stream_id`.

---

## 3. Active stream status (per room)

```
GET /api/v1/lives/{room_id}/streams/active
```

### Response

```json
{
  "room_id": "7684638051930344214",
  "started_at": "2026-09-13T21:00:00Z",
  "host_username": "braingame.live",
  "active": true
}
```

| Field           | Type    | Description |
| --------------- | ------- | ----------- |
| `room_id`       | string  | TikTok room id |
| `started_at`    | string  | When the live started (first seen batch) |
| `host_username` | string? | TikTok username of the host, if known |
| `active`        | boolean | `true` if a batch was ingested in the last 30 seconds. A stale stream is still returned with `active: false` so the game can react to the live going quiet. |

---

## Errors

| Status | When | Body |
| ------ | ---- | ---- |
| `404`  | `{room_id}` on the room-scoped endpoints (`/lives/{room_id}/events`, `/lives/{room_id}/streams/active`) has no persisted stream/events. Never returned by `/lives/active`. | `{ "detail": "Room not found" }` |
| `422`  | Invalid `types` value, `limit` out of `1..200`, or malformed `since` | `{ "detail": "..." }` |

---

## Example

```bash
curl "http://localhost:8000/api/v1/lives/7684638051930344214/events?since=42&types=comment,gift&limit=50"
```

```json
{
  "events": [
    {
      "event_id": 43,
      "type": "comment",
      "ts": "2026-09-13T22:30:03.123456Z",
      "user": { "unique_id": "player_one", "nickname": "Player One", "avatar": null },
      "data": { "message": "Let's go!" }
    },
    {
      "event_id": 44,
      "type": "gift",
      "ts": "2026-09-13T22:30:04.123456Z",
      "user": { "unique_id": "whale", "nickname": "Whale", "avatar": null },
      "data": { "gift_name": "TikTok", "repeat_count": 1, "diamonds": 10 }
    }
  ],
  "next_cursor": 44,
  "has_more": false
}
```