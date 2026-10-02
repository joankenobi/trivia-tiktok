"""Show and round endpoints.

Autoplay is the normal path: the runner owns the open/close/reveal sequence and
these endpoints only describe or nudge it. The manual routes stay because they
are what the runner itself is built from, and what a host uses to take over.
"""

from __future__ import annotations

import random

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db import get_session
from ...infra.models import Question, Round, Show
from ...services import session_service as svc
from ...services.show_runner import (
    SHOW_FINISHED,
    close_round_now,
    open_round_now,
)
from ..schemas import CreateShowIn, RankingEntry, RoundOut, ShowOut, TallyOut
from ._common import is_revealed, round_out, visible_round

router = APIRouter(tags=["shows"])


async def _show_out(
    session: AsyncSession, show: Show, *, autoplay: bool
) -> ShowOut:
    rounds = sorted(show.rounds, key=lambda r: r.position)  # selectin-loaded
    current = visible_round(rounds)
    return ShowOut(
        id=show.id,
        room_id=show.room_id,
        question_count=show.question_count,
        question_time_limit_s=show.question_time_limit_s,
        status=show.status,
        current_round_id=current.id if current else None,
        # The open round carries a tally built on the spot. `round.tally_json` is
        # only written at close, so reading it would show a frozen (empty) count
        # for the entire voting window -- exactly the number the audience wants
        # to watch move. Closed rounds still use their frozen tally.
        rounds=[
            await _round_out_live(session, r, is_revealed(r.status))
            for r in rounds
        ],
        autoplay=autoplay,
    )


async def _round_out_live(
    session: AsyncSession, rnd: Round, reveal: bool
) -> RoundOut:
    if rnd.status == "open":
        return round_out(rnd, reveal).model_copy(
            update={"tally": TallyOut(**await svc.build_tally(session, rnd))}
        )
    return round_out(rnd, reveal)


def _autoplay_state(show_id: int) -> bool:
    from ... import main_state

    return main_state.autoplay_on(show_id)


def _start_autoplay(show_id: int) -> bool:
    from ... import main_state

    if main_state.runner is None:
        return False
    # A finished show has nothing left to play; the runner would stop on its
    # first tick anyway, but failing loudly here beats a silently dead button.
    if not main_state.runner.start(show_id):
        return False
    return True


@router.post("/shows", response_model=ShowOut, status_code=201)
async def create_show(
    body: CreateShowIn, session: AsyncSession = Depends(get_session)
) -> ShowOut:
    questions = list(
        (await session.execute(select(Question).order_by(Question.id))).scalars().all()
    )
    if not questions:
        raise HTTPException(503, "question bank is empty")

    count = min(body.question_count, len(questions))
    picked = [q.id for q in random.sample(questions, count)]
    show = await svc.create_show(
        session, body.room_id, picked, body.question_time_limit_s
    )
    # Autoplay by default: creating a show is the same as starting it. The
    # runner task takes it from there, so the response still has no open round.
    autoplay = _start_autoplay(show.id) if body.autoplay else False
    return await _show_out(session, show, autoplay=autoplay)


@router.get("/shows/active")
async def get_active_show(session: AsyncSession = Depends(get_session)) -> dict:
    """The most recently created show, or null.

    The OBS overlay has no UI, so it needs one call to find what to render
    instead of a show id typed by hand. Declared before `/shows/{show_id}` so
    the literal path wins the match.
    """
    show = (
        await session.execute(select(Show).order_by(Show.id.desc()).limit(1))
    ).scalar_one_or_none()
    if show is None:
        return {"show_id": None, "room_id": None, "round_id": None, "status": None}

    current = visible_round(show.rounds)
    return {
        "show_id": show.id,
        "room_id": show.room_id,
        "round_id": current.id if current else None,
        "status": current.status if current else None,
    }


@router.get("/shows/{show_id}", response_model=ShowOut)
async def get_show(show_id: int, session: AsyncSession = Depends(get_session)) -> ShowOut:
    show = await session.get(Show, show_id)
    if show is None:
        raise HTTPException(404, "show not found")
    return await _show_out(session, show, autoplay=_autoplay_state(show_id))


@router.post("/shows/{show_id}/autoplay", response_model=ShowOut)
async def start_autoplay(
    show_id: int, session: AsyncSession = Depends(get_session)
) -> ShowOut:
    """Hand the show over to the runner."""
    show = await session.get(Show, show_id)
    if show is None:
        raise HTTPException(404, "show not found")
    if show.status == SHOW_FINISHED:
        raise HTTPException(409, "show already finished")

    # A stopped runner leaves an open round behind. Re-adopting it is exactly
    # what the runner does on resume, so restarting mid-question costs nothing.
    _start_autoplay(show_id)
    return await _show_out(session, show, autoplay=_autoplay_state(show_id))


@router.delete("/shows/{show_id}/autoplay", response_model=ShowOut)
async def stop_autoplay(
    show_id: int, session: AsyncSession = Depends(get_session)
) -> ShowOut:
    """Take manual control back. The current round is left exactly as it is."""
    from ... import main_state

    show = await session.get(Show, show_id)
    if show is None:
        raise HTTPException(404, "show not found")
    if main_state.runner is not None:
        await main_state.runner.stop(show_id)
    return await _show_out(session, show, autoplay=False)


@router.post("/shows/{show_id}/rounds", response_model=RoundOut)
async def open_next_round(
    show_id: int, session: AsyncSession = Depends(get_session)
) -> RoundOut:
    """Open the next pending round. Refuses while one is already open."""
    show = await session.get(Show, show_id)
    if show is None:
        raise HTTPException(404, "show not found")

    if any(r.status == "open" for r in show.rounds):
        raise HTTPException(409, "a round is already open")
    nxt = next(
        (r for r in sorted(show.rounds, key=lambda r: r.position) if r.status == "pending"),
        None,
    )
    if nxt is None:
        raise HTTPException(409, "no pending rounds left")

    from ... import main_state

    # Same helper the runner uses, so manual opening and automatic opening share
    # one cursor rule instead of two copies that drift apart.
    rnd = await open_round_now(session, nxt, main_state.registry)
    return round_out(rnd, False)


@router.post("/rounds/{round_id}/close", response_model=RoundOut)
async def close_round(
    round_id: int, session: AsyncSession = Depends(get_session)
) -> RoundOut:
    rnd = await session.get(Round, round_id)
    if rnd is None:
        raise HTTPException(404, "round not found")

    from ... import main_state

    try:
        rnd = await close_round_now(session, rnd, main_state.registry)
    except svc.RoundNotFound:
        raise HTTPException(404, "round not found")
    # Still closed, not revealed: the answer stays hidden until /reveal.
    return round_out(rnd, False)


@router.post("/rounds/{round_id}/reveal", response_model=RoundOut)
async def reveal_round(
    round_id: int, session: AsyncSession = Depends(get_session)
) -> RoundOut:
    try:
        rnd = await svc.reveal_round(session, round_id)
    except svc.RoundNotFound:
        raise HTTPException(404, "round not found")
    return round_out(rnd, True)


@router.get("/shows/{show_id}/ranking", response_model=list[RankingEntry])
async def get_ranking(
    show_id: int, session: AsyncSession = Depends(get_session)
) -> list[RankingEntry]:
    rows = await svc.ranking(session, show_id)
    return [RankingEntry(**r) for r in rows]


@router.get("/questions")
async def list_questions(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    rows = (
        (await session.execute(select(Question).order_by(Question.id).limit(limit)))
        .scalars()
        .all()
    )
    return [
        {
            "id": q.id,
            "text": q.text,
            "category": q.category,
            "difficulty": q.difficulty,
            "options": list(q.options),
        }
        for q in rows
    ]


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "app": settings.app_name}
