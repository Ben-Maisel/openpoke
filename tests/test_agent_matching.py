"""Tests for name matching and duplicate detection (P4)."""

import json
from pathlib import Path

import pytest

from server.services.execution.matching import (
    find_possible_duplicates,
    meaningful_words,
    name_similarity,
)
from server.config import get_settings
from server.services.execution.registry import AgentRecord

DUPLICATE_THRESHOLD = get_settings().duplicate_name_threshold

DECOYS = json.loads((Path(__file__).resolve().parents[1] / "evals" / "routing" / "data" / "decoys.json").read_text())["decoys"]
CASES = {c["id"]: c for c in json.loads(
    (Path(__file__).resolve().parents[1] / "evals" / "routing" / "data" / "cases.json").read_text())["cases"]}


def agent(id_, name, last_used="2026-10-26T12:00:00Z"):
    return AgentRecord(id=id_, name=name, summary="", status="active",
                       created_at=last_used, last_used_at=last_used, run_count=0)


def test_meaningful_words_drop_generic_task_words_and_possessives():
    assert meaningful_words("Email to Alice about the Q3 Report") == {"alice", "q3", "report"}
    assert meaningful_words("Mom's Birthday Gift") == {"mom", "birthday", "gift"}
    assert meaningful_words("Follow up reminder") == frozenset()


@pytest.mark.parametrize(
    "first, second",
    [
        ("Alice Wong Lunch", "Lunch with Alice Wong"),
        ("lunch with alice wong!", "Lunch with Alice Wong"),
        ("Friday Lunch with Alice", "Lunch with Alice Wong"),
        ("Q3 Report Follow-up", "Q3 Report with Priya"),
        ("Call Mom Reminder", "Mom Call Reminder"),
    ],
)
def test_near_duplicates_are_flagged(first, second):
    assert name_similarity(first, second) >= DUPLICATE_THRESHOLD


@pytest.mark.parametrize(
    "first, second",
    [
        ("Email to Alice", "Lunch with Alice"),           # same person, different task
        ("Denver Flights Search", "Lisbon Flights"),      # same kind of task, different trip
        ("Call Mom Reminder", "Mom's Birthday Gift"),
        ("Follow up", "Follow up"),                       # nothing meaningful to compare
    ],
)
def test_different_tasks_are_not_flagged(first, second):
    assert name_similarity(first, second) < DUPLICATE_THRESHOLD


@pytest.mark.parametrize(
    "first, second",
    [("Lunch with Alice Chen", "Lunch with Alice Wong"), ("Vercel Job Offer", "Stripe Job Offer")],
)
def test_lookalikes_that_differ_in_one_word_are_flagged_so_the_model_decides(first, second):
    # Known trade-off: a stricter rule would miss real duplicates. Because the check warns
    # rather than blocks, the model sees both agents and confirms it's a separate task.
    assert name_similarity(first, second) >= DUPLICATE_THRESHOLD


def test_most_eval_decoys_would_be_caught_on_creation():
    # The messy-roster decoys are exactly the duplicates this check exists to prevent.
    caught = total = 0
    for case_id, decoys in DECOYS.items():
        real = CASES[case_id]["expect"]["agents"][0]
        for decoy in decoys:
            total += 1
            caught += name_similarity(decoy, real) >= DUPLICATE_THRESHOLD
    assert caught / total >= 0.85, f"only {caught}/{total} decoys flagged"  # 42/48 when written


def test_candidates_are_ranked_by_similarity_then_recency():
    agents = [
        agent("a1", "Lunch with Alice Wong", "2026-10-01T00:00:00Z"),   # identical words: 1.0
        agent("a2", "Friday Lunch with Alice", "2026-10-20T00:00:00Z"),  # 2 of 3 words: lower, despite being newer
        agent("a3", "Vercel Job Offer"),
        agent("a4", "Alice Wong Lunch", "2026-10-25T00:00:00Z"),         # 1.0 and most recent
    ]
    ranked = [c.agent.id for c in find_possible_duplicates("Lunch with Alice Wong", agents)]
    assert ranked == ["a4", "a1", "a2"]


def test_no_candidates_for_a_genuinely_new_task():
    assert find_possible_duplicates("Denver Flights", [agent("a1", "Lunch with Alice Wong")]) == []
