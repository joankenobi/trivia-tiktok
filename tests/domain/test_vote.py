"""Vote parsing tests.

`test_real_chat_yields_zero_votes` is the guard rail for the whole design. It runs
the parser over 200 real TikTok live comments and asserts that none of them parse
as a vote. If someone ever relaxes VOTE_RE to a substring search, this test fails
immediately and the reason is self-explanatory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.vote import OptionLetter, parse_vote

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "real_chat_sample.json"


@pytest.fixture(scope="module")
def real_comments() -> list[str]:
    return [row["message"] for row in json.loads(FIXTURE.read_text(encoding="utf-8"))]


def test_real_chat_yields_zero_votes(real_comments: list[str]) -> None:
    assert len(real_comments) == 200
    parsed = [(m, parse_vote(m)) for m in real_comments if parse_vote(m) is not None]
    assert parsed == [], f"real chat messages parsed as votes: {parsed[:10]}"


def test_real_chat_every_message_contains_an_ad_character(real_comments: list[str]) -> None:
    """Documents WHY the parser must be anchored rather than a substring search."""
    import re

    with_letter = [m for m in real_comments if re.search(r"[A-Da-d]", m)]
    assert len(with_letter) == len(real_comments)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("A", OptionLetter.A),
        ("a", OptionLetter.A),
        ("  A  ", OptionLetter.A),
        ("A?", OptionLetter.A),
        ("A.", OptionLetter.A),
        ("A!", OptionLetter.A),
        ("!a", OptionLetter.A),
        ("b", OptionLetter.B),
        ("C", OptionLetter.C),
        ("d", OptionLetter.D),
        ("\U0001f525B", OptionLetter.B),
    ],
)
def test_accepts_bare_letters_with_decoration(message: str, expected: OptionLetter) -> None:
    assert parse_vote(message) is expected


@pytest.mark.parametrize(
    "message",
    [
        None,
        "",
        "Hola",
        "ABCD",  # one message must never vote for four options
        "A B",
        "AB",
        "chupetA-n",  # capital A inside a word
        "hola Jacinta vendes mangos?",
        "A mi me gusta",
        "Bia",
        "casa",
        "Dan",
    ],
)
def test_rejects_non_votes(message: str | None) -> None:
    assert parse_vote(message) is None


def test_letters_are_case_insensitive() -> None:
    assert parse_vote("a") == parse_vote("A")
    assert parse_vote("b") == parse_vote("B")
