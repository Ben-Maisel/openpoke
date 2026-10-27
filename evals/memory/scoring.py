"""Deterministic scoring for memory answers: no LLM judge."""

from __future__ import annotations

from typing import Sequence

from ..routing.scoring import wilson_interval

CORRECT, WRONG, ERROR = "correct", "wrong", "error"


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


# Correct when the answer contains at least one alternative from every expected group
def is_correct(answer: str, expect: Sequence[Sequence[str]]) -> bool:
    text = _normalize(answer)
    return bool(expect) and all(any(_normalize(alt) in text for alt in group) for group in expect)


def classify(answer: str, error: str | None, expect: Sequence[Sequence[str]]) -> str:
    if error:
        # The runtime turns API failures into a failed run; a run that just never finished is a wrong answer.
        return WRONG if "iteration limit" in error or "final response" in error else ERROR
    return CORRECT if is_correct(answer, expect) else WRONG


__all__ = ["CORRECT", "ERROR", "WRONG", "classify", "is_correct", "wilson_interval"]
