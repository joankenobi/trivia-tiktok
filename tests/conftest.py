"""Shared test fixtures.

The TTS voiceover is stubbed for the whole suite. The round-open path schedules
a real POST to the voice service, and a test must never depend on (or trigger)
that service; `tests/unit/test_tts.py` exercises the client directly instead.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stub_tts(monkeypatch):
    monkeypatch.setattr(
        "app.services.show_runner.notify_question", lambda *_args, **_kwargs: None
    )
