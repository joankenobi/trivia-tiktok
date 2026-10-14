"""Text-to-speech endpoints: OS voices (pyttsx3) and the local llama-tts model."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, status

from app.schemas.audio import (
    TTSRequest,
    TTSResponse,
    TTSVoiceRequest,
    TTSVoiceResponse,
)
from app.services.audio import tts_speak, tts_speak_model

router = APIRouter(tags=["tts"])


@router.post(
    "/tts",
    response_model=TTSVoiceResponse,
    status_code=status.HTTP_200_OK,
)
async def speak_with_system_voice(payload: TTSVoiceRequest) -> TTSVoiceResponse:
    """Synthesize ``payload.text`` with a local OS voice (pyttsx3) and play it.

    Delegates to :func:`app.services.audio.tts_speak`, which is synchronous and
    blocking, so it runs in a worker thread. Playback is serialized with the
    model endpoint through a shared lock.
    """
    try:
        await asyncio.to_thread(tts_speak, payload.text, payload.voice_id)
    except Exception as exc:  # pyttsx3 raises a bare Exception on engine errors
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return TTSVoiceResponse(text=payload.text, voice_id=payload.voice_id)


@router.post(
    "/tts/model",
    response_model=TTSResponse,
    status_code=status.HTTP_200_OK,
)
async def speak_with_model(payload: TTSRequest) -> TTSResponse:
    """Synthesize ``payload.text`` with the llama-tts model and play it back.

    Delegates to :func:`app.services.audio.tts_speak_model`, which runs the
    external ``llama-tts.exe`` process and blocks until the audio playback
    finishes.
    """
    try:
        output = await tts_speak_model(payload.text)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return TTSResponse(text=payload.text, output=output)
