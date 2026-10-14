"""Tests for the sound-listing endpoints.

The overlay cannot enumerate a folder in the browser, so it asks the API for the
list. The only non-trivial rules are: audio suffixes only, sorted, and an empty
or missing folder is `[]` rather than an error.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.api.routes import sounds


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A throwaway app whose sound root is a temp dir.

    The route reads the module-global `WEB_DIR`, so patching it here keeps the
    real `web/sounds` folder out of the test.
    """
    monkeypatch.setattr(sounds, "WEB_DIR", tmp_path)
    application = FastAPI()
    application.include_router(sounds.router, prefix="/api/v1")
    return application


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.get(path)


def _write(directory: Path, *names: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"fake")


class TestWaitingSounds:
    async def test_missing_folder_is_an_empty_list(self, app):
        res = await _get(app, "/api/v1/sounds/waiting")
        assert res.status_code == 200
        assert res.json() == {"sounds": []}

    async def test_empty_folder_is_an_empty_list(self, app, tmp_path):
        (tmp_path / "sounds" / "waiting").mkdir(parents=True)
        assert (await _get(app, "/api/v1/sounds/waiting")).json() == {"sounds": []}

    async def test_lists_audio_urls_sorted_and_ignores_non_audio(self, app, tmp_path):
        _write(
            tmp_path / "sounds" / "waiting",
            "tick.wav",
            "buzz.mp3",
            "cover.png",
            "readme.txt",
        )
        body = (await _get(app, "/api/v1/sounds/waiting")).json()
        assert body["sounds"] == [
            "/sounds/waiting/buzz.mp3",
            "/sounds/waiting/tick.wav",
        ]


class TestCorrectSounds:
    async def test_missing_folder_is_an_empty_list(self, app):
        res = await _get(app, "/api/v1/sounds/correct")
        assert res.status_code == 200
        assert res.json() == {"sounds": []}

    async def test_empty_folder_is_an_empty_list(self, app, tmp_path):
        (tmp_path / "sounds" / "correct").mkdir(parents=True)
        assert (await _get(app, "/api/v1/sounds/correct")).json() == {"sounds": []}

    async def test_lists_audio_urls_sorted_and_ignores_non_audio(self, app, tmp_path):
        _write(
            tmp_path / "sounds" / "correct",
            "ding.wav",
            "chime.mp3",
            "cover.png",
            "readme.txt",
        )
        body = (await _get(app, "/api/v1/sounds/correct")).json()
        assert body["sounds"] == [
            "/sounds/correct/chime.mp3",
            "/sounds/correct/ding.wav",
        ]


class TestPushPlayerUnchanged:
    async def test_still_lists_push_player_under_its_own_path(self, app, tmp_path):
        _write(tmp_path / "sounds" / "pushPlayer", "pop.mp3")
        body = (await _get(app, "/api/v1/sounds/pushPlayer")).json()
        assert body == {"sounds": ["/sounds/pushPlayer/pop.mp3"]}

    async def test_missing_push_folder_is_an_empty_list(self, app):
        assert (await _get(app, "/api/v1/sounds/pushPlayer")).json() == {"sounds": []}
