"""Focused tests for the OpenTDB-envelope question loader and writer.

The bank is now an Open Trivia DB envelope (`response_code` + `results`)
extended with an optional per-question `explanation`. These tests pin the
loader contract: id from position, HTML unescaping, deterministic option
shuffle, explanation passthrough, and hard rejection of malformed rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import QUESTIONS_FILE
from app.domain.question import Question
from app.infra.seed import (
    QuestionBankError,
    load_questions_from_file,
    save_questions_to_file,
)

LETTERS = "ABCD"


def _selected(q: Question) -> str:
    """Text of the option the question marks as correct."""
    return q.options[LETTERS.index(q.correct.value)]


def _row(
    question: str,
    correct_answer: str,
    incorrect_answers: list[str],
    **extra: object,
) -> dict:
    row: dict = {
        "type": "multiple",
        "difficulty": "medium",
        "category": "general",
        "question": question,
        "correct_answer": correct_answer,
        "incorrect_answers": incorrect_answers,
    }
    row.update(extra)
    return row


def _write_bank(tmp_path: Path, results: list[dict], name: str = "bank.json") -> Path:
    p = tmp_path / name
    p.write_text(
        json.dumps({"response_code": 0, "results": results}, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def test_parses_envelope_and_maps_correct_answer(tmp_path: Path) -> None:
    p = _write_bank(
        tmp_path,
        [
            _row("Question one", "Beta", ["Alpha", "Gamma", "Delta"]),
            _row("Question two", "Two", ["One", "Three", "Four"]),
        ],
    )
    qs = load_questions_from_file(p)

    assert len(qs) == 2
    assert all(isinstance(q, Question) for q in qs)
    raw_rows = json.loads(p.read_text(encoding="utf-8"))["results"]
    for q, row in zip(qs, raw_rows):
        assert _selected(q) == row["correct_answer"]


def test_decodes_html_entities_everywhere(tmp_path: Path) -> None:
    p = _write_bank(
        tmp_path,
        [
            _row(
                "Tom &amp; Jerry said &quot;hi&#039;s&quot;",
                "&aacute;cido &amp; base",
                ["uno", "dos", "c&rsquo;d"],
                category="cu&aacute;ntica",
                explanation="porque &quot;s&iacute;&quot;",
            )
        ],
    )
    q = load_questions_from_file(p)[0]

    assert q.text == 'Tom & Jerry said "hi\'s"'
    assert q.category == "cuántica"
    assert "ácido & base" in q.options
    assert "c’d" in q.options
    assert q.explanation == 'porque "sí"'


def test_has_four_options_with_correct_and_incorrects(tmp_path: Path) -> None:
    p = _write_bank(tmp_path, [_row("Q", "C", ["X", "Y", "Z"])])
    q = load_questions_from_file(p)[0]

    assert len(q.options) == 4
    assert set(q.options) == {"C", "X", "Y", "Z"}
    assert _selected(q) == "C"


def test_shuffle_is_deterministic_and_varies_the_letter(tmp_path: Path) -> None:
    p = _write_bank(
        tmp_path,
        [
            _row("Question one", "C1", ["X1", "Y1", "Z1"]),
            _row("Question two", "C2", ["X2", "Y2", "Z2"]),
            _row("Question three", "C3", ["X3", "Y3", "Z3"]),
        ],
    )
    first = load_questions_from_file(p)
    second = load_questions_from_file(p)

    assert [q.options for q in first] == [q.options for q in second]
    assert [q.correct for q in first] == [q.correct for q in second]
    assert len({q.correct for q in first}) > 1, "correct letter should not always be A"


def test_explanation_passthrough_when_present_and_none_when_absent(tmp_path: Path) -> None:
    p = _write_bank(
        tmp_path,
        [
            _row("With explanation", "Yes", ["No", "Maybe", "Never"], explanation="Because."),
            _row("Without explanation", "Yes", ["No", "Maybe", "Never"]),
        ],
    )
    with_expl, without_expl = load_questions_from_file(p)

    assert with_expl.explanation == "Because."
    assert without_expl.explanation is None


def test_id_is_derived_from_position_one_based(tmp_path: Path) -> None:
    p = _write_bank(
        tmp_path,
        [
            _row("First", "C", ["X", "Y", "Z"]),
            _row("Second", "C", ["X", "Y", "Z"]),
            _row("Third", "C", ["X", "Y", "Z"]),
        ],
    )
    qs = load_questions_from_file(p)

    assert [q.id for q in qs] == [1, 2, 3]


def test_non_multiple_type_is_rejected(tmp_path: Path) -> None:
    p = _write_bank(
        tmp_path,
        [_row("True or false?", "True", ["False"], type="boolean")],
    )
    with pytest.raises(QuestionBankError):
        load_questions_from_file(p)


def test_wrong_incorrect_answer_count_is_rejected(tmp_path: Path) -> None:
    p = _write_bank(tmp_path, [_row("Q", "C", ["X", "Y"])])
    with pytest.raises(QuestionBankError):
        load_questions_from_file(p)


def test_correct_answer_duplicated_in_incorrects_is_rejected(tmp_path: Path) -> None:
    p = _write_bank(tmp_path, [_row("Q", "C", ["C", "Y", "Z"])])
    with pytest.raises(QuestionBankError):
        load_questions_from_file(p)


def test_bare_array_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "legacy.json"
    p.write_text(
        json.dumps(
            [{"id": 1, "text": "Q", "options": ["A", "B", "C", "D"], "correct": "A"}]
        ),
        encoding="utf-8",
    )
    with pytest.raises(QuestionBankError):
        load_questions_from_file(p)


def test_empty_results_is_rejected(tmp_path: Path) -> None:
    p = _write_bank(tmp_path, [])
    with pytest.raises(QuestionBankError):
        load_questions_from_file(p)


def test_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    qs = load_questions_from_file(tmp_path / "does_not_exist.json")
    assert qs


def test_round_trip_preserves_questions(tmp_path: Path) -> None:
    p = tmp_path / "round_trip.json"
    original = load_questions_from_file(
        _write_bank(
            tmp_path,
            [
                _row("Question one", "C1", ["X1", "Y1", "Z1"], explanation="first"),
                _row("Question two", "C2", ["X2", "Y2", "Z2"]),
            ],
        )
    )

    save_questions_to_file(original, p)
    reloaded = load_questions_from_file(p)

    assert [q.options for q in reloaded] == [q.options for q in original]
    assert [q.correct for q in reloaded] == [q.correct for q in original]
    assert [q.text for q in reloaded] == [q.text for q in original]


def test_shipped_bank_loads_with_matching_correct_answers() -> None:
    raw_rows = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))["results"]
    qs = load_questions_from_file(QUESTIONS_FILE)

    assert len(qs) == 15
    assert len(qs) == len(raw_rows)
    for q, row in zip(qs, raw_rows):
        assert _selected(q) == row["correct_answer"]
    assert len({q.correct for q in qs}) > 1
