"""Read API: chat event pagination and active stream lookup."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.events import ActiveStreamResponse, EventListResponse
from app.services.events import RoomNotFoundError, get_active_stream, get_events

router = APIRouter(tags=["events"])

_VALID_TYPES = {"comment", "gift", "like", "follow"}


def _parse_types(raw: str | None) -> list[str] | None:
    """Split a comma-separated ``types`` value; ``None``/empty means all types."""
    if raw is None or not raw.strip():
        return None
    types = [token.strip() for token in raw.split(",") if token.strip()]
    for kind in types:
        if kind not in _VALID_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown event type {kind!r}; allowed: comment, gift, like, follow",
            )
    return types or None


@router.get("/lives/{room_id}/events", response_model=EventListResponse)
async def list_live_events(
    room_id: str,
    since: int | None = Query(
        default=None,
        ge=0,
        description="Return only events with an id strictly greater than this cursor.",
    ),
    types: str | None = Query(
        default=None,
        description="Comma-separated event types to include (comment, gift, like, follow). Omit for all.",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=200,
        description="Maximum number of events to return per page.",
    ),
    db: AsyncSession = Depends(get_db),
) -> EventListResponse:
    kind_filter = _parse_types(types)
    try:
        return await get_events(
            db,
            room_id=room_id,
            since=since,
            types=kind_filter,
            limit=limit,
        )
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Room not found") from exc


@router.get("/lives/{room_id}/streams/active", response_model=ActiveStreamResponse)
async def active_stream(
    room_id: str,
    db: AsyncSession = Depends(get_db),
) -> ActiveStreamResponse:
    try:
        return await get_active_stream(db, room_id=room_id)
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Room not found") from exc