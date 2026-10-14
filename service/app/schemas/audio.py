"""Pydantic v2 request/response models for the text-to-speech API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TTSRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=1, description="Text to synthesize and play.")


class TTSResponse(BaseModel):
    status: Literal["ok"] = "ok"
    text: str
    output: str


class TTSVoiceRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=1, description="Text to synthesize and play.")
    voice_id: int = Field(
        default=2,
        ge=0,
        description="Index into the system voice list (pyttsx3).",
    )


class TTSVoiceResponse(BaseModel):
    status: Literal["ok"] = "ok"
    text: str
    voice_id: int
