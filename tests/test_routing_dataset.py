"""Guards on the routing eval dataset: labels are consistent and rosters are fair and reproducible."""

from collections import Counter

import pytest

from evals.routing.dataset import ROSTER_SIZES, build_roster, distractor_pool, load_cases, mentions

CASES = load_cases()
CATEGORIES = {"explicit_followup", "implicit_followup", "disambiguation", "new_task", "no_delegation"}
HISTORY_TAGS = {"user_message", "poke_reply", "agent_message"}


# --- The labelled cases ---------------------------------------------------------

def test_case_mix():
    assert len(CASES) == 40
    assert Counter(c.category for c in CASES) == {
        "explicit_followup": 10, "implicit_followup": 8, "disambiguation": 6, "new_task": 10, "no_delegation": 6,
    }


def test_case_ids_are_unique():
    assert len({c.id for c in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_case_is_internally_consistent(case):
    assert case.category in CATEGORIES
    assert case.message.strip()
    # A correct answer must actually be in every roster the case runs against.
    assert set(case.expect.agents) <= set(case.required_agents)
    # The expectation matches the category.
    expected_kind = {"new_task": "new", "no_delegation": "none"}.get(case.category, "agent")
    assert case.expect.kind == expected_kind
    # Anything that could have a competing answer declares terms to keep out of the padding.
    if case.expect.kind != "none":
        assert case.exclude, "cases that delegate must exclude their topic's terms from distractors"
    assert all(tag in HISTORY_TAGS for tag, _ in case.history)
    if case.category == "implicit_followup":
        assert case.history, "implicit follow-ups only make sense with history"
    if case.category == "disambiguation":
        assert len(case.required_agents) >= 2, "disambiguation needs a confuser in the roster"


# --- The distractor pool ----------------------------------------------------------

def test_pool_is_large_unique_and_sorted():
    pool = distractor_pool()
    assert len(pool) >= 700
    assert len(set(pool)) == len(pool)
    assert pool == sorted(pool)


@pytest.mark.parametrize(
    "name, terms, expected",
    [
        ("Car Service Reminder", ["car"], True),
        ("Call with Ben Carter", ["car"], True),       # over-matching is fine: it only shrinks the pool
        ("Email to Ben", ["ben"], True),
        ("Trip to Denver", ["DENVER"], True),
        ("Scarf Order", ["car"], False),               # not at a word start
        ("Lunch with Alice", ["bob"], False),
        ("Lunch with Alice", [], False),
    ],
)
def test_mentions(name, terms, expected):
    assert mentions(name, terms) is expected


# --- Rosters ------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_rosters_are_fair_at_every_size(case):
    for size in ROSTER_SIZES:
        roster = build_roster(case, size)
        assert len(roster) == size
        assert len(set(roster)) == size, "no duplicate agent names"
        for agent in case.required_agents:
            assert roster.count(agent) == 1
        distractors = [n for n in roster if n not in case.required_agents]
        leaked = [n for n in distractors if mentions(n, case.exclude)]
        assert not leaked, f"padding contains a competing answer: {leaked}"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_rosters_are_nested_so_only_noise_grows(case):
    rosters = [set(build_roster(case, size)) for size in ROSTER_SIZES]
    for smaller, larger in zip(rosters, rosters[1:]):
        assert smaller <= larger


def test_rosters_are_reproducible_and_seed_dependent():
    case = CASES[0]
    assert build_roster(case, 200) == build_roster(case, 200)
    assert build_roster(case, 200, seed=1) != build_roster(case, 200, seed=2)


def test_correct_agent_positions_are_spread_out():
    # The right answer must not always sit at the start or end of the list.
    fractions = []
    for case in CASES:
        roster = build_roster(case, 500)
        fractions += [roster.index(agent) / (len(roster) - 1) for agent in case.expect.agents]
    assert min(fractions) < 0.25 and max(fractions) > 0.75
    assert sum(0.25 <= f <= 0.75 for f in fractions) >= len(fractions) // 3


def test_impossible_roster_size_is_rejected():
    with pytest.raises(ValueError):
        build_roster(CASES[0], 10_000)
