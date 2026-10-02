from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from ..config import QUESTIONS_FILE
from ..domain.question import Question
from ..domain.vote import OptionLetter


class QuestionBankError(Exception):
    """The question file is present but invalid: refuse to boot."""


def _options_from_raw(opts) -> tuple[str, str, str, str]:
    if isinstance(opts, (list, tuple)) and len(opts) == 4:
        return tuple(str(o) for o in opts)
    raise ValueError("Each question must have exactly 4 options")


def load_questions_from_file(path: Path | None = None) -> list[Question]:
    """Load and validate the bank.

    A malformed row is a hard error, not a silent skip. Half a bank means
    rounds with missing options and questions whose letters do not line up,
    which would surface as a wrong answer in front of a live audience.
    """
    p = path or QUESTIONS_FILE
    if not p.exists():
        return default_questions()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QuestionBankError(f"{p} is not valid JSON: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise QuestionBankError(f"{p} must contain a non-empty JSON array")

    qs: list[Question] = []
    errors: list[str] = []
    for index, row in enumerate(data):
        try:
            qs.append(
                Question(
                    id=int(row["id"]),
                    text=str(row["text"]),
                    category=row.get("category"),
                    difficulty=str(row.get("difficulty", "medium")),
                    options=_options_from_raw(row["options"]),
                    correct=OptionLetter(str(row["correct"]).upper()),
                    explanation=row.get("explanation"),
                )
            )
        except (KeyError, ValueError, TypeError, ValidationError) as exc:
            errors.append(f"  row {index}: {exc}")

    if errors:
        raise QuestionBankError(
            f"{p} has {len(errors)} invalid question(s):\n" + "\n".join(errors)
        )
    if not qs:
        raise QuestionBankError(f"{p} produced no questions")
    return qs


def default_questions() -> list[Question]:
    return [
        Question(
            id=1,
            text="What is the answer to life, the universe, and everything?",
            category="general",
            difficulty="easy",
            options=("42", "24", "7", "100"),
            correct=OptionLetter.A,
            explanation="It's 42 (Hitchhiker's Guide).",
        ),
        Question(
            id=2,
            text="Which gas makes up most of Earth's atmosphere?",
            category="science",
            difficulty="easy",
            options=("Oxygen", "Nitrogen", "Carbon Dioxide", "Argon"),
            correct=OptionLetter.B,
        ),
        Question(
            id=3,
            text="What is the capital of France?",
            category="geography",
            difficulty="easy",
            options=("Berlin", "Madrid", "Paris", "Rome"),
            correct=OptionLetter.C,
        ),
        Question(
            id=4,
            text="Which planet is known as the Red Planet?",
            category="science",
            difficulty="medium",
            options=("Venus", "Mars", "Jupiter", "Saturn"),
            correct=OptionLetter.B,
        ),
        Question(
            id=5,
            text="Who wrote '1984'?",
            category="literature",
            difficulty="medium",
            options=("Orwell", "Huxley", "Dick", "Bradbury"),
            correct=OptionLetter.A,
        ),
        Question(
            id=6,
            text="Which is the fastest land animal?",
            category="animals",
            difficulty="medium",
            options=("Wildebeest", "Cheetah", "Gazelle", "Antelope"),
            correct=OptionLetter.B,
        ),
        Question(
            id=7,
            text="In what year did Apollo 11 land on the Moon?",
            category="history",
            difficulty="hard",
            options=("1968", "1969", "1970", "1971"),
            correct=OptionLetter.B,
        ),
        Question(
            id=8,
            text="What is the chemical symbol for gold?",
            category="chemistry",
            difficulty="hard",
            options=("Ag", "Au", "Pb", "Fe"),
            correct=OptionLetter.B,
        ),
        Question(
            id=9,
            text="Which element has atomic number 1?",
            category="chemistry",
            difficulty="hard",
            options=("Helium", "Hydrogen", "Lithium", "Boron"),
            correct=OptionLetter.B,
        ),
        Question(
            id=10,
            text="What is the largest ocean on Earth?",
            category="geography",
            difficulty="hard",
            options=("Atlantic", "Indian", "Arctic", "Pacific"),
            correct=OptionLetter.D,
        ),
    ]


def save_questions_to_file(qs: list[Question], path: Path | None = None) -> None:
    p = path or QUESTIONS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    data = [q.model_dump() for q in qs]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
