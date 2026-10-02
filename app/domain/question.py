"""Question entity and scoring constants.

`options` is a fixed 4-tuple and `correct` is a letter rather than an index, so
the whole class of "index does not line up with the letter" bugs is designed out.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from .vote import OptionLetter

DIFF_BASE = {"easy": 100, "medium": 150, "hard": 200}

SPEED_BONUS_RATIO = 0.50


class Question(BaseModel):
    id: int
    text: str
    category: str | None = None
    difficulty: str = "medium"  # easy | medium | hard
    options: tuple[str, str, str, str]  # EXACTLY 4, positional == A, B, C, D
    correct: OptionLetter
    explanation: str | None = None

    @field_validator("options")
    @classmethod
    def _exactly4(cls, v: tuple[str, str, str, str]) -> tuple[str, str, str, str]:
        if len(v) != 4:
            raise ValueError("Question must have exactly 4 options (A, B, C, D)")
        return v

    @property
    def base_points(self) -> int:
        return DIFF_BASE.get(self.difficulty, DIFF_BASE["medium"])


def score(is_correct: bool, base: int, elapsed_ms: int, limit_ms: int) -> int:
    """Points for one answer.

    Only correct answers score. The bonus is a linear function of the time left,
    so the first correct answer always gets the full bonus and the last one still
    keeps its base points regardless of how many people took part.
    """
    if not is_correct:
        return 0
    if limit_ms <= 0:
        return base
    ratio = max(0, limit_ms - elapsed_ms) / limit_ms
    return base + int(base * SPEED_BONUS_RATIO * ratio)
