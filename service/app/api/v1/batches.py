"""Batch intake endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import Batch, BatchAck
from app.services.batches import ingest_batch

router = APIRouter(tags=["batches"])


@router.post(
    "/lives/{room_id}/batches",
    response_model=BatchAck,
    status_code=status.HTTP_200_OK,
)
async def receive_batch(
    room_id: str, payload: Batch, db: AsyncSession = Depends(get_db)
) -> BatchAck:
    """Accept one batch from the capturer and persist its events.

    The path ``room_id`` is authoritative; a mismatched body ``room_id`` is
    rejected because it indicates a client wiring bug.
    """
    if payload.room_id is not None and payload.room_id != room_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"room_id in path ({room_id!r}) does not match "
                f"room_id in body ({payload.room_id!r})"
            ),
        )

    result = await ingest_batch(db, room_id, payload)
    return BatchAck(received=result.received, batch_id=result.batch_id)