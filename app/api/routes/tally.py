"""Live tally endpoint.

The SPA polls this every second. While a round is open the tally is computed
live; once the round is closed it is served from the frozen snapshot so late
events can never mutate a result the audience already saw.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...infra.models import Round
from ...services import session_service as svc
from ..schemas import TallyOut

router = APIRouter(tags=["tally"])


@router.get("/rounds/{round_id}/tally", response_model=TallyOut)
async def get_tally(
    round_id: int, session: AsyncSession = Depends(get_session)
) -> TallyOut:
    rnd = await session.get(Round, round_id)
    if rnd is None:
        raise HTTPException(404, "round not found")
    if rnd.tally_json:
        return TallyOut(**rnd.tally_json)
    return TallyOut(**(await svc.build_tally(session, rnd)))
