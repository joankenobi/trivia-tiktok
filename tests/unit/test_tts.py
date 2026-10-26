"""Tests for the TTS voiceover client.

The interesting behaviour is the boundary: exactly which endpoint and body a new
question produces, and that a failing voice service cannot reach the caller.
"""

from __future__ import annotations

import httpx
import pytest

from app.infra.tts import TTSClient, _announce_safely


def _client(handler) -> TTSClient:
    return TTSClient(
        base_url="http://tts.test",
        timeout_s=5.0,
        transport=httpx.MockTransport(handler),
    )


class TestAnnounce:
    async def test_posts_question_text_to_the_tts_endpoint(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={})

        await _client(handler).announce("Capital of France?")

        assert len(seen) == 1
        assert seen[0].method == "POST"
        assert seen[0].url.path == "/api/v1/tts"
        assert seen[0].content == b'{"text":"Capital of France?"}'

    async def test_error_response_raises_for_the_caller(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        with pytest.raises(httpx.HTTPStatusError):
            await _client(handler).announce("boom")


class TestAnnounceSafely:
    async def test_a_failing_service_does_not_raise(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no voice service", request=request)

        monkeypatch.setattr("app.infra.tts._default_client", _client(handler))
        # Must return normally despite the dead service.
        await _announce_safely("still fine")


class TestDefaultTimeout:
    def test_default_timeout_outlasts_a_spoken_question(self):
        """The upstream endpoint answers only after the text has finished
        playing, so the response latency is the speech duration. A shorter
        default times out on every long question while the audio still plays.
        """
        assert TTSClient().timeout_s >= 30.0
