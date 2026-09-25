"""Aggregated v1 router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.batches import router as batches_router
from app.api.v1.events import router as events_router
from app.api.v1.health import router as health_router

router = APIRouter()
router.include_router(health_router)
router.include_router(batches_router)
router.include_router(events_router)