"""Test fixtures: 200 real chat comments from a live TikTok room."""

from __future__ import annotations

import json
from pathlib import Path


def load_real_sample() -> list[dict]:
    p = Path(__file__).resolve().parents[1] / "fixtures" / "real_chat_sample.json"
    return json.loads(p.read_text(encoding="utf-8"))
