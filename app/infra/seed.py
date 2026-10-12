from __future__ import annotations

import hashlib
import html
import json
import random
from pathlib import Path

from pydantic import ValidationError

from ..config import QUESTIONS_FILE
from ..domain.question import Question
from ..domain.vote import OptionLetter

LETTERS = "ABCD"


class QuestionBankError(Exception):
    """The question file is present but invalid: refuse to boot."""


def _shuffled_options(
    text: str, correct_answer: str, incorrect: list[str]
) -> tuple[tuple[str, str, str, str], OptionLetter]:
    """Place the four options in a deterministic, stable pseudo-random order.

    OpenTDB lists `correct_answer` on its own, so without a shuffle the answer
    would always land on option A. Seeding from the question text and answer
    keeps the layout stable across restarts and test runs while varying the
    correct letter from question to question.

    The pre-shuffle list is sorted so the result depends only on the SET of
    options, not on the order the file happened to list them in. That is what
    makes save -> load round-trip to the exact same option order.
    """
    options = sorted([correct_answer, *incorrect])
    seed = int.from_bytes(
        hashlib.sha256((text + "\0" + correct_answer).encode("utf-8")).digest()[:8],
        "big",
    )
    random.Random(seed).shuffle(options)
    correct = OptionLetter(LETTERS[options.index(correct_answer)])
    return (options[0], options[1], options[2], options[3]), correct


def _question_from_result(index: int, row) -> Question:
    if not isinstance(row, dict):
        raise ValueError("each result must be a JSON object")

    qtype = row.get("type")
    if qtype not in (None, "multiple"):
        raise ValueError(f"unsupported question type {qtype!r}; only 'multiple' is accepted")

    incorrect_raw = row["incorrect_answers"]
    if not isinstance(incorrect_raw, (list, tuple)):
        raise ValueError("incorrect_answers must be a list")
    if len(incorrect_raw) != 3:
        raise ValueError(
            f"incorrect_answers must have exactly 3 entries, got {len(incorrect_raw)}"
        )

    text = html.unescape(str(row["question"]))
    correct_answer = html.unescape(str(row["correct_answer"]))
    incorrect = [html.unescape(str(x)) for x in incorrect_raw]
    if correct_answer in incorrect:
        raise ValueError("correct_answer must not also appear in incorrect_answers")

    category = row.get("category")
    explanation = row.get("explanation")
    options, correct = _shuffled_options(text, correct_answer, incorrect)
    return Question(
        id=index + 1,
        text=text,
        category=html.unescape(str(category)) if category is not None else None,
        difficulty=str(row.get("difficulty", "medium")),
        options=options,
        correct=correct,
        explanation=html.unescape(str(explanation)) if explanation is not None else None,
    )


def load_questions_from_file(path: Path | None = None) -> list[Question]:
    """Load and validate the OpenTDB-envelope bank.

    The file is a JSON object with a `results` array (the Open Trivia DB
    envelope), extended with an optional per-question `explanation`. A malformed
    row is a hard error, not a silent skip. Half a bank means rounds with missing
    options and questions whose letters do not line up, which would surface as a
    wrong answer in front of a live audience.
    """
    p = path or QUESTIONS_FILE
    if not p.exists():
        return default_questions()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QuestionBankError(f"{p} is not valid JSON: {exc}") from exc

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results:
        raise QuestionBankError(
            f"{p} must be an OpenTDB envelope: a JSON object with a non-empty "
            f"'results' array"
        )

    qs: list[Question] = []
    errors: list[str] = []
    for index, row in enumerate(results):
        try:
            qs.append(_question_from_result(index, row))
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
    results: list[dict] = []
    for q in qs:
        correct_index = LETTERS.index(q.correct.value)
        entry: dict = {
            "type": "multiple",
            "difficulty": q.difficulty,
            "category": q.category,
            "question": q.text,
            "correct_answer": q.options[correct_index],
            "incorrect_answers": [
                option for i, option in enumerate(q.options) if i != correct_index
            ],
        }
        if q.explanation is not None:
            entry["explanation"] = q.explanation
        results.append(entry)
    data = {"response_code": 0, "results": results}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
