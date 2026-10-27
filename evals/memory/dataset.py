"""The long-lived worker's journal and the questions asked of it.

A journal is a list of runs (request, tool calls, response). The planted runs from
data/story.json sit at fixed places: 'early' runs open the journal, 'middle' runs sit
halfway, and 'late' runs close it. Filler runs from data/filler.json pad the gaps in a
seeded order, so a longer journal only adds noise and the planted facts never change.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

DATA_DIR = Path(__file__).resolve().parent / "data"
JOURNAL_LENGTHS: Tuple[int, ...] = (20, 60, 200)
DEFAULT_SEED = 20261027


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: Dict[str, Any]
    result: Any
    ok: bool = True


@dataclass(frozen=True)
class Run:
    request: str
    calls: Tuple[ToolCall, ...]
    response: str
    planted: bool


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    source: str
    question: str
    expect: Tuple[Tuple[str, ...], ...]


def _load(name: str) -> Dict[str, Any]:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


# A compact email from the data files as the full search result the app would have stored
def _expand_email(email: Dict[str, Any], query: str, email_id: str) -> Dict[str, Any]:
    return {
        "id": email_id, "thread_id": email.get("thread", email_id), "query": query,
        "subject": email["subject"], "sender": email["from"], "recipient": "me@example.com",
        "timestamp": email.get("date", "2026-10-10 09:00:00"), "label_ids": ["INBOX"],
        "clean_text": email["text"], "has_attachments": False, "attachment_count": 0, "attachment_filenames": [],
    }


# Filler email IDs use letters only, so no generated ID can contain a numeric answer
def _letters(number: int) -> str:
    text = ""
    while True:
        number, digit = divmod(number, 26)
        text = "abcdefghijklmnopqrstuvwxyz"[digit] + text
        if number == 0:
            return text


def _to_run(raw: Dict[str, Any], planted: bool, copy: int = 0) -> Run:
    def fill(text: str) -> str:
        return text.replace("{n}", str(copy))

    calls = []
    for c, call in enumerate(raw["calls"]):
        result = call["result"]
        if call["tool"] == "task_email_search":
            query = call["arguments"]["search_query"]
            result = [
                _expand_email({**e, "subject": fill(e["subject"])}, query,
                              e.get("id") or f"fl{_letters(copy)}{_letters(c)}{_letters(i)}")
                for i, e in enumerate(result)
            ]
        calls.append(ToolCall(call["tool"], call["arguments"], result, call.get("ok", True)))
    return Run(fill(raw["request"]), tuple(calls), fill(raw["response"]), planted)


def agent_name() -> str:
    return _load("story.json")["agent_name"]


def planted_runs() -> Dict[str, List[Run]]:
    story = _load("story.json")
    return {part: [_to_run(r, planted=True) for r in story[part]] for part in ("early", "middle", "late")}


def filler_runs(count: int, seed: int = DEFAULT_SEED) -> List[Run]:
    templates = _load("filler.json")["runs"]
    rng = random.Random(seed)
    order: List[int] = []
    while len(order) < count:
        cycle = list(range(len(templates)))
        rng.shuffle(cycle)
        order += cycle
    return [_to_run(templates[t], planted=False, copy=i + 1) for i, t in enumerate(order[:count])]


def build_journal(length: int, seed: int = DEFAULT_SEED) -> List[Run]:
    """Early runs, filler, the middle runs at the halfway point, filler, then the late runs."""
    planted = planted_runs()
    fixed = sum(len(runs) for runs in planted.values())
    if length < fixed:
        raise ValueError(f"A journal needs at least {fixed} runs to hold the planted ones")
    filler = filler_runs(length - fixed, seed)
    middle_at = length // 2 - len(planted["early"])
    before, after = filler[:max(middle_at, 0)], filler[max(middle_at, 0):]
    return [*planted["early"], *before, *planted["middle"], *after, *planted["late"]]


def load_questions() -> List[Question]:
    return [
        Question(q["id"], q["category"], q["source"], q["question"], tuple(tuple(group) for group in q["expect"]))
        for q in _load("questions.json")["questions"]
    ]


def journal_text(runs: Sequence[Run]) -> str:
    """Every word in a journal, for the dataset guards."""
    return json.dumps([[r.request, [(c.arguments, c.result) for c in r.calls], r.response] for r in runs])


__all__ = [
    "DATA_DIR", "DEFAULT_SEED", "JOURNAL_LENGTHS", "Question", "Run", "ToolCall",
    "agent_name", "build_journal", "filler_runs", "journal_text", "load_questions", "planted_runs",
]
