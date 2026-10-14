"""Push-sound listing endpoint.

The overlay plays one sound at random from a static folder every time an
animation runs, but a browser cannot enumerate a directory. This route hands it
the list; the static host serves the files themselves from the same path.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from ...config import WEB_DIR

router = APIRouter(tags=["sounds"])

# Only these are audio the overlay can play. Anything else the user drops in the
# folder (a readme, a cover image) must not be handed to `new Audio()`.
SOUND_SUFFIXES = {".mp3", ".wav"}


def _list_sounds(subdir: str) -> dict:
    directory = Path(WEB_DIR) / "sounds" / subdir
    if not directory.is_dir():
        return {"sounds": []}
    names = sorted(
        entry.name
        for entry in directory.iterdir()
        if entry.is_file() and entry.suffix.lower() in SOUND_SUFFIXES
    )
    return {"sounds": [f"/sounds/{subdir}/{name}" for name in names]}


@router.get("/sounds/pushPlayer")
async def list_push_sounds() -> dict:
    return _list_sounds("pushPlayer")


@router.get("/sounds/waiting")
async def list_waiting_sounds() -> dict:
    return _list_sounds("waiting")


@router.get("/sounds/correct")
async def list_correct_sounds() -> dict:
    return _list_sounds("correct")
