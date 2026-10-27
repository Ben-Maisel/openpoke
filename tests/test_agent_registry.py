"""Tests for the agent registry: stable IDs, cards, and facts (P1)."""

from datetime import datetime, timedelta, timezone

import pytest

from server.services.execution.registry import AgentRegistry


@pytest.fixture
def clock():
    state = {"now": datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)}

    def advance(minutes=1):
        state["now"] += timedelta(minutes=minutes)

    state["advance"] = advance
    return state


@pytest.fixture
def registry(tmp_path, clock):
    return AgentRegistry(tmp_path / "registry.db", now=lambda: clock["now"])


# --- Identity -----------------------------------------------------------------

def test_ids_are_short_sequential_and_stable(registry):
    first = registry.create("Lunch with Alice")
    second = registry.create("Vercel Job Offer")
    assert (first.id, second.id) == ("a1", "a2")
    assert registry.get("a1") == first


def test_ids_are_never_reused_even_after_delete_or_clear(registry):
    registry.create("one")
    registry.create("two")
    registry.delete("a2")
    assert registry.create("three").id == "a3"
    registry.clear()
    assert registry.create("four").id == "a4"


def test_the_same_name_can_belong_to_different_agents(registry):
    # Names are labels, not identities: two agents may share one.
    first = registry.create("Email to Alice")
    second = registry.create("Email to Alice")
    assert first.id != second.id


def test_ids_survive_a_restart(tmp_path, clock):
    path = tmp_path / "registry.db"
    AgentRegistry(path).create("Lunch with Alice")
    reopened = AgentRegistry(path)
    assert reopened.get("a1").name == "Lunch with Alice"
    assert reopened.create("next").id == "a2"


@pytest.mark.parametrize("bad", ["", "a", "17", "b17", "a1x", "a-1", None])
def test_malformed_ids_find_nothing(registry, bad):
    registry.create("Lunch with Alice")
    assert registry.get(bad) is None
    assert registry.touch(bad) is False


def test_ids_are_matched_case_insensitively(registry):
    registry.create("Lunch with Alice")
    assert registry.get(" A1 ").id == "a1"


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_names_are_rejected(registry, name):
    with pytest.raises(ValueError):
        registry.create(name)


# --- Cards: summary, usage, status ---------------------------------------------

def test_new_agent_starts_with_its_summary_and_no_runs(registry):
    agent = registry.create("  Lunch with Alice  ", summary="  Invite Alice to lunch Friday  ")
    assert (agent.name, agent.summary, agent.status, agent.run_count) == (
        "Lunch with Alice", "Invite Alice to lunch Friday", "active", 0,
    )
    assert agent.created_at == agent.last_used_at == "2026-10-26T12:00:00Z"


def test_touch_records_a_run(registry, clock):
    registry.create("Lunch with Alice")
    clock["advance"](30)
    assert registry.touch("a1") is True
    agent = registry.get("a1")
    assert (agent.run_count, agent.last_used_at) == (1, "2026-10-26T12:30:00Z")


def test_summary_can_be_replaced(registry):
    registry.create("Lunch with Alice", summary="first")
    registry.update_summary("a1", "Invite sent; awaiting Alice's reply")
    assert registry.get("a1").summary == "Invite sent; awaiting Alice's reply"


def test_status_is_validated(registry):
    registry.create("Lunch with Alice")
    assert registry.set_status("a1", "dormant") is True
    assert registry.get("a1").status == "dormant"
    with pytest.raises(ValueError):
        registry.set_status("a1", "deleted")


def test_updates_to_unknown_agents_report_false(registry):
    assert registry.update_summary("a99", "x") is False
    assert registry.set_status("a99", "archived") is False
    assert registry.delete("a99") is False


# --- Listing and lookup ---------------------------------------------------------

def test_list_is_most_recently_used_first(registry, clock):
    for name in ("old", "middle", "new"):
        registry.create(name)
        clock["advance"]()
    registry.touch("a1")  # "old" was just used again
    assert [a.name for a in registry.list_agents()] == ["old", "new", "middle"]


def test_list_can_filter_by_status(registry):
    registry.create("keep")
    registry.create("archive me")
    registry.set_status("a2", "archived")
    assert [a.id for a in registry.list_agents(statuses=["active"])] == ["a1"]
    assert [a.id for a in registry.list_agents(statuses=["active", "archived"])] == ["a2", "a1"]


def test_find_by_name_is_exact_and_prefers_the_most_recently_used(registry, clock):
    registry.create("Email to Alice")
    clock["advance"]()
    registry.create("Email to Alice")
    clock["advance"]()
    registry.touch("a1")
    assert registry.find_by_name("Email to Alice").id == "a1"
    assert registry.find_by_name("email to alice") is None


# --- Facts ----------------------------------------------------------------------

def test_facts_are_grouped_deduplicated_and_trimmed(registry):
    registry.create("Lunch with Alice")
    assert registry.add_facts("a1", "email", ["alice.wong@x.com", " alice.wong@x.com ", "", "bob@x.com"]) == 2
    assert registry.add_facts("a1", "email", ["bob@x.com"]) == 0
    registry.add_facts("a1", "draft", ["r-1"])
    facts = registry.facts("a1")
    assert facts["email"] == ["alice.wong@x.com", "bob@x.com"]
    assert facts["draft"] == ["r-1"]
    assert facts["trigger"] == []


def test_fact_kinds_are_validated(registry):
    registry.create("Lunch with Alice")
    with pytest.raises(ValueError):
        registry.add_facts("a1", "phone", ["555"])


def test_facts_can_be_removed(registry):
    registry.create("Lunch with Alice")
    registry.add_facts("a1", "draft", ["r-1", "r-2"])
    assert registry.remove_fact("a1", "draft", "r-1") is True
    assert registry.remove_fact("a1", "draft", "r-1") is False
    assert registry.facts("a1")["draft"] == ["r-2"]


def test_facts_for_unknown_agents_are_ignored(registry):
    assert registry.add_facts("a99", "email", ["x@y.com"]) == 0
    assert registry.facts("a99")["email"] == []


def test_deleting_an_agent_deletes_its_facts(registry):
    registry.create("Lunch with Alice")
    registry.add_facts("a1", "email", ["alice@x.com"])
    registry.delete("a1")
    registry.create("next")  # a2: must not inherit a1's facts
    assert registry.facts("a1")["email"] == [] and registry.facts("a2")["email"] == []
