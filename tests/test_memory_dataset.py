"""Guards on the long-lived worker eval dataset: answers exist, filler never leaks one, journals are reproducible."""

import json
from collections import Counter

import pytest

from evals.memory.dataset import (
    JOURNAL_LENGTHS, build_journal, filler_runs, journal_text, load_questions, planted_runs,
)
from server.agents.execution_agent.memory import digest_tool_call

QUESTIONS = load_questions()
PLANTED = [run for runs in planted_runs().values() for run in runs]
CATEGORIES = {"old_detail", "preference", "changed", "open_item", "recent_detail"}


def contains(text, alternative):
    return alternative.lower() in text.lower()


def test_question_mix():
    assert len(QUESTIONS) == 20
    assert len({q.id for q in QUESTIONS}) == 20
    assert set(Counter(q.category for q in QUESTIONS)) == CATEGORIES


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda q: q.id)
def test_every_answer_is_somewhere_in_the_planted_runs(question):
    text = journal_text(PLANTED)
    for group in question.expect:
        assert any(contains(text, alt) for alt in group), (question.id, group)


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda q: q.id)
def test_filler_never_contains_an_answer(question):
    text = journal_text(filler_runs(200))
    for group in question.expect:
        leaked = [alt for alt in group if contains(text, alt)]
        assert not leaked, (question.id, leaked)


@pytest.mark.parametrize("question", [q for q in QUESTIONS if "email, deep" in q.source], ids=lambda q: q.id)
def test_deep_email_answers_are_beyond_every_digest(question):
    # Past the digest's snippet and absent from every other planted digest: only recall reaches them.
    digests = "\n".join(
        digest_tool_call({"ref": "r1", "tool": c.tool, "ok": c.ok, "arguments": c.arguments, "result": c.result})
        for run in PLANTED for c in run.calls
    )
    for group in question.expect:
        visible = [alt for alt in group if contains(digests, alt)]
        assert not visible, (question.id, visible)


def test_draft_bodies_are_shown_whole_in_digests():
    # The exception to "deep": a draft body is shown in full, so the expense total is visible without recall.
    (late_draft,) = [c for run in planted_runs()["late"] for c in run.calls if c.tool == "gmail_create_draft"]
    digest = digest_tool_call({"ref": "r1", "tool": late_draft.tool, "ok": True,
                               "arguments": late_draft.arguments, "result": late_draft.result})
    assert "$1,184.60" in digest


def test_the_superseded_hotel_details_differ_from_the_current_ones():
    early = journal_text(planted_runs()["early"])
    assert "Marriott" in early and "MR-4471" in early and "219" in early
    assert "Hyatt" not in early and "HY-5528" not in early


# --- Journals ----------------------------------------------------------------------

@pytest.mark.parametrize("length", JOURNAL_LENGTHS)
def test_journal_has_the_planted_runs_in_place(length):
    journal = build_journal(length)
    planted = planted_runs()
    assert len(journal) == length
    early, late = len(planted["early"]), len(planted["late"])
    assert journal[:early] == planted["early"]
    assert journal[-late:] == planted["late"]
    (middle,) = planted["middle"]
    assert journal.index(middle) == length // 2
    assert sum(r.planted for r in journal) == len(PLANTED)


def test_journals_are_reproducible_and_seed_dependent():
    assert build_journal(60) == build_journal(60)
    assert build_journal(60, seed=1) != build_journal(60, seed=2)


def test_filler_copies_get_their_own_email_ids():
    ids = [e["id"] for run in filler_runs(200) for c in run.calls if c.tool == "task_email_search" for e in c.result]
    assert len(ids) == len(set(ids))


def test_a_journal_too_short_for_the_story_is_rejected():
    with pytest.raises(ValueError):
        build_journal(len(PLANTED) - 1)


def test_every_planted_tool_result_is_plain_json():
    for run in PLANTED:
        for call in run.calls:
            json.dumps(call.result)
