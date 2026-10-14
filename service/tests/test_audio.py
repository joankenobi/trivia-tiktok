"""API contract tests for the TTS endpoints.

The endpoints shell out to ``llama-tts.exe`` / the pyttsx3 engine (not available
in CI), so the service calls are monkeypatched to assert the HTTP contract
instead of the external backends.
"""

from __future__ import annotations

import app.api.v1.audio as audio_api

VOICE_URL = "/api/v1/tts"
MODEL_URL = "/api/v1/tts/model"


# --- POST /tts (pyttsx3 system voices) ---------------------------------------


async def test_tts_voice_returns_ok_and_forwards_args(client, monkeypatch):
    calls: dict = {}

    def fake(text, voice_id=2):
        calls["text"] = text
        calls["voice_id"] = voice_id
        return True

    monkeypatch.setattr(audio_api, "tts_speak", fake)

    res = await client.post(VOICE_URL, json={"text": "hola"})
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "text": "hola", "voice_id": 2}
    assert calls == {"text": "hola", "voice_id": 2}


async def test_tts_voice_forwards_voice_id(client, monkeypatch):
    monkeypatch.setattr(audio_api, "tts_speak", lambda text, voice_id=2: True)

    res = await client.post(VOICE_URL, json={"text": "hola", "voice_id": 1})
    assert res.status_code == 200
    assert res.json()["voice_id"] == 1


async def test_tts_voice_rejects_blank_text(client):
    res = await client.post(VOICE_URL, json={"text": "   "})
    assert res.status_code == 422


async def test_tts_voice_rejects_negative_voice_id(client):
    res = await client.post(VOICE_URL, json={"text": "hola", "voice_id": -1})
    assert res.status_code == 422


async def test_tts_voice_returns_502_on_engine_failure(client, monkeypatch):
    def boom(text, voice_id=2):
        raise Exception("No voices available for TTS.")

    monkeypatch.setattr(audio_api, "tts_speak", boom)

    res = await client.post(VOICE_URL, json={"text": "hola"})
    assert res.status_code == 502
    assert "No voices available" in res.json()["detail"]


# --- POST /tts/model (llama-tts) ---------------------------------------------


async def test_tts_model_returns_output(client, monkeypatch):
    async def fake(text: str) -> str:
        return f"spoke: {text}"

    monkeypatch.setattr(audio_api, "tts_speak_model", fake)

    res = await client.post(MODEL_URL, json={"text": "hola"})
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "text": "hola", "output": "spoke: hola"}


async def test_tts_model_strips_and_rejects_blank_text(client):
    res = await client.post(MODEL_URL, json={"text": "   "})
    assert res.status_code == 422


async def test_tts_model_returns_502_on_service_failure(client, monkeypatch):
    async def boom(text: str) -> str:
        raise RuntimeError("TTS failed: boom")

    monkeypatch.setattr(audio_api, "tts_speak_model", boom)

    res = await client.post(MODEL_URL, json={"text": "hola"})
    assert res.status_code == 502
    assert "TTS failed" in res.json()["detail"]
