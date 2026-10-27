"""Tests for agent search and the working set (P2)."""

from datetime import datetime, timedelta, timezone

import pytest

from server.services.execution.registry import AgentRecord, AgentRegistry
from server.services.execution.search import search_agents, select_working_set


def agent(n, name, summary="", minutes_ago=0):
    used = (datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return AgentRecord(id=f"a{n}", name=name, summary=summary, status="active",
                       created_at=used, last_used_at=used, run_count=1)


def ids(results):
    return [r.agent.id for r in results]


# --- search_agents ---------------------------------------------------------------

def test_name_words_find_the_agent():
    agents = [agent(1, "Vercel Job Offer"), agent(2, "Lunch with Alice Wong"), agent(3, "Dentist Reminder")]
    assert ids(search_agents("any update on the vercel offer?", agents)) == ["a1"]


def test_summary_finds_an_agent_whose_name_is_vague():
    agents = [agent(1, "Reminder Setter", "Monthly Pay rent reminder, trigger 1 active"), agent(2, "Email to Bob")]
    assert ids(search_agents("move my rent reminder to the 2nd", agents)) == ["a1"]


def test_card_facts_find_the_agent():
    agents = [agent(1, "Lunch plans"), agent(2, "Q3 numbers")]
    facts = {"a1": {"email": ["alice.wong@acme.com"], "person": ["Alice Wong"]}}
    assert ids(search_agents("did alice wong reply?", agents, facts)) == ["a1"]


def test_the_card_tells_lookalikes_apart():
    # The messy-roster problem: three look-alike names, only the card says which one holds the context.
    agents = [
        agent(1, "Lunch with Alice Wong", "Coordinating Friday lunch; invite sent to Alice Wong, awaiting her reply"),
        agent(2, "Alice Wong Lunch", "Looked up Alice Wong's phone number; done"),
        agent(3, "Friday Lunch with Alice", "Could not find a calendar slot; abandoned"),
    ]
    assert ids(search_agents("did alice wong reply to the lunch invite?", agents))[0] == "a1"


def test_distinctive_words_outweigh_common_ones():
    # "alice" is on every agent, so the rarer "nopa" decides.
    agents = [agent(i, f"Alice task {i}") for i in range(1, 6)] + [agent(6, "Nopa dinner with Alice")]
    assert ids(search_agents("alice: is the nopa booking confirmed?", agents))[0] == "a6"


def test_a_quoted_agent_id_wins():
    agents = [agent(1, "Lunch with Alice"), agent(17, "Vercel Job Offer")]
    assert ids(search_agents("[SUCCESS] Vercel Job Offer (a17): sent", agents))[0] == "a17"
    assert ids(search_agents("re a1", agents))[0] == "a1"


@pytest.mark.parametrize("query", ["", "   ", "ok thanks!", "send it"])
def test_queries_without_meaningful_words_match_nothing(query):
    assert search_agents(query, [agent(1, "Lunch with Alice"), agent(2, "Send Reminder")]) == []


def test_nonmatching_agents_are_left_out_and_limit_applies():
    agents = [agent(i, f"Denver trip {i}") for i in range(1, 9)] + [agent(9, "Lunch with Alice")]
    results = search_agents("denver trip", agents, limit=3)
    assert len(results) == 3 and "a9" not in ids(results)


def test_ties_go_to_the_most_recently_used():
    agents = [agent(1, "Denver Flights", minutes_ago=60), agent(2, "Denver Flights", minutes_ago=5)]
    assert ids(search_agents("denver flights", agents)) == ["a2", "a1"]


# --- select_working_set ------------------------------------------------------------

def test_working_set_is_hot_cache_then_matches_without_repeats():
    agents = [agent(i, f"Errand {i}", minutes_ago=i) for i in range(1, 11)]  # a1 most recent ... a10 oldest
    agents.append(agent(11, "Vercel Job Offer", minutes_ago=500))
    working = select_working_set("vercel offer update", agents, hot_size=3, shortlist_size=2)
    assert [a.id for a in working.hot] == ["a1", "a2", "a3"]
    assert ids(working.matches) == ["a11"]  # the only match; non-matching errands stay out
    assert working.total_agents == len(agents)


def test_a_hot_agent_is_not_repeated_in_the_matches():
    agents = [agent(1, "Vercel Job Offer", minutes_ago=1), agent(2, "Vercel Offer Follow-up", minutes_ago=600)]
    working = select_working_set("vercel offer", agents, hot_size=1, shortlist_size=5)
    assert [a.id for a in working.hot] == ["a1"]
    assert ids(working.matches) == ["a2"]
    assert [a.id for a in working.agents] == ["a1", "a2"]


def test_working_set_size_is_bounded_no_matter_how_many_agents_exist():
    agents = [agent(i, f"Alice errand {i}", minutes_ago=i) for i in range(1, 501)]
    working = select_working_set("alice errand", agents)
    assert len(working.agents) == 10


def test_empty_registry_gives_an_empty_working_set():
    working = select_working_set("anything", [])
    assert (working.hot, working.matches, working.total_agents) == ([], [], 0)


# --- registry.all_facts ------------------------------------------------------------

def test_all_facts_returns_every_agents_facts_in_one_call(tmp_path):
    registry = AgentRegistry(tmp_path / "registry.db")
    first, second, _ = registry.create("one"), registry.create("two"), registry.create("three")
    registry.add_facts(first.id, "email", ["a@x.com"])
    registry.add_facts(second.id, "draft", ["r-1"])
    facts = registry.all_facts()
    assert set(facts) == {first.id, second.id}  # agents without facts are omitted
    assert facts[first.id]["email"] == ["a@x.com"] and facts[second.id]["draft"] == ["r-1"]
    assert facts[first.id]["trigger"] == []
